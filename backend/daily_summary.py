import json
import re
from ai_analysis import get_client, response_text, raise_if_truncated, DIARY_MAX_TOKENS

MODEL = "claude-opus-5-5"

DAILY_SUMMARY_PROMPT = """You are a professional trading coach producing an end-of-day performance review for a day trader.

You will receive structured data: the date, all trades with their analysis, the day's KPIs, and optionally a diary entry. Your job is to produce a thorough, honest coaching report in JSON format.

Rules:
- Reference actual tickers, prices, and dollar amounts — never speak in generalities when you have specifics
- trade_grades must contain exactly one entry per trade provided (use the trade_group key)
- overall_grade reflects PROCESS quality, not just P&L (a losing day with great discipline can earn a B)
- Be direct and specific. If a trade was bad, name exactly why
- Return ONLY valid JSON — no markdown fences, no explanation

Required JSON schema:
{
  "narrative": "2-3 paragraph overview of the trading day — what happened, the flow of the session, any notable moments",
  "mental_game": "1-2 sentences on the trader's psychological state and emotional arc across the day",
  "strengths": ["specific thing done well 1", "specific thing done well 2"],
  "mistakes": ["specific mistake with detail 1", "specific mistake with detail 2"],
  "coaching": ["specific actionable coaching point 1", "specific actionable coaching point 2", "specific actionable coaching point 3"],
  "trade_grades": [
    {
      "trade_group": "exact trade_group key from input",
      "ticker": "SYMBOL",
      "grade": "A|B|C|D|F",
      "one_line": "One specific sentence explaining the grade."
    }
  ],
  "overall_grade": "A+|A|A-|B+|B|B-|C+|C|C-|D|F",
  "tomorrow_focus": ["specific focus point 1", "specific focus point 2"],
  "patterns": ["pattern identified today 1", "pattern identified today 2"]
}"""


def build_daily_context(conn, date: str, account_id) -> dict:
    """Fetch all data needed to generate a daily summary."""

    # Trades with joined analysis for the date
    sql = """
        SELECT t.id, t.trade_group, t.ticker, t.side, t.instrument_type,
               t.net_pnl, t.gross_pnl, t.commissions, t.executions,
               t.option_type, t.option_strike, t.option_expiry,
               ta.strategy, ta.r_multiple, ta.stop_loss, ta.target_price,
               ta.risk_per_trade, ta.risk_reward, ta.mistakes,
               ta.emotional_state, ta.entry_reason, ta.exit_reason,
               ta.ai_feedback, ta.idea_source, ta.match_confidence
        FROM trades t
        LEFT JOIN trade_analysis ta ON t.trade_group = ta.trade_group
        WHERE t.date = ?
    """
    params = [date]
    if account_id is not None:
        sql += " AND t.account_id = ?"
        params.append(account_id)
    sql += " ORDER BY t.id"

    rows = conn.execute(sql, params).fetchall()
    trades = []
    for row in rows:
        d = dict(row)
        try:
            d['executions'] = json.loads(d.get('executions') or '[]')
        except Exception:
            d['executions'] = []
        trades.append(d)

    # Day KPIs
    all_pnl = [t.get('net_pnl') or 0 for t in trades]
    winners = [p for p in all_pnl if p > 0]
    losers = [p for p in all_pnl if p < 0]
    total_trades = len(trades)
    day_kpis = {
        "total_net_pnl": round(sum(all_pnl), 2),
        "total_trades": total_trades,
        "winning_trades": len(winners),
        "losing_trades": len(losers),
        "win_rate": round(len(winners) / total_trades * 100, 1) if total_trades else 0,
        "avg_win": round(sum(winners) / len(winners), 2) if winners else 0,
        "avg_loss": round(sum(losers) / len(losers), 2) if losers else 0,
        "profit_factor": round(sum(winners) / abs(sum(losers)), 2) if losers else None,
    }

    # All-time KPIs for context
    at_params = []
    at_sql = "SELECT net_pnl, gross_pnl FROM trades WHERE 1=1"
    if account_id is not None:
        at_sql += " AND account_id = ?"
        at_params.append(account_id)
    at_rows = conn.execute(at_sql, at_params).fetchall()
    at_all = [dict(r)['net_pnl'] or 0 for r in at_rows]
    at_wins = [p for p in at_all if p > 0]
    at_losses = [p for p in at_all if p < 0]
    at_total = len(at_all)
    alltime_kpis = {
        "win_rate": round(len(at_wins) / at_total * 100, 1) if at_total else 0,
        "avg_win": round(sum(at_wins) / len(at_wins), 2) if at_wins else 0,
        "avg_loss": round(sum(at_losses) / len(at_losses), 2) if at_losses else 0,
        "profit_factor": round(sum(at_wins) / abs(sum(at_losses)), 2) if at_losses else None,
    }

    # Diary entry for the date
    diary_row = conn.execute(
        "SELECT ai_analysis FROM diary_entries WHERE entry_date = ?" +
        (" AND account_id = ?" if account_id is not None else "") +
        " ORDER BY id DESC LIMIT 1",
        [date] + ([account_id] if account_id is not None else [])
    ).fetchone()

    diary_summary = None
    if diary_row and diary_row[0]:
        try:
            da = json.loads(diary_row[0])
            diary_summary = {
                "overall_summary": da.get("overall_summary"),
                "patterns_identified": da.get("patterns_identified", []),
                "improvement_areas": da.get("improvement_areas", []),
            }
        except Exception:
            pass

    return {
        "date": date,
        "trades": trades,
        "day_kpis": day_kpis,
        "alltime_kpis": alltime_kpis,
        "diary_summary": diary_summary,
    }


