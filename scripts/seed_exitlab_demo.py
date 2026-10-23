"""Reproducible synthetic executions on locally cached historical Alpaca prices.

Run with --fetch once, then offline without it. No credentials or bars are exported.
Only adds rows to a dedicated demo account; never deletes or rewrites existing trades.
"""
import argparse
from collections import Counter
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
LABEL = "Synthetic trades, historical market prices"
ACCOUNT = "Exit Lab demo | " + LABEL


def minute_points(bar, serial):
    # A declared synthetic intraminute path, not reconstructed historical ticks.
    middle = (bar.high, bar.low) if serial % 2 else (bar.low, bar.high)
    return [(bar.ts+timedelta(seconds=s), p) for s, p in zip((0, 20, 40, 59), (bar.open, *middle, bar.close))]


def price_at(bar, second, serial):
    points = minute_points(bar, serial)
    for (a, p), (b, q) in zip(points, points[1:]):
        if second <= (b-bar.ts).total_seconds():
            return p+(q-p)*(second-(a-bar.ts).total_seconds())/(b-a).total_seconds()
    return bar.close


def actual_exits(bars, entered, entry, stop, direction, quantity, serial, closing):
    import math
    style = ("early scratch", "timed 25", "timed 80", "partial then held", "held to close")[serial % 5]
    plans = {"early scratch": [(5, quantity)], "timed 25": [(25, quantity)],
             "timed 80": [(80, quantity)], "partial then held": [(15, quantity//2), (70, quantity-quantity//2)],
             "held to close": []}[style]
    scheduled = {min(entered+timedelta(minutes=m), closing-timedelta(seconds=1)): q for m, q in plans}
    remaining, fills = quantity, []
    previous_time, previous_price = entered, entry
    for bar in bars:
        if bar.ts+timedelta(minutes=1) <= entered:
            continue
        points = dict(minute_points(bar, serial))
        for stamp in scheduled:
            if bar.ts <= stamp < bar.ts+timedelta(minutes=1):
                points[stamp] = price_at(bar, (stamp-bar.ts).seconds, serial)
        for stamp, price in sorted(points.items()):
            if stamp <= entered:
                continue
            if direction*(price-stop) <= 0:
                gap = stamp == bar.ts
                if not gap:
                    portion = (stop-previous_price)/(price-previous_price) if price != previous_price else 1
                    stamp = previous_time+timedelta(seconds=max(1, math.ceil((stamp-previous_time).total_seconds()*portion)))
                fills.append((stamp, remaining, price if gap else stop))
                return style, fills
            if stamp in scheduled:
                qty = min(remaining, scheduled[stamp])
                fills.append((stamp, qty, price))
                remaining -= qty
                if remaining == 0:
                    return style, fills
            previous_time, previous_price = stamp, price
    fills.append((closing, remaining, bars[-1].close))
    return style, fills


def seed(conn, days, symbols, fetch_missing=False):
    from exitlab.bars import cached, fetch
    from exitlab.calendar import session
    from exitlab.models import NY, iso, continuous
    from exitlab.eligibility import from_row

    with conn:
        row = conn.execute("SELECT id FROM accounts WHERE name=?", (ACCOUNT,)).fetchone()
        account_id = row[0] if row else conn.execute(
            "INSERT INTO accounts(name,type,color,broker) VALUES(?,'day_trading','#F0A93B','Synthetic demo')",
            (ACCOUNT,)).lastrowid
    added, existing, skipped = 0, 0, []
    for day in days:
        opening, closing = session(day)
        start = opening - timedelta(minutes=90)
        # Benchmark is cached even when SPY is not itself a demo trade symbol.
        spy = cached(conn, "SPY", start, closing, "sip")
        if not continuous(spy, opening, closing) and fetch_missing:
            if fetch(conn, "SPY", start, closing, "sip")["feed"] != "sip":
                raise ValueError("RS/RW demo requires SIP SPY prices")
        for symbol in symbols:
            bars = cached(conn, symbol, start, closing, "sip")
            if not continuous(bars, opening, closing) and fetch_missing:
                result = fetch(conn, symbol, start, closing, "sip")
                if result["feed"] != "sip":
                    raise ValueError("Demo requires SIP. IEX fallback was cached but not used.")
                bars = cached(conn, symbol, start, closing, "sip")
            if not continuous(bars, opening, closing):
                skipped.append(f"{day} {symbol}: incomplete regular session; run --fetch")
                continue
            by_time = {b.ts: b for b in bars}
            for slot, minute in enumerate((30, 105, 210)):
                group = f"exitlab-demo-v2:{day}:{symbol}:{slot}"
                if conn.execute("SELECT 1 FROM trades WHERE account_id=? AND trade_group=?", (account_id, group)).fetchone():
                    existing += 1
                    continue
                # Deterministic, no future price selection or outcome optimization.
                serial = days.index(day)*len(symbols)*3 + symbols.index(symbol)*3 + slot
                direction = 1 if serial % 3 else -1
                entry_minute = opening + timedelta(minutes=minute)
                second = 7 + serial % 43
                entered = entry_minute + timedelta(seconds=second)
                entry = price_at(by_time[entry_minute], second, serial)
                prior = [b for b in bars if entry_minute-timedelta(minutes=20) <= b.ts < entry_minute]
                risk = round(max(0.12, entry*0.0012, sum(b.high-b.low for b in prior)/len(prior)*1.8), 2)
                stop = round(entry-direction*risk, 4)
                quantity = (25, 50, 75, 100)[serial % 4]
                style, exits = actual_exits(bars, entered, entry, stop, direction, quantity, serial, closing)
                exited = exits[-1][0]
                exit_price = sum(q*p for _, q, p in exits)/quantity
                executions = [
                    {"date": t.astimezone(NY).date().isoformat(), "time": t.astimezone(NY).strftime("%H:%M:%S"),
                     "action": a, "qty": q, "price": p, "commission": 0}
                    for t, a, q, p in [(entered, "BOT" if direction == 1 else "SOLD", quantity, entry)] +
                    [(t, "SOLD" if direction == 1 else "BOT", q, p) for t, q, p in exits]]
                pnl = direction*(exit_price-entry)*quantity
                with conn:
                    tid = conn.execute("""INSERT INTO trades
                        (account_id,trade_group,date,ticker,instrument_type,side,gross_pnl,net_pnl,
                         executions,source,excursion_source,setup_notes,setup)
                        VALUES(?,?,?,?,'STOCK',?,?,?,?, 'demo','demo',?,?)""",
                        (account_id, group, day, symbol, "LONG" if direction == 1 else "SHORT",
                         pnl, pnl, json.dumps(executions), json.dumps({"label": LABEL, "actual_exit_style": style, "generator": "v2 fixed schedule, synthetic OHLC path, hard stop"}),
                         ("LOD Reversal", "Opening Drive", "VWAP Reclaim", "Trend Pullback")[serial % 4])).lastrowid
                    row = conn.execute("SELECT * FROM trades WHERE id=?", (tid,)).fetchone()
                    trade = from_row(row)
                    bucket = serial % 10
                    conn.execute("""INSERT INTO trade_analysis(trade_group,ticker,date,stop_loss,notes,match_confidence)
                        VALUES(?,?,?,?,?,'manual')""", (group, symbol, day, stop if bucket != 9 else None, LABEL))
                added += 1
            print(f"Cached SIP {day} {symbol}: {len(bars)} bars", flush=True)
    return {"label": LABEL, "account_id": account_id, "added": added, "existing": existing, "skipped": skipped}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=ROOT / "backend" / "exitlab_local.db")
    parser.add_argument("--end", default="2026-10-16")
    parser.add_argument("--sessions", type=int, default=24)
    parser.add_argument("--symbols", nargs="+", default=["SPY", "QQQ", "NVDA"])
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    path = args.database.resolve()
    if path.parent != (ROOT / "backend").resolve() or not path.name.startswith("exitlab_"):
        parser.error("Use a local backend/exitlab_*.db in this worktree only")
    if not 2 <= args.sessions <= 100:
        parser.error("Use 2 to 100 sessions")
    os.environ["DATABASE_PATH"] = str(path)
    import database
    from exitlab.calendar import session
    from exitlab import service, simple
    from exitlab.rules import Costs
    from exitlab.excursion import excursions
    database.init_db()
    days, day = [], date.fromisoformat(args.end)
    while len(days) < args.sessions:
        try:
            session(day.isoformat())
            days.append(day.isoformat())
        except ValueError:
            pass
        day -= timedelta(days=1)
    days.reverse()
    conn = database.get_db()
    try:
        result = seed(conn, days, args.symbols, args.fetch)
        ready = service.readiness(conn, result["account_id"], "sip")
        result.update(sessions=days, readiness=ready,
                      bars=conn.execute("SELECT COUNT(*) FROM market_bars WHERE feed='sip'").fetchone()[0])
        if args.smoke:
            comparison = simple.history(conn, result["account_id"])[0]
            result["comparison"] = {**comparison, "rules": [
                {k: v for k, v in r.items() if k != "trades"} for r in comparison["rules"]]}
            first = next(e for e in service.load_entries(conn, result["account_id"], "sip") if not e["reasons"])
            result["first_trade"] = {"id": first["id"], "symbol": first["trade"].symbol,
                                     "excursion": excursions(first["trade"], first["bars"])}
        if args.report:
            args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in result.items() if k != "readiness"}, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
