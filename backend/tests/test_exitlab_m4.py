"""M4 regressions: backfill contamination, period promotion and chart sample counts."""
from dataclasses import replace
import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_exitlab_engine import fixture, ZERO
from test_exitlab_storage import db, store_trade, cohort
from exitlab.bars import put_bars
from exitlab.compare import compare
from exitlab.eligibility import from_row
from exitlab.service import readiness
from exitlab.simple import simple_replay
from exitlab.rules import PRESETS
from exitlab.api import router, connection
from exitlab.schema import migrate


def add_days(db, days, start=30):
    entries = []
    for i, day in enumerate(days, start):
        t, bars = fixture([(100, 101.01, 99.5, 101)], day=day)
        t = replace(t, id=i)
        store_trade(db, t)
        put_bars(db, t.symbol, bars)
        entries.append({'id': i, 'trade': t, 'bars': bars, 'reasons': []})
    db.commit()
    return entries


@pytest.mark.parametrize('side,opening', [('LONG','BOT'),('SHORT','SOLD')])
def test_newest_first_same_second_scalp(side, opening):
    closing = 'SOLD' if opening == 'BOT' else 'BOT'
    row = {'id':1,'ticker':'FIXTURE','instrument_type':'STOCK','side':side,'executions':json.dumps([
        {'date':'2026-10-16','time':'09:45:07','action':closing,'qty':10,'price':100.1},
        {'date':'2026-10-16','time':'09:45:07','action':opening,'qty':10,'price':100}])}
    trade = from_row(row, 99 if side == 'LONG' else 101)
    assert trade.entry == 100 and trade.exit == 100.1
    assert trade.entered_at == trade.exited_at
