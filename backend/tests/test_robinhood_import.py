"""The Robinhood importer against scripts/sample_import_robinhood.csv.

    cd backend && python -m pytest tests -q

Expected figures are worked out by hand from the sample rows (shown in each comment).
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from csv_parser import detect_broker, parse_broker_csv  # noqa: E402

SAMPLE = (BACKEND.parent / "scripts" / "sample_import_robinhood.csv").read_text(encoding="utf-8-sig")
HEADER = '"Activity Date","Process Date","Settle Date","Instrument","Description","Trans Code","Quantity","Price","Amount"\n'


@pytest.fixture(scope="module")
def rh():
    trades, skipped = parse_broker_csv(SAMPLE, "auto", account_id=1)
    assert skipped == 0
    return {t["trade_group"]: t for t in trades}


def test_detected_as_robinhood():
    assert detect_broker(SAMPLE) == "robinhood"


def test_other_broker_selected_is_refused():
    with pytest.raises(ValueError, match="Robinhood"):
        parse_broker_csv(SAMPLE, "thinkorswim", account_id=1)


def test_stock_round_trip_with_fee(rh):
    # BOT 10 @175.00 = 1,750.00; SOLD 10 @177.50 = 1,775.00 but 1,774.97 arrived
    # gross 25.00, fee 0.03, net 24.97
    t = rh["2026-09-21_AAPL_STOCK_1"]
    assert (t["side"], t["gross_pnl"], t["commissions"], t["net_pnl"]) == ("LONG", 25.0, 0.03, 24.97)
    assert len(json.loads(t["executions"])) == 2


def test_option_round_trip(rh):
    # BTO 2 @1.25 = 250.00 (paid 250.06); STC 2 @1.80 = 360.00 (got 359.94)
    # gross 110.00, fees 0.12, net 109.88
    t = rh["2026-09-23_SPY_OPTION_2026-09-25_450_CALL_1"]
    assert (t["side"], t["gross_pnl"], t["commissions"], t["net_pnl"]) == ("LONG", 110.0, 0.12, 109.88)
    assert (t["option_expiry"], t["option_strike"], t["option_type"]) == ("2026-09-25", 450.0, "CALL")


def test_expired_option_closes_at_zero(rh):
    # BTO 1 @2.10 = 210.00 (paid 210.03), expired worthless: net -210.03
    t = rh["2026-09-25_TSLA_OPTION_2026-09-25_240_PUT_1"]
    assert (t["gross_pnl"], t["net_pnl"]) == (-210.0, -210.03)
    assert [e["action"] for e in json.loads(t["executions"])] == ["BOT", "SOLD"]


def test_fractional_open_position_and_non_trades_ignored(rh):
    # the 0.5 MSFT buy is still open; the dividend and ACH deposit are not trades
    assert rh["2026-09-24_MSFT_STOCK_1"]["net_pnl"] == 0.0
    assert len(rh) == 4


def test_unreadable_trade_row_names_the_line():
    bad = HEADER + '"9/21/2026","9/21/2026","9/22/2026","AAPL","Apple","Buy","ten","$175.00","($1,750.00)"\n'
    with pytest.raises(ValueError, match="line 2"):
        parse_broker_csv(bad, "robinhood", account_id=1)


def test_expiry_of_a_position_opened_in_an_earlier_import():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE trades (id INTEGER PRIMARY KEY, account_id INTEGER, trade_group TEXT,
                    date TEXT, ticker TEXT, instrument_type TEXT, side TEXT, gross_pnl REAL, net_pnl REAL,
                    commissions REAL, executions TEXT, option_expiry TEXT, option_strike REAL,
                    option_type TEXT, source TEXT)""")
    opened = HEADER + '"9/22/2026","9/22/2026","9/23/2026","QQQ","QQQ 9/25/2026 Put $400.00","STO","1","$3.00","$299.97"\n'
    for t in parse_broker_csv(opened, "robinhood", 1, conn)[0]:
        conn.execute("INSERT INTO trades (account_id, trade_group, date, ticker, instrument_type, side, "
                     "gross_pnl, net_pnl, commissions, executions, option_expiry, option_strike, option_type, source) "
                     "VALUES (:account_id, :trade_group, :date, :ticker, :instrument_type, :side, :gross_pnl, "
                     ":net_pnl, :commissions, :executions, :option_expiry, :option_strike, :option_type, :source)", t)

    expired = HEADER + '"9/25/2026","9/25/2026","9/25/2026","QQQ","Option Expiration for QQQ 9/25/2026 Put $400.00","OEXP","1","",""\n'
    parse_broker_csv(expired, "robinhood", 1, conn)
    # the short put is bought back at zero inside the stored trade: kept the 300.00 premium
    row = conn.execute("SELECT side, gross_pnl, net_pnl, executions FROM trades").fetchone()
    assert (row["side"], row["gross_pnl"], row["net_pnl"]) == ("SHORT", 300.0, 299.97)
    assert [e["action"] for e in json.loads(row["executions"])] == ["SOLD", "BOT"]
