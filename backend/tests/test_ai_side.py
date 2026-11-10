"""The AI prompts read option trades by direction: a bought put is bearish, a sold put bullish.

    cd backend && python -m pytest tests/test_ai_side.py -q
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_analysis import side_for_ai, build_trades_context, build_brain_context  # noqa: E402


def test_side_for_ai_spells_out_option_direction():
    opt = {'instrument_type': 'OPTION'}
    assert side_for_ai({**opt, 'side': 'LONG', 'option_type': 'PUT'}) == 'LONG (bought put, bearish)'
    assert side_for_ai({**opt, 'side': 'SHORT', 'option_type': 'PUT'}) == 'SHORT (sold put, bullish)'
    assert side_for_ai({**opt, 'side': 'LONG', 'option_type': 'CALL'}) == 'LONG (bought call, bullish)'
    assert side_for_ai({**opt, 'side': 'SHORT', 'option_type': 'CALL'}) == 'SHORT (sold call, bearish)'
    assert side_for_ai({'instrument_type': 'STOCK', 'side': 'SHORT'}) == 'SHORT'
    assert side_for_ai({'instrument_type': 'FUTURE', 'side': 'LONG', 'option_type': None}) == 'LONG'
    assert side_for_ai({'instrument_type': 'OPTION', 'side': 'LONG'}) == 'LONG'  # no option_type: unchanged


def _db():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE trades (id INTEGER PRIMARY KEY, account_id INTEGER, trade_group TEXT,
        date TEXT, ticker TEXT, instrument_type TEXT, side TEXT, option_type TEXT,
        executions TEXT, net_pnl REAL, gross_pnl REAL)""")
    conn.execute("""CREATE TABLE trade_analysis (trade_group TEXT, account_id INTEGER, strategy TEXT,
        r_multiple REAL, emotional_state TEXT, mistakes TEXT, stop_loss REAL, target_price REAL)""")
    conn.execute("CREATE TABLE diary_entries (account_id INTEGER, entry_date TEXT, ai_analysis TEXT)")
    fills = json.dumps([{'action': 'BOT', 'qty': 1, 'price': 2.0, 'time': '10:00:00'},
                        {'action': 'SOLD', 'qty': 1, 'price': 3.0, 'time': '10:30:00'}])
    conn.execute("INSERT INTO trades VALUES (1, 1, 'g1', '2026-11-09', 'AAPL', 'OPTION', 'LONG', 'PUT', ?, 100, 101)", (fills,))
    return conn


def test_diary_context_carries_option_type():
    ctx = build_trades_context(_db(), '2026-11-09', 1)
    assert side_for_ai(ctx[0]) == 'LONG (bought put, bearish)'


def test_brain_context_shows_bought_put_as_bearish():
    assert 'AAPL | LONG (bought put, bearish)' in build_brain_context(_db(), [1])
