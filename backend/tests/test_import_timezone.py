"""Thinkorswim fill times converted from the trading machine's clock to exchange (Eastern) time.

    cd backend && python -m pytest tests -q

Expected times are worked out by hand from the UTC offsets in September 2026 (US and EU
still on summer time): New York UTC-4, Bucharest UTC+3, Tokyo UTC+9.
"""
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from csv_parser import IMPORT_LOCAL_TZ, _to_exchange_time, parse_broker_csv  # noqa: E402

HEAD = "Account Statement\n\nCash Balance\nDATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n"


def statement(*rows):
    return HEAD + "\n".join(rows) + "\n"


TRIP = statement(
    '9/15/26,05:00:00,TRD,="1",BOT +100 TSLA @250.00,,,"-25,000.00","1"',
    '9/15/26,16:30:00,TRD,="2",SOLD -100 TSLA @251.00,-0.75,,"25,100.00","1"',
)


def only_trade(import_tz=None):
    kwargs = {} if import_tz is None else {"import_tz": import_tz}
    trades, skipped = parse_broker_csv(TRIP, "auto", 1, **kwargs)
    assert skipped == 0 and len(trades) == 1
    t = trades[0]
    return t, [(e["date"], e["time"]) for e in json.loads(t["executions"])]


def test_default_is_exchange_time_so_nothing_shifts():
    assert IMPORT_LOCAL_TZ == "America/New_York"
    t, fills = only_trade()
    assert t["trade_group"] == "9/15/26_TSLA_STOCK_1"
    assert fills == [("2026-09-15", "05:00:00"), ("2026-09-15", "16:30:00")]


def test_bucharest_is_seven_hours_ahead():
    # 16:30 EEST (UTC+3) = 13:30 UTC = 09:30 EDT
    assert _to_exchange_time("2026-09-15", "16:30:00", "Europe/Bucharest") == ("2026-09-15", "09:30:00")
    # without seconds
    assert _to_exchange_time("2026-09-15", "16:30", "Europe/Bucharest") == ("2026-09-15", "09:30:00")


def test_conversion_can_move_a_fill_to_the_previous_day():
    # 05:00 JST (UTC+9) on 9/15 = 20:00 UTC on 9/14 = 16:00 EDT on 9/14
    assert _to_exchange_time("2026-09-15", "05:00:00", "Asia/Tokyo") == ("2026-09-14", "16:00:00")


def test_bad_input_falls_back_to_the_unconverted_value():
    assert _to_exchange_time("2026-09-15", "not a time", "Europe/Bucharest") == ("2026-09-15", "not a time")
    assert _to_exchange_time("2026-09-15", "16:30:00", "Not/AZone") == ("2026-09-15", "16:30:00")
    assert _to_exchange_time("", "16:30:00", "Europe/Bucharest") == ("", "16:30:00")


def test_import_shifts_fills_and_keeps_the_statement_date_form_in_the_key():
    # 05:00 JST -> 16:00 EDT the day before; 16:30 JST -> 03:30 EDT the same day
    t, fills = only_trade("Asia/Tokyo")
    assert fills == [("2026-09-14", "16:00:00"), ("2026-09-15", "03:30:00")]
    # the key is dated by the closing fill, in M/D/YY like keys stored before the conversion existed
    assert t["trade_group"] == "9/15/26_TSLA_STOCK_1"
    assert t["net_pnl"] == 99.25