def generate_daily_summary(context: dict) -> dict:
    """Call Claude to generate a structured daily summary."""
    client = get_client()

    date = context["date"]
    trades = context["trades"]
    kpis = context["day_kpis"]
    alltime = context["alltime_kpis"]
    diary = context.get("diary_summary")

    # Format trades for the prompt
    trade_lines = []
    for t in trades:
        execs = t.get("executions", [])
        first_time = next((e.get("time", "") for e in execs), "")
        line = (
            f"  - trade_group: {t['trade_group']} | ticker: {t['ticker']} | "
            f"side: {t['side']} | net_pnl: ${t.get('net_pnl') or 0:.2f} | "
            f"strategy: {t.get('strategy') or 'N/A'} | "
            f"r_multiple: {t.get('r_multiple') or 'N/A'} | "
            f"stop_loss: {t.get('stop_loss') or 'N/A'} | "
            f"risk_per_trade: {t.get('risk_per_trade') or 'N/A'} | "
            f"entry_reason: {t.get('entry_reason') or 'N/A'} | "
            f"exit_reason: {t.get('exit_reason') or 'N/A'} | "
            f"mistakes: {t.get('mistakes') or 'none'} | "
            f"emotional_state: {t.get('emotional_state') or 'N/A'} | "
            f"first_entry_time: {first_time or 'N/A'}"
        )
        trade_lines.append(line)

    diary_section = ""
    if diary:
        diary_section = f"""
Diary entry for this date:
  Overall summary: {diary.get('overall_summary') or 'N/A'}
  Patterns: {', '.join(diary.get('patterns_identified', [])) or 'N/A'}
  Improvement areas: {', '.join(diary.get('improvement_areas', [])) or 'N/A'}"""

    user_content = f"""Date: {date}

Day KPIs:
  Net P&L: ${kpis['total_net_pnl']:.2f}
  Trades: {kpis['total_trades']} ({kpis['winning_trades']}W / {kpis['losing_trades']}L)
  Win Rate: {kpis['win_rate']}%
  Avg Win: ${kpis['avg_win']:.2f} | Avg Loss: ${kpis['avg_loss']:.2f}
  Profit Factor: {kpis['profit_factor']}

Historical averages (all-time):
  Win Rate: {alltime['win_rate']}% | Avg Win: ${alltime['avg_win']:.2f} | Avg Loss: ${alltime['avg_loss']:.2f} | Profit Factor: {alltime['profit_factor']}

Trades:
{chr(10).join(trade_lines) if trade_lines else '  No trades on this date.'}
{diary_section}

Generate the daily coaching summary JSON."""

    response = client.messages.create(
        model=MODEL,
        # 4096 was too tight: adaptive thinking spends from the same budget, so a
        # 10-trade day cut the JSON off mid-string.
        max_tokens=DIARY_MAX_TOKENS,
        output_config={"effort": "medium"},
        system=DAILY_SUMMARY_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    raise_if_truncated(response, "daily review")

    raw = response_text(response)
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)

    result = json.loads(raw)
    result.setdefault("narrative", "")
    result.setdefault("strengths", [])
    result.setdefault("mistakes", [])
    result.setdefault("coaching", [])
    result.setdefault("trade_grades", [])
    result.setdefault("overall_grade", "C")
    result.setdefault("tomorrow_focus", [])
    result.setdefault("patterns", [])
    result.setdefault("mental_game", "")
    return result
