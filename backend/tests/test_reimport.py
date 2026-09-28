"""Importing a statement on top of trades already stored for the same day.

    cd backend && python -m pytest tests -q

A second import used to number its new position cycles from _1 again and overwrite the
stored trade with that name, so a whole round trip disappeared.
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

HEAD = "Account Statement\n\nCash Balance\nDATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n"
ROWS = [
    '9/15/26,09:46:16,TRD,="1",BOT +400 TSLA @250.00,,,"-100,000.00","1"',
    '9/15/26,09:50:48,TRD,="2",SOLD -100 TSLA @250.50,-0.75,,"25,050.00","1"',
    '9/15/26,09:57:47,TRD,="3",SOLD -300 TSLA @249.00,-2.25,,"74,700.00","1"',
    '9/15/26,09:59:52,TRD,="4",BOT +300 CRWD @200.00,,,"-60,000.00","1"',
    '9/15/26,10:14:04,TRD,="5",SOLD -100 CRWD @201.00,-0.50,,"20,100.00","1"',
    '9/15/26,10:19:49,TRD,="6",SOLD -200 CRWD @199.50,-1.00,,"39,900.00","1"',
    '9/15/26,10:55:12,TRD,="7",BOT +200 TSLA @248.00,,,"-49,600.00","1"',
    '9/15/26,11:00:35,TRD,="8",SOLD -200 TSLA @249.00,-1.50,,"49,800.00","1"',
]


def statement(rows):
    return HEAD + "\n".join(rows) + "\n"


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
        yield c


def post(client, content):
    r = client.post("/api/import-csv", data={"account_id": "1"},
                    files={"file": ("statement.csv", content.encode(), "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()


def stored(client):
    conn = sqlite3.connect(client.db)
    rows = conn.execute("SELECT trade_group, net_pnl, executions FROM trades ORDER BY trade_group").fetchall()
    return {g: (pnl, len(json.loads(ex))) for g, pnl, ex in rows}


def test_second_import_adds_to_the_day_instead_of_overwriting(client):
    conn = sqlite3.connect(client.db)
    if not conn.execute("SELECT 1 FROM accounts WHERE id=1").fetchone():
        conn.execute("INSERT INTO accounts (id, name, type) VALUES (1, 'Day', 'day_trading')")
        conn.commit()

    # a statement taken mid-morning: the first TSLA trip and the CRWD entry only
    post(client, statement(ROWS[:4]))
    conn.execute("INSERT INTO trade_analysis (trade_group, ticker, date, strategy) VALUES "
                 "('9/15/26_TSLA_STOCK_1', 'TSLA', '2026-09-15', 'Morning note')")
    conn.commit()

    # the full day's statement afterwards
    post(client, statement(ROWS))
    trades = stored(client)
    assert set(trades) == {"9/15/26_TSLA_STOCK_1", "9/15/26_TSLA_STOCK_2", "9/15/26_CRWD_STOCK_1"}
    assert trades["9/15/26_TSLA_STOCK_1"] == (-253.0, 3)
    assert trades["9/15/26_TSLA_STOCK_2"][1] == 2
    assert trades["9/15/26_CRWD_STOCK_1"][1] == 3
    note = conn.execute("SELECT trade_group FROM trade_analysis WHERE strategy='Morning note'").fetchone()
    assert note[0] == "9/15/26_TSLA_STOCK_1"

    # the same statement again changes nothing
    before = stored(client)
    result = post(client, statement(ROWS))
    assert result["imported"] == 0
    assert stored(client) == before


GENERIC = "date,time,symbol,side,quantity,price,commission\n"
GROWS = [
    "2026-09-15,09:46:16,TSLA,BUY,400,250.00,0",
    "2026-09-15,09:57:47,TSLA,SELL,400,249.00,3.00",
    "2026-09-15,10:55:12,TSLA,BUY,200,248.00,0",
    "2026-09-15,11:00:35,TSLA,SELL,200,249.00,1.50",
]


def test_generic_template_reimport_keeps_iso_named_trades(client):
    conn = sqlite3.connect(client.db)
    if not conn.execute("SELECT 1 FROM accounts WHERE id=1").fetchone():
        conn.execute("INSERT INTO accounts (id, name, type) VALUES (1, 'Day', 'day_trading')")
        conn.commit()
    r = client.post("/api/import-csv", data={"account_id": "1", "broker": "generic"},
                    files={"file": ("fills.csv", (GENERIC + "\n".join(GROWS[:2]) + "\n").encode(), "text/csv")})
    assert r.status_code == 200, r.text
    r = client.post("/api/import-csv", data={"account_id": "1", "broker": "generic"},
                    files={"file": ("fills.csv", (GENERIC + "\n".join(GROWS) + "\n").encode(), "text/csv")})
    assert r.status_code == 200, r.text
    trades = stored(client)
    assert set(trades) == {"2026-09-15_TSLA_STOCK_1", "2026-09-15_TSLA_STOCK_2"}
    assert trades["2026-09-15_TSLA_STOCK_1"] == (-403.0, 2)


RH = '"Activity Date","Process Date","Settle Date","Instrument","Description","Trans Code","Quantity","Price","Amount"\n'


def rh_report(rows):
    # Robinhood lists newest first
    return RH + "".join(f'"{d}","{d}","{d}","{t}","{desc}","{code}","{q}","{p}","{a}"\n'
                        for d, t, desc, code, q, p, a in reversed(rows))


RH_ROWS = [
    ("9/10/2026", "SPY", "SPY 10/16/2026 Call $450.00", "BTO", "1", "$2.00", "($200.03)"),
    ("9/25/2026", "NVDA", "Nvidia", "Buy", "20", "$120.00", "($2,400.00)"),
    ("9/28/2026", "AMD", "AMD", "Buy", "5", "$150.00", "($750.00)"),
    # the first report was pulled here, mid-day on 9/28
    ("9/28/2026", "AMD", "AMD", "Buy", "5", "$150.00", "($750.00)"),
    ("9/28/2026", "AMD", "AMD", "Sell", "10", "$155.00", "$1,549.95"),
    ("10/2/2026", "NVDA", "Nvidia", "Sell", "20", "$125.00", "$2,499.94"),
    ("10/9/2026", "SPY", "SPY 10/16/2026 Call $450.00", "STC", "1", "$3.00", "$299.97"),
]


def test_robinhood_overlapping_reports(client):
    conn = sqlite3.connect(client.db)
    if not conn.execute("SELECT 1 FROM accounts WHERE id=1").fetchone():
        conn.execute("INSERT INTO accounts (id, name, type) VALUES (1, 'RH', 'day_trading')")
        conn.commit()

    def send(rows):
        r = client.post("/api/import-csv", data={"account_id": "1", "broker": "robinhood"},
                        files={"file": ("rh.csv", rh_report(rows).encode(), "text/csv")})
        assert r.status_code == 200, r.text
        return r.json()

    send(RH_ROWS[:3])
    assert send(RH_ROWS)["skipped"] == 3   # only the fills already stored, not the repeat AMD buy
    trades = stored(client)
    # AMD: 2 x 750.00 paid, 1,549.95 received -> +49.95 over three fills
    assert trades["2026-09-28_AMD_STOCK_1"] == (49.95, 3)
    # positions open at the end of the first report close in the second
    assert trades["2026-10-02_NVDA_STOCK_1"] == (99.94, 2)
    assert trades["2026-09-10_SPY_OPTION_2026-10-16_450_CALL_1"] == (99.94, 2)

    assert send(RH_ROWS)["skipped"] == len(RH_ROWS)   # a third import changes nothing
    assert stored(client) == trades
