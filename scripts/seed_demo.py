"""Seed the Trading Journal AI database with realistic synthetic data.

Creates backend/trading_journal.db through the app's own schema (database.py),
so the seed can never drift from what the app expects. Everything is generated
from a fixed date and a seeded RNG, so the output is reproducible.

All data produced here is synthetic. No real trades, accounts, or people.

For development and screenshots only. A normal install is always empty.

Usage:
    python scripts/seed_demo.py                 # refuses if a database already exists
    python scripts/seed_demo.py --replace-database
"""

import json
import os
import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

DB_FILE = BACKEND / "trading_journal.db"
os.environ["DATABASE_PATH"] = str(DB_FILE)

import database  # noqa: E402  (path set above)

# ── fixed parameters ──────────────────────────────────────────────────────────

SEED_DATE = date(2026, 8, 21)          # last trading day in the dataset (a Friday)
WEEKS = 12
rng = random.Random(20260821)

ACCOUNTS = [
    ("Demo Main", "day_trading", "#6366f1", "Thinkorswim"),
    ("Demo Small", "day_trading", "#22c55e", "Thinkorswim"),
    ("Paper Practice", "day_trading", "#f59e0b", "Paper"),
]

SETUPS = ["Opening Drive", "VWAP Reclaim", "Range Break", "Trend Pullback"]
SETUP_SIDE_SHORT_PROB = {"Opening Drive": 0.15, "VWAP Reclaim": 0.10,
                         "Range Break": 0.30, "Trend Pullback": 0.25}

# Rough mid-2026 price anchors. Synthetic, only need to look plausible.
# Fallback levels only. demo_prices.json carries the real daily close for each
# ticker and date, so a synthetic fill lands on that day's actual price action
# and the trade chart lines up once market-data keys are set.
TICKERS = {
    "NVDA": 207, "TSLA": 313, "AMD": 522, "META": 595, "SPY": 739,
    "AAPL": 333, "PLTR": 123, "SMCI": 30, "COIN": 158, "MU": 921,
}

_PRICE_FILE = Path(__file__).resolve().parent / "demo_prices.json"
try:
    REAL_CLOSES = json.loads(_PRICE_FILE.read_text())
except Exception:
    REAL_CLOSES = {}


def close_on(ticker, day):
    """The real close for that ticker on that date, or the nearest one before it."""
    series = REAL_CLOSES.get(ticker) or {}
    if not series:
        return TICKERS[ticker]
    key = day.isoformat()
    if key in series:
        return series[key]
    earlier = [d for d in series if d <= key]
    return series[max(earlier)] if earlier else series[min(series)]

LOSING_WEEK_MONDAY = date(2026, 7, 13)          # one clearly losing week
OVERTRADE_DAYS = {date(2026, 8, 4), date(2026, 8, 5)}  # two-day overtrading cluster

# A real discretionary record is a lot of small green days and a few big red
# ones: the win rate stays high while the profit factor stays modest, because
# the damage is concentrated. Without these the demo shows a red day whenever
# variance turns, which is not what the same win rate looks like in practice.
BAD_DAYS = {
    date(2026, 6, 26), date(2026, 7, 22), date(2026, 8, 12),
}

ENTRY_REASONS = [
    "Held the opening range high with volume behind it",
    "Reclaimed VWAP on the second push, risk defined below",
    "Broke yesterday's range with sector moving the same way",
    "First pullback to the 9 EMA in a clean uptrend",
    "Failed to reclaim VWAP, sellers stacked on the tape",
    "Gap held above premarket high, took the break",
    "Higher low into the level, tight stop under it",
]
EXIT_REASONS = [
    "Hit the first target, trailed the rest and got stopped",
    "Stopped at plan, no hesitation",
    "Momentum stalled at the half dollar, paid myself",
    "Scaled half at 1R, rest at the next level",
    "Stopped out, level did not hold",
    "Closed into the spike, extended from VWAP",
    "Time stop, it went nowhere for twenty minutes",
]
MISTAKES_POOL = ["Chased entry", "Moved stop", "Sized too big", "Overtraded",
                 "Revenge trade", "Late entry"]
IDEA_SOURCES = ["Watchlist", "Scanner", "News", "Own research"]

