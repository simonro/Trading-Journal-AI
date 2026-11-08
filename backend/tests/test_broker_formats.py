"""Tradovate, NinjaTrader, TopstepX and Webull exports.

    cd backend && python -m pytest tests -q

The futures files below are one synthetic /ES day (seven round trips) written in each
broker's layout: every format has to give the same trades, P&L and fees.
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import broker_formats  # noqa: E402
from broker_formats import SourceAccountsError, futures_ticker, parse_timestamp  # noqa: E402
from csv_parser import detect_broker, parse_broker_csv  # noqa: E402

# (time ET, side, qty, price) for a synthetic 5 Nov 2026 /ESZ26 session
ES_DAY = [
    ("09:46:56", "Buy", 1, "5810.00"), ("09:47:52", "Sell", 1, "5815.50"),
    ("09:49:21", "Buy", 2, "5812.25"), ("09:50:15", "Sell", 1, "5818.00"), ("09:50:37", "Sell", 1, "5809.75"),
    ("10:00:07", "Buy", 2, "5805.50"), ("12:01:33", "Sell", 2, "5798.25"),
    ("13:42:51", "Buy", 2, "5790.00"), ("14:04:25", "Sell", 2, "5794.75"),
    ("14:13:45", "Buy", 2, "5796.50"), ("14:21:30", "Sell", 2, "5793.00"),
    ("14:40:02", "Buy", 1, "5788.25"), ("14:40:02", "Buy", 1, "5788.25"), ("15:02:48", "Sell", 2, "5797.50"),
    ("15:07:09", "Sell", 2, "5801.00"), ("15:11:34", "Buy", 1, "5796.25"), ("15:19:24", "Buy", 1, "5799.50"),
]
EXPECTED_GROSS = [275.0, 162.5, -725.0, 475.0, -350.0, 925.0, 312.5]
FEE_PER_CONTRACT = 3.64


def gross_in_order(trades):
    return [t["gross_pnl"] for t in sorted(trades, key=lambda t: json.loads(t["executions"])[0]["time"])]


def tradovate_day(newest_first=True):
    rows = ["orderId,Account,Date,Fill Time,B/S,Contract,Product,Filled Qty,Avg Fill Price,Status"]
    for i, (t, side, q, px) in enumerate(ES_DAY):
        rows.append(f"{1000 + i},DEMO1,11/5/26,11/05/2026 {t}, {side},ESZ6,ES,{q},{px}, Filled")
    rows.append("1999,DEMO1,11/5/26,, Buy,ESZ6,ES,0,, Canceled")
    body = rows[1:]
    if newest_first:
        body = body[::-1]
    return "\n".join([rows[0]] + body) + "\n"


def ninja_day():
    rows = ["Instrument,Action,Quantity,Price,Time,ID,E/X,Commission,Account"]
    for i, (t, side, q, px) in enumerate(ES_DAY):
        rows.append(f"ES 12-26,{side},{q},{px},11/5/2026 {t},{i},Entry,${FEE_PER_CONTRACT * q:.2f},Sim101")
    return "\n".join(rows) + "\n"


def topstepx_day():
    rows = ["Id,AccountName,ContractName,EnteredAt,FilledAt,ExecutePrice,Fees,Side,Size,Status,PositionDisposition"]
    for i, (t, side, q, px) in enumerate(ES_DAY):
        h, m, s = map(int, t.split(":"))
        utc = f"11/05/2026 {h + 5:02d}:{m:02d}:{s:02d} +00:00"   # EST = UTC-5 on 5 Nov
        rows.append(f"{i},TSX-1,CON.F.US.EP.Z26,{utc},{utc},{px},{FEE_PER_CONTRACT * q:.2f},"
                    f"{'Bid' if side == 'Buy' else 'Ask'},{q},Filled,Opening")
    return "\n".join(rows) + "\n"


@pytest.mark.parametrize("make,key,fees", [
    (tradovate_day, "tradovate", False),
    (ninja_day, "ninjatrader", True),
    (topstepx_day, "topstepx", True),
])
def test_es_day_gives_the_same_trades_in_every_format(make, key, fees):
    content = make()
    assert detect_broker(content) == key
    warnings = []
    trades, skipped = parse_broker_csv(content, "auto", account_id=1, warnings=warnings)
    assert skipped == 0
    assert {t["ticker"] for t in trades} == {"/ESZ26"}
    assert {t["instrument_type"] for t in trades} == {"FUTURE"}
    assert gross_in_order(trades) == EXPECTED_GROSS
    assert sum(t["gross_pnl"] for t in trades) == sum(EXPECTED_GROSS)
    contracts = sum(q for _, _, q, _ in ES_DAY)
    assert round(sum(t["commissions"] for t in trades), 2) == (round(FEE_PER_CONTRACT * contracts, 2) if fees else 0)
    assert bool(warnings) is (not fees)


def test_tradovate_oldest_first_gives_the_same_trades():
    trades, _ = parse_broker_csv(tradovate_day(newest_first=False), "tradovate", account_id=1)
    assert gross_in_order(trades) == EXPECTED_GROSS


def test_file_timezone_is_converted_to_eastern():
    content = tradovate_day().replace("11/05/2026 09:46:56", "11/05/2026 08:46:56")
    trades, _ = parse_broker_csv(content, "tradovate", account_id=1, timezone="America/Chicago")
    first = min(json.loads(t["executions"])[0]["time"] for t in trades)
    assert first == "09:46:56"


def test_partly_filled_then_cancelled_order_still_counts():
    content = ("orderId,Account,Date,Fill Time,B/S,Contract,Product,Filled Qty,Avg Fill Price,Status\n"
               "1,A,11/5/26,11/05/2026 09:30:00,Buy,MNQZ6,MNQ,2,25000.00,Canceled\n"
               "2,A,11/5/26,11/05/2026 09:35:00,Sell,MNQZ6,MNQ,2,25010.00,Filled\n")
    trades, _ = parse_broker_csv(content, "tradovate", account_id=1)
    assert len(trades) == 1 and trades[0]["gross_pnl"] == 40.0   # 10 pts x 2 x $2


def test_reversal_fill_is_split_into_two_trades():
    content = ("orderId,Account,Date,Fill Time,B/S,Contract,Product,Filled Qty,Avg Fill Price,Status\n"
               "1,A,11/5/26,11/05/2026 09:30:00,Buy,MESZ6,MES,1,7400.00,Filled\n"
               "2,A,11/5/26,11/05/2026 09:40:00,Sell,MESZ6,MES,2,7410.00,Filled\n"
               "3,A,11/5/26,11/05/2026 09:50:00,Buy,MESZ6,MES,1,7405.00,Filled\n")
    trades, _ = parse_broker_csv(content, "tradovate", account_id=1)
    assert sorted(t["side"] for t in trades) == ["LONG", "SHORT"]
    assert sorted(t["gross_pnl"] for t in trades) == [25.0, 50.0]


def test_several_accounts_in_one_file_ask_which_one():
    two = ninja_day() + "ES 12-26,Buy,1,7400.00,11/5/2026 16:00:00,99,Entry,$3.64,Sim102\n" \
                        "ES 12-26,Sell,1,7401.00,11/5/2026 16:01:00,98,Exit,$3.64,Sim102\n"
    with pytest.raises(SourceAccountsError) as err:
        parse_broker_csv(two, "ninjatrader", account_id=1)
    assert err.value.accounts == ["Sim101", "Sim102"]
    trades, _ = parse_broker_csv(two, "ninjatrader", account_id=1, source_account="Sim102")
    assert [t["gross_pnl"] for t in trades] == [50.0]
    with pytest.raises(ValueError, match="not in this file"):
        parse_broker_csv(two, "ninjatrader", account_id=1, source_account="Nope")


def test_unknown_futures_root_fails_with_the_contract_named():
    content = ninja_day().replace("ES 12-26", "ZZ 12-26")
    with pytest.raises(ValueError, match="no known point value for /ZZZ26"):
        parse_broker_csv(content, "ninjatrader", account_id=1)


def test_webull_combined_columns_zone_word_and_options():
    content = ("Name,Symbol,Side,Status,Filled/Total Qty,Price/Avg Price,Filled Time\n"
               "SPY call,SPY261120C00600000,Sell,Filled,2/2,0/3.10,11/05/2026 10:15:00 EST\n"
               "SPY call,SPY261120C00600000,Buy,Filled,2/2,0/2.50,11/05/2026 09:45:00 EST\n"
               "Apple,AAPL,Buy,Cancelled,0/10,0/0,\n"
               "Apple,AAPL,Buy,Filled,10/10,190/@190.00,11/05/2026 09:40:00 EST\n")
    # newest first except the last row: order comes from the times, not the file
    assert detect_broker(content) == "webull"
    trades, _ = parse_broker_csv(content, "auto", account_id=1)
    by = {t["instrument_type"]: t for t in trades}
    opt = by["OPTION"]
    assert (opt["ticker"], opt["option_expiry"], opt["option_strike"], opt["option_type"]) == \
        ("SPY", "2026-11-20", 600.0, "CALL")
    assert opt["gross_pnl"] == 120.0           # 0.60 x 2 x 100
    assert by["STOCK"]["ticker"] == "AAPL"


def test_explicit_broker_checks_its_own_columns():
    with pytest.raises(ValueError, match="does not have the columns of a Tradovate export"):
        parse_broker_csv(ninja_day(), "tradovate", account_id=1)


def test_generic_template_is_not_taken_by_a_broker_reader():
    generic = ("date,time,symbol,side,quantity,price,commission,asset_type\n"
               "2026-11-05,09:30:00,AAPL,BUY,10,190,0,STOCK\n")
    assert detect_broker(generic) == "generic"
    assert broker_formats.detect(generic) is None


@pytest.mark.parametrize("raw,year,expected", [
    ("ESZ5", 2025, "/ESZ25"), ("ESZ6", 2026, "/ESZ26"), ("ESH0", 2029, "/ESH30"),
    ("/ESU6", 2026, "/ESU26"), ("MNQZ25", 2025, "/MNQZ25"), ("ES 12-26", 2026, "/ESZ26"),
    ("6EZ5", 2025, "/6EZ25"), ("CON.F.US.EP.Z26", 2026, "/ESZ26"),
    ("CON.F.US.MNQ.H27", 2027, "/MNQH27"), ("AAPL", 2026, None),
])
def test_futures_ticker(raw, year, expected):
    assert futures_ticker(raw, year) == expected


def test_timestamps():
    et = lambda s, z="America/New_York": parse_timestamp(s, z).strftime("%Y-%m-%d %H:%M:%S")
    assert et("08/18/2026 09:31:00 EST") == "2026-08-18 09:31:00"     # Webull's year-round EST
    assert et("2026-11-05T14:46:56Z") == "2026-11-05 09:46:56"
    assert et("11/05/2026 14:46:56 +00:00") == "2026-11-05 09:46:56"
    assert et("11/5/2026 9:46:56 AM", "America/Chicago") == "2026-11-05 10:46:56"
    assert et("9/15/2026 9:39") == "2026-09-15 09:39:00"
    assert parse_timestamp("not a time", "UTC") is None


# ── Through the API, against a throwaway database ────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "journal.db"
    monkeypatch.setenv("DATABASE_PATH", str(db))
    for name in ("database", "main"):
        sys.modules.pop(name, None)
    import database
    database.DB_PATH = str(db)
    import main
    monkeypatch.setattr(main, "_classify_dates", lambda conn, dates: 0, raising=False)
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        c.db = str(db)
        conn = sqlite3.connect(c.db)
        if not conn.execute("SELECT 1 FROM accounts WHERE id=1").fetchone():
            conn.execute("INSERT INTO accounts (id, name, type) VALUES (1, 'Futures', 'day_trading')")
            conn.commit()
        yield c


def post(client, content, **form):
    return client.post("/api/import-csv", data={"account_id": "1", **form},
                       files={"file": ("export.csv", content.encode(), "text/csv")})


def stored(client):
    conn = sqlite3.connect(client.db)
    return {g: (pnl, n) for g, pnl, n in conn.execute(
        "SELECT trade_group, gross_pnl, json_array_length(executions) FROM trades ORDER BY trade_group")}


def test_reimport_is_a_no_op_and_identical_fills_are_counted(client):
    r = post(client, tradovate_day(), broker="tradovate")
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 7 and r.json()["warnings"]
    before = stored(client)
    assert len(before) == 7
    again = post(client, tradovate_day(), broker="tradovate").json()
    assert again["imported"] == 0 and again["skipped"] == 17
    assert stored(client) == before
    # the 14:40:02 trip has two identical 1-lot buys; both stay
    assert sorted(n for _, n in before.values()).count(3) == 3


def test_timezone_is_remembered_per_account(client):
    assert post(client, tradovate_day(), broker="tradovate", timezone="America/Chicago").status_code == 200
    conn = sqlite3.connect(client.db)
    assert conn.execute("SELECT import_timezone FROM accounts WHERE id=1").fetchone()[0] == "America/Chicago"
    # a later import without a zone uses the saved one, so nothing doubles
    again = post(client, tradovate_day(), broker="tradovate").json()
    assert again["imported"] == 0
    assert post(client, tradovate_day(), broker="tradovate", timezone="Mars/Base").status_code == 400


def test_several_accounts_answer_409_with_the_list(client):
    two = ninja_day().replace("Sim101\n", "Sim101\n", 1) + \
        "ES 12-26,Buy,1,7400.00,11/5/2026 16:00:00,99,Entry,$3.64,Sim102\n" \
        "ES 12-26,Sell,1,7401.00,11/5/2026 16:01:00,98,Exit,$3.64,Sim102\n"
    r = post(client, two, broker="auto")
    assert r.status_code == 409
    assert r.json()["source_accounts"] == ["Sim101", "Sim102"]
    r = post(client, two, broker="auto", source_account="Sim102")
    assert r.status_code == 200 and r.json()["imported"] == 1


def test_second_options_import_adds_instead_of_overwriting(client):
    head = "Name,Symbol,Side,Status,Filled,Avg Price,Filled Time\n"
    trip1 = ("c,SPY261120C00600000,Buy,Filled,1,2.50,11/05/2026 09:45:00 EST\n"
             "c,SPY261120C00600000,Sell,Filled,1,3.00,11/05/2026 10:00:00 EST\n")
    trip2 = ("c,SPY261120C00600000,Buy,Filled,1,2.00,11/05/2026 13:00:00 EST\n"
             "c,SPY261120C00600000,Sell,Filled,1,2.20,11/05/2026 13:30:00 EST\n")
    assert post(client, head + trip1, broker="webull").status_code == 200
    assert post(client, head + trip1 + trip2, broker="webull").status_code == 200
    trades = stored(client)
    assert len(trades) == 2
    assert sorted(p for p, _ in trades.values()) == [20.0, 50.0]


def test_one_stored_copy_hides_only_one_identical_new_fill(client):
    head = "orderId,Account,Date,Fill Time,B/S,Contract,Product,Filled Qty,Avg Fill Price,Status\n"
    buy = "1,A,11/5/26,11/05/2026 09:30:00,Buy,MESZ6,MES,1,7400.00,Filled\n"
    sell = "2,A,11/5/26,11/05/2026 09:31:00,Sell,MESZ6,MES,1,7401.00,Filled\n"
    assert post(client, head + buy + sell, broker="tradovate").status_code == 200
    # a fuller export of the same minute: the same 1-lot trip was made twice
    r = post(client, head + buy + buy + sell + sell, broker="tradovate").json()
    assert r["skipped"] == 2
    fills = sum(n for _, n in stored(client).values())
    assert fills == 4


# ── Shapes from real exports found on GitHub (headers copied, rows our own) ───

TOPSTEPX_REAL_HEAD = ("﻿Id,AccountName,ContractName,Status,Type,Size,Side,CreatedAt,TradeDay,FilledAt,"
                      "CancelledAt,StopPrice,LimitPrice,ExecutePrice,PositionDisposition,CreationDisposition,"
                      "RejectionReason,ExchangeOrderId,PlatformOrderId\n")


def test_topstepx_real_layout_skips_cancelled_orders_and_reads_offsets():
    content = TOPSTEPX_REAL_HEAD + (
        "1,PRAC-1,MCLK6,Filled,Market,1,Bid,03/30/2026 08:41:49 +01:00,03/30/2026 00:00:00 -05:00,"
        "03/30/2026 08:41:49 +01:00,,,,101.790000000,Opening,Trader,,,\n"
        "2,PRAC-1,MGCM6,Cancelled,Limit,1,Bid,03/30/2026 08:41:49 +01:00,03/30/2026 00:00:00 -05:00,,"
        "03/30/2026 08:50:00 +01:00,,4672.6,,Undetermined,Trader,Cancelled by trader,,\n"
        "3,PRAC-1,MCLK6,Filled,Stop,1,Ask,03/30/2026 08:41:49 +01:00,03/30/2026 00:00:00 -05:00,"
        "03/30/2026 08:54:39 +01:00,,101.64,,101.660000000,Closing,Trader,,,\n")
    trades, _ = parse_broker_csv(content, "auto", account_id=1)
    assert len(trades) == 1
    t = trades[0]
    assert t["ticker"] == "/MCLK26" and t["gross_pnl"] == -13.0          # 0.13 x $100
    assert json.loads(t["executions"])[0]["time"] == "03:41:49"          # 08:41:49 +01:00 = 03:41:49 ET


def test_webull_crypto_rows_are_left_out_with_a_note():
    content = ("Name,Symbol,Side,Status,Filled,Total Qty,Price,Avg Price,Time-in-Force,Placed Time,Filled Time\n"
               "Shiba Inu,SHIBUSD,Buy,Filled,721864.95,721864.95,@0.000,0.000,DAY,10/06/2021 21:06:34 EDT,10/06/2021 21:28:05 EDT\n"
               "Camber,CEI,Buy,Filled,100,100,@1.00,1.00,DAY,10/15/2021 10:00:00 EDT,10/15/2021 10:00:01 EDT\n"
               "Camber,CEI,Sell,Filled,100,100,1.35,1.35,DAY,10/15/2021 15:32:39 EDT,10/15/2021 15:32:39 EDT\n")
    warnings = []
    trades, _ = parse_broker_csv(content, "webull", account_id=1, warnings=warnings)
    assert [t["ticker"] for t in trades] == ["CEI"] and trades[0]["gross_pnl"] == 35.0
    assert any("SHIBUSD" in w for w in warnings)


def test_day_first_dates_are_read_day_first():
    content = ("Instrument,Action,Quantity,Price,Time,ID,E/X,Commission,Account\n"
               "MES 12-26,Buy,1,7400,13/11/2026 09:30:00,1,Entry,$0.62,Sim101\n"
               "MES 12-26,Sell,1,7402,05/11/2026 09:35:00,2,Exit,$0.62,Sim101\n")
    warnings = []
    trades, _ = parse_broker_csv(content, "ninjatrader", account_id=1, warnings=warnings)
    dates = sorted(e["date"] for t in trades for e in json.loads(t["executions"]))
    assert dates == ["2026-11-05", "2026-11-13"]
    assert any("day-first" in w for w in warnings)


def test_reversal_across_two_imports_splits_at_the_stored_position(client):
    head = "orderId,Account,Date,Fill Time,B/S,Contract,Product,Filled Qty,Avg Fill Price,Status\n"
    assert post(client, head + "1,A,11/5/26,11/05/2026 09:30:00,Buy,MESZ6,MES,1,7400.00,Filled\n",
                broker="tradovate").status_code == 200
    r = post(client, head + "2,A,11/6/26,11/06/2026 09:40:00,Sell,MESZ6,MES,2,7410.00,Filled\n"
                            "3,A,11/6/26,11/06/2026 09:50:00,Buy,MESZ6,MES,1,7405.00,Filled\n",
             broker="tradovate")
    assert r.status_code == 200, r.text
    conn = sqlite3.connect(client.db)
    rows = sorted(conn.execute("SELECT side, gross_pnl FROM trades").fetchall())
    assert rows == [("LONG", 50.0), ("SHORT", 25.0)]


def test_regrouping_keeps_the_point_value_a_trade_was_booked_with(client):
    head = "date,time,symbol,side,quantity,price,commission,asset_type,multiplier\n"
    first = "2026-11-05,09:30:00,/ZCZ26,BUY,1,4.50,0,FUTURE,5000\n2026-11-05,09:40:00,/ZCZ26,SELL,1,4.60,0,FUTURE,5000\n"
    second = "2026-11-05,10:30:00,/ZCZ26,BUY,1,4.60,0,FUTURE,5000\n2026-11-05,10:40:00,/ZCZ26,SELL,1,4.65,0,FUTURE,5000\n"
    assert post(client, head + first, broker="generic").status_code == 200
    assert post(client, head + first + second, broker="generic").status_code == 200
    conn = sqlite3.connect(client.db)
    assert sorted(p for (p,) in conn.execute("SELECT gross_pnl FROM trades")) == [250.0, 500.0]


def test_zone_is_saved_only_for_exports_that_use_it(client):
    tos = ("Account Statement\n\nCash Balance\nDATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n"
           '11/5/26,09:46:16,TRD,="1",BOT +10 TSLA @361.00,,,"-3,610.00","1"\n'
           '11/5/26,09:50:48,TRD,="2",SOLD -10 TSLA @362.00,,,"3,620.00","1"\n')
    assert post(client, tos, broker="auto", timezone="America/Chicago").status_code == 200
    conn = sqlite3.connect(client.db)
    assert conn.execute("SELECT import_timezone FROM accounts WHERE id=1").fetchone()[0] is None
    assert post(client, tradovate_day(), broker="tradovate", timezone="America/Chicago").status_code == 200
    r = post(client, tradovate_day(), broker="tradovate", timezone="America/New_York").json()
    assert any("changed from America/Chicago" in w for w in r["warnings"])


# ── tastytrade, TradeStation, Power E*TRADE ──────────────────────────────────

TASTY_HEAD = ("Date,Type,Sub Type,Action,Symbol,Instrument Type,Description,Value,Quantity,Average Price,"
              "Commissions,Fees,Multiplier,Root Symbol,Underlying Symbol,Expiration Date,Strike Price,"
              "Call or Put,Order #,Total,Currency\n")


def test_tastytrade_wheel_expiration_assignment_and_cash_rows():
    content = TASTY_HEAD + (
        # newest first, as tastytrade exports
        "2025-06-11T00:00:00+0000,Receive Deliver,Buy to Open,BUY_TO_OPEN,UNFI,Equity,Buy to Open 100 UNFI @ 29.00,"
        "\"-2,900.00\",100,-29.00,--,-5.00,,,,,,,,\"-2,905.00\",USD\n"
        "2025-06-11T00:00:00+0000,Receive Deliver,Assignment,,UNFI  250620P00029000,Equity Option,"
        "Removal of option due to assignment,0.00,1,0.00,--,0.00,100,UNFI,UNFI,6/20/25,29.0,PUT,,0.00,USD\n"
        "2025-06-06T20:00:00+0000,Receive Deliver,Expiration,BUY_TO_CLOSE,SPY   250606P00500000,Equity Option,"
        "Removal of 1.0 SPY 06/06/25 Put 500.00 due to expiration.,0.00,1,0.00,--,0.00,100,SPY,SPY,6/6/25,500.0,PUT,,0.00,USD\n"
        "2025-06-02T14:30:13+0000,Trade,Sell to Open,SELL_TO_OPEN,SPY   250606P00500000,Equity Option,"
        "Sold 1 SPY 06/06/25 Put 500.00 @ 1.11,111.00,1,111.00,-1.00,-0.14,100,SPY,SPY,6/6/25,500,PUT,1,109.86,USD\n"
        "2025-06-02T14:00:00+0000,Trade,Sell to Open,SELL_TO_OPEN,UNFI  250620P00029000,Equity Option,"
        "Sold 1 UNFI 06/20/25 Put 29.00 @ 2.75,275.00,1,275.00,-1.00,-0.14,100,UNFI,UNFI,6/20/25,29,PUT,2,273.86,USD\n"
        "2025-06-01T00:00:00+0000,Money Movement,Deposit,,,,ACH deposit,\"1,000.00\",0,,0.00,0.00,,,,,,,,\"1,000.00\",USD\n")
    assert detect_broker(content) == "tastytrade"
    warnings = []
    trades, _ = parse_broker_csv(content, "auto", account_id=1, warnings=warnings)
    by = {(t["ticker"], t["instrument_type"]): t for t in trades}
    spy = by[("SPY", "OPTION")]
    assert (spy["side"], spy["gross_pnl"], spy["commissions"]) == ("SHORT", 111.0, 1.14)
    unfi_put = by[("UNFI", "OPTION")]
    assert unfi_put["gross_pnl"] == 275.0 and unfi_put["option_strike"] == 29.0
    shares = by[("UNFI", "STOCK")]
    assert shares["side"] == "LONG" and shares["commissions"] == 5.0
    assert json.loads(shares["executions"])[0]["price"] == 29.0
    assert warnings == []


def test_tastytrade_transfer_is_skipped_and_orphan_close_is_named():
    content = TASTY_HEAD + (
        "2025-01-24T00:00:00+0000,Receive Deliver,Transfer,SELL_TO_OPEN,NRXP,Equity,Transferred 10.0 Short NRXP,"
        "0.00,10,0.00,--,0.00,,,,,,,,0.00,USD\n"
        "2025-01-03T16:33:47+0000,Trade,Sell to Close,SELL_TO_CLOSE,BP    250417C00029000,Equity Option,"
        "Sold 1 BP 04/17/25 Call 29.00 @ 1.00,100.00,1,100.00,0.00,-0.14,100,BP,BP,4/17/25,29,CALL,3,99.86,USD\n")
    warnings = []
    trades, _ = parse_broker_csv(content, "tastytrade", account_id=1, warnings=warnings)
    assert len(trades) == 1
    assert any("Transfer" in w for w in warnings)
    assert any("BP" in w and "before this file" in w for w in warnings)


def test_tradestation_stock_short_and_option_contracts():
    head = ("Account Number,Type,TradeInd,Transaction,Quantity,Cusip,ADP,Symbol,CallPut,UnderlyingSymbol,"
            "ExpireDate,StrikePrice,TD,SD,Activity Date,Price,Amount,CurrencyCode,Commission,Description,"
            "Activity Time,Order ID\n")
    content = head + (
        '12345678,Margin,T,Sell Short,200,="0",,FUSE  ,,,,,02/02/2026,02/03/2026,02/02/2026,2.36,472.00,USD,1.08,FUSE INC,02/02/2026 09:22:03:000,1\n'
        '12345678,Margin,T,Buy to Cover,200,="0",,FUSE  ,,,,,02/02/2026,02/03/2026,02/02/2026,2.41,482.00,USD,1.04,FUSE INC,02/02/2026 09:25:47:000,2\n'
        '12345678,Margin,T,Buy,200,="TSLA260220C420202602",,TSLA260220C420,CALL,TSLA  ,02/20/2026,420.0000,02/18/2026,02/19/2026,02/18/2026,6.35,1271.30,USD,1.30,CALL TSLA,02/18/2026 09:47:12:000,3\n'
        '12345678,Margin,T,Sell,200,="TSLA260220C420202602",,TSLA260220C420,CALL,TSLA  ,02/20/2026,420.0000,02/18/2026,02/19/2026,02/18/2026,7.9,1578.70,USD,1.30,CALL TSLA,02/18/2026 11:03:40:000,4\n')
    assert detect_broker(content) == "tradestation"
    trades, _ = parse_broker_csv(content, "auto", account_id=1)
    by = {t["instrument_type"]: t for t in trades}
    assert (by["STOCK"]["side"], by["STOCK"]["gross_pnl"], by["STOCK"]["commissions"]) == ("SHORT", -10.0, 2.12)
    opt = by["OPTION"]
    assert (opt["ticker"], opt["option_expiry"], opt["option_strike"], opt["option_type"]) == ("TSLA", "2026-02-20", 420.0, "CALL")
    assert opt["gross_pnl"] == 310.0                                    # 1.55 x 2 contracts x 100
    assert json.loads(opt["executions"])[0]["time"] == "09:47:12"


def test_etrade_title_line_options_from_description_and_no_fees():
    content = ('"Account 900000001 Orders, as of 02/02/26 at 04:00 PM EST"\n'
               '"Symbol","Status","Fill","Description","Market","Time","Account","ID"\n'
               '"IWM","Filled","3 @ 1.48","Sell 3 Feb-20-26 225 Puts @ 1.48 Limit to Open","SMART","02/18/2026, 10:12:05 AM","900000001","1"\n'
               '"IWM","Filled","3 @ 0.62","Buy 3 Feb-20-26 225 Puts @ Market to Close","SMART","02/19/2026, 10:30:22 AM","900000001","2"\n'
               '"AMD","Canceled","--","Buy 1 Feb-27-26 205 Calls @ 3.00 Limit to Open","SMART","02/19/2026, 1:10:00 PM","900000001","3"\n'
               '"FUSE","Filled","200 @ 2.36","Sell Short 200 FUSE @ Market","SMART","02/02/2026, 9:22:03 AM","900000001","4"\n'
               '"FUSE","Filled","200 @ 2.41","Buy to Cover 200 FUSE @ Market","SMART","02/02/2026, 9:25:47 AM","900000001","5"\n')
    assert detect_broker(content) == "etrade"
    warnings = []
    trades, _ = parse_broker_csv(content, "auto", account_id=1, warnings=warnings)
    by = {t["instrument_type"]: t for t in trades}
    assert len(trades) == 2
    assert (by["OPTION"]["side"], by["OPTION"]["gross_pnl"], by["OPTION"]["option_expiry"]) == ("SHORT", 258.0, "2026-02-20")
    assert (by["STOCK"]["side"], by["STOCK"]["gross_pnl"]) == ("SHORT", -10.0)
    assert any("no commissions" in w for w in warnings)


TS_HEAD = ("Account Number,Type,TradeInd,Transaction,Quantity,Cusip,ADP,Symbol,CallPut,UnderlyingSymbol,"
           "ExpireDate,StrikePrice,TD,SD,Activity Date,Price,Amount,CurrencyCode,Commission,Description,"
           "Activity Time,Order ID\n")


def test_tradestation_expired_rows_and_futures_are_named_not_dropped():
    content = TS_HEAD + (
        '1,Margin,T,Buy,200,="X",,TSLA260220C420,CALL,TSLA,02/20/2026,420,02/18/2026,02/19/2026,02/18/2026,6.35,1271.30,USD,1.30,CALL,02/18/2026 09:47:12:000,1\n'
        '1,Margin,T,Expired,200,="X",,TSLA260220C420,CALL,TSLA,02/20/2026,420,02/20/2026,02/20/2026,02/20/2026,0,0,USD,0,CALL,02/20/2026 16:00:00:000,2\n'
        '1,Margin,T,Buy,1,="0",,ESZ25,,,,,02/18/2026,02/19/2026,02/18/2026,6800,340000,USD,2.25,ES,02/18/2026 09:47:12:000,3\n')
    warnings = []
    trades, _ = parse_broker_csv(content, "tradestation", account_id=1, warnings=warnings)
    assert [t["instrument_type"] for t in trades] == ["OPTION"]
    assert any("TradeStation Expired" in w and "TradeStation futures" in w for w in warnings)


def test_tradestation_option_quantity_in_contracts_is_detected_from_amount():
    content = TS_HEAD + (
        '1,Margin,T,Buy,100,="X",,SPY260220P600,PUT,SPY,02/20/2026,600,02/18/2026,02/19/2026,02/18/2026,6.35,63500.00,USD,65,P,02/18/2026 09:47:12:000,1\n'
        '1,Margin,T,Sell,100,="X",,SPY260220P600,PUT,SPY,02/20/2026,600,02/18/2026,02/19/2026,02/18/2026,6.45,64500.00,USD,65,P,02/18/2026 10:47:12:000,2\n')
    trades, _ = parse_broker_csv(content, "tradestation", account_id=1)
    assert trades[0]["gross_pnl"] == 1000.0                         # 0.10 x 100 contracts x 100


def test_tastytrade_uses_exact_value_and_skips_corporate_actions():
    content = TASTY_HEAD + (
        "2025-03-10T12:30:04+0000,Receive Deliver,Special Dividend,SELL_TO_OPEN,YOU   250516C00035730,Equity Option,"
        "Special dividend: Open 1.0 YOU,350.00,1,350.00,--,0.00,100,YOU,YOU,5/16/25,35.73,CALL,,0.00,USD\n"
        "2025-03-03T15:00:00+0000,Trade,Sell to Close,SELL_TO_CLOSE,AES,Equity,Sold 100 AES @ 9.94,994.10,100,9.94,0.00,-0.10,1,,,,,,9,993.99,USD\n"
        "2025-03-03T14:00:00+0000,Trade,Buy to Open,BUY_TO_OPEN,AES,Equity,Bought 100 AES @ 9.90,-990.00,100,-9.90,0.00,0.00,1,,,,,,8,-990.00,USD\n")
    warnings = []
    trades, _ = parse_broker_csv(content, "tastytrade", account_id=1, warnings=warnings)
    assert len(trades) == 1 and trades[0]["gross_pnl"] == 4.10
    assert any("Special Dividend" in w for w in warnings)


def test_etrade_futures_skipped_and_option_close_without_open_is_named():
    content = ('"Symbol","Status","Fill","Description","Market","Time","Account","ID"\n'
               '"/ESZ25","Filled","1 @ 6800","Buy 1 /ESZ25 @ Market","SMART","02/18/2026, 10:12:05 AM","9","1"\n'
               '"IWM","Filled","3 @ 0.62","Sell 3 Feb-20-26 225 Puts @ Market to Close","SMART","02/19/2026, 10:30:22 AM","9","2"\n')
    warnings = []
    trades, _ = parse_broker_csv(content, "etrade", account_id=1, warnings=warnings)
    assert [t["instrument_type"] for t in trades] == ["OPTION"]
    assert any("Power E*TRADE futures" in w for w in warnings)
    assert any("IWM" in w and "before this file" in w for w in warnings)


def test_reimporting_a_file_with_a_flip_changes_nothing(client):
    """The flip (long 1, sell 2) is split from the position before the file, so the
    second import must not read the file's own stored fills as an open position."""
    f = ("orderId,Account,Date,Fill Time,B/S,Contract,Product,Filled Qty,Avg Fill Price,Status\n"
         "1,A,11/5/26,11/05/2026 09:30:00,Buy,MESZ6,MES,1,7400.00,Filled\n"
         "2,A,11/5/26,11/05/2026 09:40:00,Sell,MESZ6,MES,2,7410.00,Filled\n")
    first = post(client, f, broker="tradovate").json()
    conn = sqlite3.connect(client.db)
    before = conn.execute("SELECT trade_group, side, gross_pnl, executions FROM trades ORDER BY id").fetchall()
    second = post(client, f, broker="tradovate").json()
    after = conn.execute("SELECT trade_group, side, gross_pnl, executions FROM trades ORDER BY id").fetchall()
    assert first["imported"] == 2 and len(before) == 2
    assert second["imported"] == 0
    assert after == before
    assert not any("opened before this file" in w for w in second.get("warnings", []))
