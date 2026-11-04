"""Thinkorswim multi-leg option orders.

    cd backend && python -m pytest tests -q

Each VERTICAL used to be booked from Cash Balance at the net price, then Trade History added the
short leg's own fill on top and the long leg row (blank exec time) was dropped, so a day with
verticals showed more P&L than the statement. All accounts, prices and balances below are made up.
"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from csv_parser import parse_thinkorswim_csv  # noqa: E402

CASH = """Cash Balance
DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE
3/10/26,09:52:14,TRD,="1",SOLD -1 VERTICAL MSFT 100 (Weeklys) 10 MAR 26 410/405 PUT @2.10 CBOE,-0.03,-1.30,210.00,"1"
3/10/26,10:06:41,TRD,="2",BOT +1 VERTICAL MSFT 100 (Weeklys) 10 MAR 26 410/405 PUT @1.95 CBOE,-0.02,-1.30,-195.00,"1"
3/10/26,11:21:03,TRD,="3",SOLD -1 VERTICAL NVDA 100 10 MAR 26 182.5/180 PUT @.95 CBOE,-0.03,-1.30,95.00,"1"
3/10/26,11:47:38,TRD,="4",BOT +1 VERTICAL NVDA 100 10 MAR 26 182.5/180 PUT @.70 MIAX,-0.02,-1.30,-70.00,"1"
"""

HISTORY = """Account Trade History
,Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type
,3/10/26 11:47:38,VERTICAL,BUY,+1,TO CLOSE,NVDA,10 MAR 26,182.5,PUT,1.60,.70,LMT
,,,SELL,-1,TO CLOSE,NVDA,10 MAR 26,180,PUT,.90,DEBIT,
,3/10/26 11:21:03,VERTICAL,SELL,-1,TO OPEN,NVDA,10 MAR 26,182.5,PUT,2.40,.95,LMT
,,,BUY,+1,TO OPEN,NVDA,10 MAR 26,180,PUT,1.45,CREDIT,
,3/10/26 10:06:41,VERTICAL,BUY,+1,TO CLOSE,MSFT,10 MAR 26,410,PUT,3.80,1.95,LMT
,,,SELL,-1,TO CLOSE,MSFT,10 MAR 26,405,PUT,1.85,DEBIT,
,3/10/26 09:52:14,VERTICAL,SELL,-1,TO OPEN,MSFT,10 MAR 26,410,PUT,4.60,2.10,LMT
,,,BUY,+1,TO OPEN,MSFT,10 MAR 26,405,PUT,2.50,CREDIT,
"""

ROLL_HISTORY = """Account Trade History
,Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type
,2/18/26 10:31:12,CALENDAR,BUY,+2,TO OPEN,AAPL,13 MAR 26,230,PUT,12.40,3.25,LMT
,,,SELL,-2,TO OPEN,AAPL,27 FEB 26,230,PUT,9.15,DEBIT,
,2/18/26 14:05:47,SINGLE,SELL,-2,TO CLOSE,AAPL,13 MAR 26,230,PUT,13.10,13.10,LMT
"""


OVERLAP = """Cash Balance
DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE
3/10/26,09:52:14,TRD,="1",SOLD -1 VERTICAL MSFT 100 (Weeklys) 10 MAR 26 410/405 PUT @2.10 CBOE,-0.03,-1.30,210.00,"1"
3/10/26,09:58:30,TRD,="2",SOLD -2 VERTICAL MSFT 100 (Weeklys) 10 MAR 26 410/407.5 PUT @1.20 CBOE,-0.05,-2.60,240.00,"1"
3/10/26,10:06:41,TRD,="3",BOT +1 VERTICAL MSFT 100 (Weeklys) 10 MAR 26 410/405 PUT @1.95 CBOE,-0.02,-1.30,-195.00,"1"
3/10/26,10:40:09,TRD,="4",BOT +2 VERTICAL MSFT 100 (Weeklys) 10 MAR 26 410/407.5 PUT @1.25 CBOE,-0.05,-2.60,-250.00,"1"
"""

CUSTOM = """Cash Balance
DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE
2/18/26,10:31:12,TRD,="1",BOT +2 CALENDAR AAPL 100 (Weeklys) 13 MAR 26/27 FEB 26 230 PUT @3.25 CBOE,-0.17,-2.60,-650.00,"1"

Account Trade History
,Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type
,2/18/26 10:31:12,CALENDAR,BUY,+2,TO OPEN,AAPL,13 MAR 26,230,PUT,12.40,3.25,LMT
,,,SELL,-2,TO OPEN,AAPL,27 FEB 26,230,PUT,9.15,DEBIT,
"""

BROKEN_PARENT = """Account Trade History
,Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type
,2/18/26 10:31:12,CALENDAR,BUY,+2,TO OPEN,AAPL,13 MAR 26,230,PUT,12.40,3.25,LMT
,2/18/26,SINGLE,BUY,+1,TO OPEN,AAPL,13 MAR 26,235,PUT,6.00,6.00,LMT
,,,SELL,-2,TO OPEN,AAPL,27 FEB 26,230,PUT,9.15,DEBIT,
"""


def by_ticker(trades):
    return {t['ticker']: t for t in trades}


def test_vertical_is_one_trade_at_the_net_price():
    trades, _ = parse_thinkorswim_csv(CASH + "\n" + HISTORY, account_id=1)
    t = by_ticker(trades)
    assert len(trades) == 2
    assert t['MSFT']['gross_pnl'] == 15.0
    assert t['NVDA']['gross_pnl'] == 25.0
    assert t['MSFT']['net_pnl'] == round(15.0 - 2.65, 2)
    assert len(t['MSFT']['executions'].split('"action"')) - 1 == 2


def test_vertical_from_cash_balance_alone_is_unchanged():
    trades, _ = parse_thinkorswim_csv(CASH, account_id=1)
    assert sorted(t['gross_pnl'] for t in trades) == [15.0, 25.0]


def test_other_multi_leg_orders_keep_every_leg():
    trades, _ = parse_thinkorswim_csv(ROLL_HISTORY, account_id=1)
    legs = {(t['option_expiry'], t['side']): t for t in trades}
    # the 13 MAR leg opened in the calendar and closed later: a real round trip
    assert legs[('2026-03-13', 'LONG')]['gross_pnl'] == round((13.10 - 12.40) * 2 * 100, 2)
    # the 27 FEB short leg was the blank second row: no longer dropped
    assert ('2026-02-27', 'SHORT') in legs


def test_verticals_sharing_a_strike_stay_separate():
    trades, _ = parse_thinkorswim_csv(OVERLAP, account_id=1)
    pnl = {t['trade_group'].split('_')[-3]: t['gross_pnl'] for t in trades}
    assert pnl == {'410-405': 15.0, '410-407.5': -10.0}


def test_calendar_fees_land_on_its_first_leg():
    trades, _ = parse_thinkorswim_csv(CUSTOM, account_id=1)
    fees = {t['option_expiry']: t['commissions'] for t in trades}
    assert fees == {'2026-03-13': 2.77, '2026-02-27': 0.0}


def test_a_malformed_row_ends_the_previous_order():
    trades, _ = parse_thinkorswim_csv(BROKEN_PARENT, account_id=1)
    assert {t['option_expiry'] for t in trades} == {'2026-03-13'}