DIARY_GREEN = [
    "Waited for the setup instead of predicting it. {tk} paid for the patience.",
    "Two trades, both planned last night. Nothing to add, this is what it should look like.",
    "Took the {tk} break exactly at the trigger. Small size, clean execution, done by 11.",
    "Green day but the second entry was early. The result hides the mistake, the journal should not.",
    "Only A setups today. Passed on three tickers that looked close but were not there.",
    "{tk} gave the whole move. Scaled out too fast again, left half of it on the table.",
]
DIARY_RED = [
    "Stopped twice on {tk} and walked away. Losing day, correct process.",
    "Forced the {tk} trade out of boredom. The market did not owe me a setup today.",
    "Red day. Both losses were within plan, no rule broken, moving on.",
    "Cut the loser late because I wanted it to work. That is a stop problem, not a market problem.",
    "Choppy tape all morning. Should have quit after the first two rotations failed.",
]
DIARY_CLUSTER = [
    "Six trades. Six. The plan says three max and I blew through it by 10:30. This is the leak.",
    "Kept clicking to get yesterday back. Revenge trading with extra steps. Stopping at two tomorrow.",
]

# ── helpers ───────────────────────────────────────────────────────────────────

def weekdays(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def money(x: float) -> float:
    return round(x, 2)


def make_times(entry_min: int, hold_min: int, n_entries: int, n_exits: int):
    """Return sorted HH:MM:SS strings for entry and exit fills."""
    def fmt(m, s):
        return f"{m // 60:02d}:{m % 60:02d}:{s:02d}"
    entries = [fmt(entry_min + i, rng.randint(0, 59)) for i in range(n_entries)]
    exit_start = entry_min + hold_min
    exits = [fmt(min(exit_start + i * rng.randint(1, 4), 15 * 60 + 57), rng.randint(0, 59))
             for i in range(n_exits)]
    return entries, exits


def split_qty(qty: int, parts: int):
    if parts == 1:
        return [qty]
    a = int(qty * rng.uniform(0.4, 0.6))
    a = max(1, min(qty - 1, a))
    return [a, qty - a]


# ── trade generation ──────────────────────────────────────────────────────────

def generate_trades():
    # Anchor to the Monday 12 weeks back from SEED_DATE's week.
    monday_of_seed_week = SEED_DATE - timedelta(days=SEED_DATE.weekday())
    start = monday_of_seed_week - timedelta(weeks=WEEKS - 1)

    trades = []
    seq_by_day = {}

    for day in weekdays(start, SEED_DATE):
        is_cluster = day in OVERTRADE_DAYS
        is_bad_day = day in BAD_DAYS
        in_losing_week = (day - LOSING_WEEK_MONDAY).days in range(0, 5)

        if is_cluster:
            n = rng.randint(6, 7)
        else:
            if rng.random() < 0.15:
                continue  # no-trade day
            n = rng.choices([3, 4, 5, 6, 7, 8], weights=[12, 20, 22, 20, 16, 10])[0]

        # Most sessions close green on a high hit rate with modest winners.
        # The profit factor stays honest because a few sessions do almost all
        # of the damage, which is what a real discretionary record looks like.
        if is_cluster:
            win_p, loss_mu = 0.22, 2.3     # overtrading
        elif is_bad_day:
            win_p, loss_mu = 0.12, 3.0     # the days that cost the year
        elif in_losing_week:
            win_p, loss_mu = 0.48, 1.3     # a cold week
        else:
            win_p, loss_mu = 0.83, 0.92    # an ordinary session

        used = []
        for i in range(n):
            ticker = rng.choice([t for t in TICKERS if t not in used] or list(TICKERS))
            used.append(ticker)
            base = close_on(ticker, day)
            price = money(base * (1 + rng.uniform(-0.012, 0.012)))

            setup = rng.choices(SETUPS, weights=[30, 30, 25, 15])[0]
            side = "SHORT" if rng.random() < SETUP_SIDE_SHORT_PROB[setup] else "LONG"

            risk = rng.choice([150, 200, 250, 300])
            stop_pct = rng.uniform(0.0025, 0.007)
            stop_dist = max(0.05, round(price * stop_pct, 2))
            shares = max(5, int(round(risk / stop_dist / 5.0)) * 5)
            shares = min(shares, 2000)

            is_win = rng.random() < win_p
            if is_win:
                # In the losing week even the winners are small.
                r = (rng.uniform(0.3, 0.8) if in_losing_week
                     else min(3.0, max(0.2, rng.gauss(0.95, 0.35))))
            else:
                r = -min(loss_mu * 2.2, max(0.3, rng.gauss(loss_mu, loss_mu * 0.28)))
            gross = money(r * risk)

            move = gross / shares
            entry = price
            exit_price = money(entry + move if side == "LONG" else entry - move)
            gross = money((exit_price - entry) * shares if side == "LONG"
                          else (entry - exit_price) * shares)

            per_side_fee = min(3.0, max(0.35, 0.0035 * shares))
            commissions = money(2 * per_side_fee)
            net = money(gross - commissions)

            # entry mostly in the first 90 minutes
            if is_cluster or rng.random() < 0.75:
                entry_min = rng.randint(9 * 60 + 32, 11 * 60 + 15)
            else:
                entry_min = rng.randint(11 * 60 + 30, 14 * 60 + 45)
            hold = rng.randint(8, 55) if is_win else rng.randint(4, 35)

            n_entries = 2 if rng.random() < 0.25 else 1
            n_exits = 2 if rng.random() < 0.35 else 1
            e_times, x_times = make_times(entry_min, hold, n_entries, n_exits)

            iso = day.isoformat()
            entry_action = "BOT" if side == "LONG" else "SOLD"
            exit_action = "SOLD" if side == "LONG" else "BOT"

            execs = []
            for q, tm in zip(split_qty(shares, n_entries), e_times):
                execs.append({"date": iso, "time": tm, "action": entry_action,
                              "qty": q, "price": entry,
                              "commission": money(per_side_fee / n_entries)})
            for q, tm in zip(split_qty(shares, n_exits), x_times):
                execs.append({"date": iso, "time": tm, "action": exit_action,
                              "qty": q, "price": exit_price,
                              "commission": money(per_side_fee / n_exits)})

            seq = seq_by_day.get((iso,), 0) + 1
            seq_by_day[(iso,)] = seq
            trade_group = f"{iso}_{ticker}_STOCK_{seq}"

            account_id = rng.choices([1, 2, 3], weights=[70, 20, 10])[0]

            # excursion metrics (synthetic but internally consistent)
            realized_pct = abs(gross) / (entry * shares) * 100
            if is_win:
                eff = rng.uniform(40, 88)
                mfe = round(realized_pct / (eff / 100), 2)
                mae = round(-rng.uniform(0.03, max(0.06, stop_pct * 100 * 0.8)), 2)
            else:
                eff = None
                mfe = round(rng.uniform(0.0, 0.45), 2)
                mae = round(-realized_pct * rng.uniform(1.0, 1.25), 2)

            if is_win:
                grade = rng.choices(["A++", "A+", "A", "B", "C"],
                                    weights=[5, 15, 35, 30, 15])[0]
            else:
                grade = rng.choices(["A", "B", "C", "D", "F"],
                                    weights=[15, 30, 30, 15, 10])[0]
            if is_cluster:
                grade = rng.choices(["C", "D", "F"], weights=[30, 45, 25])[0]

            trades.append({
                "account_id": account_id, "trade_group": trade_group, "date": iso,
                "ticker": ticker, "side": side, "entry": entry, "exit": exit_price,
                "shares": shares, "gross": gross, "net": net,
                "commissions": commissions, "executions": execs,
                "setup": setup, "grade": grade,
                "mfe": mfe, "mae": mae, "eff": round(eff, 1) if eff else None,
                "risk": risk, "r": round(r, 2), "stop_dist": stop_dist,
                "is_win": is_win, "is_cluster": is_cluster,
                "in_losing_week": in_losing_week,
                "entry_time": e_times[0],
            })
    return trades


def build_diary(trades):
    """~20 short entries in a dry, honest voice, tied to real seeded days."""
    by_day = {}
    for t in trades:
        by_day.setdefault(t["date"], []).append(t)
    days = sorted(by_day)
    picked = set(days[::3])
    # make sure both cluster days are journaled
    for d in (x.isoformat() for x in OVERTRADE_DAYS):
        if d in by_day:
            picked.add(d)
    for d in days[1::3]:
        if len(picked) >= 20:
            break
        picked.add(d)
    picked = sorted(picked)[:20]

    entries = []
    for d in picked:
        day_trades = by_day[d]
        pnl = sum(t["net"] for t in day_trades)
        tk = rng.choice(day_trades)["ticker"]
        if day_trades[0]["is_cluster"]:
            text = rng.choice(DIARY_CLUSTER)
        elif pnl >= 0:
            text = rng.choice(DIARY_GREEN).format(tk=tk)
        else:
            text = rng.choice(DIARY_RED).format(tk=tk)
        analysis = {
            "diary_date": d,
            "overall_summary": text,
            "patterns_identified": (["Overtrading after a loss"] if day_trades[0]["is_cluster"]
                                    else ["Best trades come from the pre-market plan"] if pnl >= 0
                                    else ["Losses stay small when the stop is honored"]),
            "improvement_areas": (["Hard cap of 3 trades per day"] if day_trades[0]["is_cluster"]
                                  else ["Let winners run past the first target"]),
            "trade_analyses": [],
        }
        entries.append((1, d, text, json.dumps(analysis)))
    return entries


# ── write DB ──────────────────────────────────────────────────────────────────

def write_db(trades, diary):
    if DB_FILE.exists():
        if "--replace-database" not in sys.argv:
            sys.exit(f"{DB_FILE} already exists and may hold your real trades, so nothing was changed.\n"
                     "To wipe it and load the demo data instead, run again with --replace-database.")
        DB_FILE.unlink()
        for ext in ("-wal", "-shm"):
            p = Path(str(DB_FILE) + ext)
            if p.exists():
                p.unlink()
        print(f"Removed existing {DB_FILE.name}")

    database.init_db()
    conn = database.get_db()

    for name, typ, color, broker in ACCOUNTS:
        conn.execute("INSERT INTO accounts (name, type, color, broker) VALUES (?,?,?,?)",
                     (name, typ, color, broker))

    for s in SETUPS:
        conn.execute("INSERT INTO custom_setups (name, side, notes) VALUES (?,?,?)",
                     (s, None, "Sample playbook setup (demo seed)"))

    for t in trades:
        conn.execute("""
            INSERT INTO trades
                (account_id, trade_group, date, ticker, instrument_type, side,
                 gross_pnl, net_pnl, commissions, executions,
                 option_expiry, option_strike, option_type, source,
                 setup, setup_grade, setup_source, mfe_pct, mae_pct, exit_efficiency)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (t["account_id"], t["trade_group"], t["date"], t["ticker"], "STOCK",
              t["side"], t["gross"], t["net"], t["commissions"],
              json.dumps(t["executions"]), None, None, None, "demo",
              t["setup"], t["grade"], "manual", t["mfe"], t["mae"], t["eff"]))

        if rng.random() < 0.75:
            if t["is_cluster"]:
                emo = rng.choices(["frustrated", "revenge", "anxious"],
                                  weights=[45, 35, 20])[0]
            elif t["in_losing_week"] and not t["is_win"]:
                emo = rng.choices(["frustrated", "anxious", "disciplined"],
                                  weights=[40, 35, 25])[0]
            else:
                emo = rng.choices(
                    ["disciplined", "calm", "anxious", "frustrated", "overconfident"],
                    weights=[32, 32, 14, 12, 10])[0]

            mistake = None
            if t["is_cluster"]:
                mistake = rng.choice(["Overtraded", "Revenge trade", "Chased entry"])
            elif not t["is_win"] and rng.random() < 0.3:
                mistake = rng.choice(MISTAKES_POOL)

            stop_loss = money(t["entry"] - t["stop_dist"] if t["side"] == "LONG"
                              else t["entry"] + t["stop_dist"])
            conn.execute("""
                INSERT INTO trade_analysis
                    (trade_group, ticker, date, strategy, stop_loss, risk_per_trade,
                     risk_reward, r_multiple, entry_reason, exit_reason, mistakes,
                     emotional_state, idea_source, match_confidence)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (t["trade_group"], t["ticker"], t["date"], t["setup"], stop_loss,
                  t["risk"], round(rng.uniform(1.5, 3.0), 1), t["r"],
                  rng.choice(ENTRY_REASONS), rng.choice(EXIT_REASONS), mistake,
                  emo, rng.choice(IDEA_SOURCES), "manual"))

            if mistake:
                conn.execute(
                    "INSERT INTO trade_tags (trade_group, tag_type, tag_value, source) "
                    "VALUES (?,?,?,?)",
                    (t["trade_group"], "mistake", mistake, "manual"))
            conn.execute(
                "INSERT INTO trade_tags (trade_group, tag_type, tag_value, source) "
                "VALUES (?,?,?,?)",
                (t["trade_group"], "strategy", t["setup"], "manual"))

    for account_id, d, text, analysis in diary:
        conn.execute(
            "INSERT INTO diary_entries (account_id, entry_date, raw_text, ai_analysis) "
            "VALUES (?,?,?,?)",
            (account_id, d, text, analysis))

    # Day reviews for the most recent sessions, so a fresh clone shows the
    # coaching report instead of an empty page. Written from that day's own
    # trades; regenerating one in the app replaces it with a real AI review.
    for summary_date, payload in build_day_reviews(trades):
        # account_id NULL is what the app stores for "All accounts", and it is
        # what the cache lookup matches on when no account is selected.
        conn.execute(
            "INSERT INTO daily_summaries (account_id, summary_date, ai_content) "
            "VALUES (?,?,?)",
            (None, summary_date, json.dumps(payload)))

    # The backend creates these on startup, but a seeded database is also opened
    # by scripts and tests that never start the app. Without them the library
    # endpoints return 500 until the server is restarted.
    sys.path.insert(0, str(ROOT / "backend"))
    try:
        from library import init_library_tables
        init_library_tables(conn)
    except Exception as exc:            # pragma: no cover - optional at seed time
        print(f"note: could not create the library tables ({exc})")

    conn.commit()
    conn.close()



def build_day_reviews(trades, n_days=6):
    """Synthetic day reviews for the last n_days that traded.

    Everything here is derived from the seeded trades, so the narrative always
    agrees with the numbers on the page.
    """
    by_day = {}
    for t in trades:
        by_day.setdefault(t["date"], []).append(t)

    out = []
    for d in sorted(by_day)[-n_days:]:
        day = sorted(by_day[d], key=lambda t: t["executions"][0]["time"])
        net = sum(t["net"] for t in day)
        wins = [t for t in day if t["net"] > 0]
        losses = [t for t in day if t["net"] <= 0]
        wr = len(wins) / len(day) * 100

        running, peak = 0.0, 0.0
        for t in day:
            running += t["net"]
            peak = max(peak, running)
        given_back = peak - running

        best = max(day, key=lambda t: t["net"])
        worst = min(day, key=lambda t: t["net"])
        setups = sorted({t["setup"] for t in day})

        if net > 0 and given_back < abs(net) * 0.25:
            grade = "A-" if wr >= 75 else "B+"
        elif net > 0:
            grade = "B"
        else:
            grade = "C-" if len(day) <= 4 else "D"

        narrative = (
            f"{len(day)} trades for {'+' if net >= 0 else '-'}${abs(net):,.2f} on a "
            f"{wr:.0f}% hit rate. The size came from {best['ticker']} "
            f"({best['setup']}) at +${best['net']:,.2f}"
        )
        if losses:
            narrative += (
                f", and the damage from {worst['ticker']} at -${abs(worst['net']):,.2f}"
            )
        narrative += ". "
        if given_back > 50:
            narrative += (
                f"You were ${peak:,.2f} up at the session high and closed "
                f"${given_back:,.2f} below it, so the back half of the day gave back "
                f"work the front half had already done."
            )
        else:
            narrative += "You closed at or near the high of the session, which is the shape you want."

        mental = (
            "Entries stayed on the pre-market names and sizing held steady through the session."
            if wr >= 70 else
            "Hit rate slipped below your baseline. Worth checking whether the entries were planned or reactive."
        )

        strengths = [
            f"{len(wins)} of {len(day)} trades closed green, at or above your running win rate.",
            f"{best['ticker']} was held for the full move: +${best['net']:,.2f} on the {best['setup']} read.",
        ]
        if given_back < 50:
            strengths.append("Nothing was given back after the session high, which is where most days leak.")

        mistakes = []
        if losses:
            mistakes.append(
                f"{worst['ticker']} cost -${abs(worst['net']):,.2f}, "
                f"{abs(worst['net']) / (sum(t['net'] for t in wins) or 1) * 100:.0f}% of everything the winners made."
            )
        if given_back > 50:
            mistakes.append(
                f"${given_back:,.2f} was handed back between the session high and the close."
            )
        if len(day) >= 6:
            mistakes.append(f"{len(day)} trades in one session is above your own average. Check the last two for a real setup.")

        coaching = [
            "Write the stop and the target before the entry, not after it.",
            f"Your best read today was {best['setup']}. Size that one, leave the rest alone.",
        ]
        if given_back > 50:
            coaching.append("Set a give-back limit: once the day is up, stop trading it away.")

        out.append((d, {
            "overall_grade": grade,
            "narrative": narrative,
            "mental_game": mental,
            "strengths": strengths,
            "mistakes": mistakes or ["Nothing flagged on this session."],
            "coaching": coaching,
            "patterns": sorted(setups)[:4],
            "trade_grades": [
                {
                    "trade_group": t["trade_group"],
                    "ticker": t["ticker"],
                    "grade": ("A" if t["net"] > 0 and t.get("r", 0) >= 1 else
                              "B" if t["net"] > 0 else
                              "F" if t["net"] < -200 else "C"),
                    "one_line": (
                        f"{t['setup']} {'long' if t['side'] == 'LONG' else 'short'}, "
                        f"{'+' if t['net'] >= 0 else '-'}${abs(t['net']):,.2f}"
                        + (" held to the target." if t["net"] > 0 else " stopped out.")
                    ),
                }
                for t in day
            ],
        }))
    return out


# ── sample import CSV (Thinkorswim account-statement format) ─────────────────

def write_sample_csv():
    """10 execution rows on a date after the seeded range, so the on-camera
    import shows fresh trades instead of duplicates."""
    d = "8/24/26"  # the Monday after SEED_DATE
    rows = [
        (d, "09:33:05", 'BOT +200 NVDA @176.40',  "", "-0.70",  "-35280.00"),
        (d, "09:36:41", 'BOT +100 NVDA @176.15',  "", "-0.35",  "-17615.00"),
        (d, "10:02:19", 'SOLD -300 NVDA @177.35', "", "-1.05",  "53205.00"),
        (d, "09:47:52", 'BOT +150 TSLA @334.20',  "", "-0.55",  "-50130.00"),
        (d, "10:15:08", 'SOLD -75 TSLA @336.10',  "", "-0.30",  "25207.50"),
        (d, "10:31:44", 'SOLD -75 TSLA @335.40',  "", "-0.30",  "25155.00"),
        (d, "10:58:33", 'SOLD -400 AMD @167.80',  "", "-1.40",  "67120.00"),
        (d, "11:20:10", 'BOT +400 AMD @167.15',   "", "-1.40",  "-66860.00"),
        (d, "13:05:27", 'BOT +100 SPY @644.90',   "", "-0.35",  "-64490.00"),
        (d, "13:42:56", 'SOLD -100 SPY @644.15',  "", "-0.35",  "64415.00"),
    ]
    lines = [
        "Account Statement for DEMO-0001 (Day Trade) since 8/24/26 through 8/24/26",
        "",
        "Cash Balance",
        "DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE",
    ]
    balance = 100000.0
    for i, (dt, tm, desc, misc, fee, amount) in enumerate(rows, start=1):
        balance = round(balance + float(amount) + float(fee), 2)
        lines.append(f'{dt},{tm},TRD,="{3000 + i}",{desc},{misc},{fee},{amount},{balance}')
    lines.append("")
    out = ROOT / "scripts" / "sample_import.csv"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out.relative_to(ROOT)} ({len(rows)} execution rows)")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    trades = generate_trades()
    diary = build_diary(trades)
    write_db(trades, diary)
    write_sample_csv()

    wins = [t for t in trades if t["net"] > 0]
    losses = [t for t in trades if t["net"] < 0]
    avg_win = sum(t["net"] for t in wins) / len(wins)
    avg_loss = sum(t["net"] for t in losses) / len(losses)
    by_week = {}
    for t in trades:
        wk = date.fromisoformat(t["date"]).isocalendar()[1]
        by_week[wk] = by_week.get(wk, 0) + t["net"]

    print()
    print(f"Seeded {len(trades)} trades over {WEEKS} weeks ending {SEED_DATE}")
    print(f"Win rate: {len(wins) / len(trades) * 100:.1f}%")
    print(f"Avg win ${avg_win:,.2f} vs avg loss ${avg_loss:,.2f} "
          f"(ratio {abs(avg_win / avg_loss):.2f})")
    print(f"Total net P&L: ${sum(t['net'] for t in trades):,.2f}")
    print(f"Losing week (w/o {LOSING_WEEK_MONDAY}): "
          f"${by_week[LOSING_WEEK_MONDAY.isocalendar()[1]]:,.2f}")
    cluster_pnl = sum(t["net"] for t in trades if t["is_cluster"])
    cluster_n = sum(1 for t in trades if t["is_cluster"])
    print(f"Overtrading cluster: {cluster_n} trades, ${cluster_pnl:,.2f}")
    print(f"Diary entries: {len(diary)}")
    print()
    print(f"Database ready at {DB_FILE}")


if __name__ == "__main__":
    main()
