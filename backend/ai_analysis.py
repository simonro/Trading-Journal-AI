import anthropic
import base64
import json
import os
import re
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-opus-5-5"

DIARY_SYSTEM_PROMPT = """You are an expert trading coach analyzing a trader's handwritten or typed diary entry.

Your task:
1. Read the diary screenshot carefully
2. Match each trade mentioned to the provided trade list (matched by ticker + price/time)
3. Extract structured data for each trade found
4. Return ONLY valid JSON — no markdown, no explanation, just the JSON object

## Match Confidence Rules
- **high**: ticker matches AND diary entry price is within $0.50 of avg_entry_price
- **medium**: ticker matches AND diary time is within 15 minutes of first_entry_time
- **low**: ticker matches AND it's the only trade for that ticker that day
- **ambiguous**: ticker matches but multiple trades exist for that ticker and no price/time to distinguish
- **unmatched**: ticker mentioned in diary but NOT found in the provided trade list

## Required JSON Schema
{
  "diary_date": "YYYY-MM-DD",
  "overall_summary": "2-3 sentence summary of the day and trader's mindset",
  "patterns_identified": ["pattern1", "pattern2"],
  "improvement_areas": ["area1", "area2"],
  "trade_analyses": [
    {
      "trade_group": "trade_group_key_from_context_or_null_if_unmatched",
      "match_confidence": "high|medium|low|ambiguous|unmatched",
      "match_notes": "brief explanation of why this confidence level",
      "ticker": "SYMBOL",
      "entry_price_diary": 107.67,
      "target_price": 109.50,
      "strategy": "canonical setup name if the trader named one (see Setup Vocabulary), otherwise a free-text strategy name e.g. VWAP Support, Opening Gap Momentum, Breakout",
      "stop_loss": 106.50,
      "risk_per_trade": 234.00,
      "risk_reward": 2.5,
      "r_multiple": 0.8,
      "entry_reason": "why the trader entered",
      "exit_reason": "why the trader exited",
      "mistakes": "any errors mentioned or implied, null if none",
      "emotional_state": "calm|anxious|overconfident|disciplined|frustrated|revenge",
      "idea_source": "where the trade idea came from e.g. Watchlist, Scanner, Alert, News, Social Media, Own Research — null if not mentioned",
      "notes": "any other free-form notes",
      "tags": [
        {"type": "strategy|setup|execution|mistake|emotion|outcome|source", "value": "tag text"}
      ],
      "ai_feedback": "One sentence of constructive coaching feedback."
    }
  ]
}

## Setup Vocabulary (the trader's playbook: use these EXACT names when the trader names one)

The trader's phrasing varies. When a note names one of the playbook setups, normalise
`strategy` to the canonical name on the left. Do NOT invent a setup when the note only
describes price action.

| Canonical name | The note may say |
|---|---|
| `Opening Drive` | opening drive, open drive, opening momentum, drive off the open |
| `VWAP Reclaim` | vwap reclaim, reclaim, vwap bounce, reclaimed vwap |
| `Range Break` | range break, range breakout, broke the range, box break |
| `Trend Pullback` | trend pullback, pullback, flag pullback, first pullback |

Notes are often voice-dictated, so names arrive garbled ("v-wap re-claim" for VWAP Reclaim,
"lost Diwa" for lost VWAP). Interpret phonetically and in context.

If the note describes the trade but names no setup (e.g. "gap-up fade, lost VWAP"), leave
`strategy` as that free-text description.

## Tag Type Guidelines
- strategy: one of the four canonical setup names above when named, else VWAP Support, Opening Gap Momentum, Breakout, Day to Swing, Covered Call, Fade, Reversal
- setup: Pre-market plan, Reactive trade, News catalyst, Technical level
- execution: Good entry, Early entry, Late entry, Scaled in, Scaled out, Held through stop
- mistake: Revenge trade, Overtraded, Moved stop, Sized too big, Chased entry, Broke rules
- emotion: Disciplined, Patient, Anxious, Overconfident, Frustrated, FOMO
- outcome: Winner, Loser, Breakeven, Partial exit, Full target hit
- source: Watchlist, Scanner, Alert, News, Idea from X, Own research

For typed diary notes (not images): parse each line, extract ticker/time/price/SL/target/strategy/source.
For "Source: Watchlist" → create tag {type: "source", value: "Watchlist"}.

If a field is not mentioned in the diary, use null. NEVER hallucinate data. Return only the JSON object."""



# ── Setup-name normalisation ──────────────────────────────────────────────────
# Diary notes are often voice-dictated, so setup names arrive garbled. The model
# is told to normalise these, but this is a closed vocabulary, so we also do it
# deterministically. This only canonicalises the DIARY's `strategy` text.

_SETUP_ALIASES = [
    ('Opening Drive', [
        'opening drive', 'open drive', 'opening momentum', 'drive off the open',
    ]),
    ('VWAP Reclaim', [
        'vwap reclaim', 'v-wap reclaim', 'vwap re-claim', 'vwap bounce',
        'reclaimed vwap', 'reclaim',
    ]),
    ('Range Break', [
        'range break', 'range breakout', 'box break', 'broke the range',
    ]),
    ('Trend Pullback', [
        'trend pullback', 'flag pullback', 'first pullback', 'pullback',
    ]),
]


def normalize_strategy(raw):
    """Map a dictated strategy name onto a canonical setup name.

    Returns the original string unchanged when it does not name one of the
    playbook setups. A description like "gap-up fade (lost VWAP)" is legitimate
    free text and must not be forced into a setup.
    """
    if not raw or not isinstance(raw, str):
        return raw
    t = raw.strip()
    if not t:
        return raw
    _EDGE = " .:-–—\"'"
    low = t.lower().strip(_EDGE)
    # Try the full string first, then with a leading label removed. Order matters:
    # 'setup c' must match its alias before 'setup' is stripped off the front.
    candidates = [low]
    for prefix in ('the signal is', 'setup name', 'strategy', 'signal', 'setup'):
        if low.startswith(prefix):
            candidates.append(low[len(prefix):].lstrip(_EDGE))
            break

    # longest aliases first so "vwap reclaim" wins over "reclaim"
    for cand in candidates:
        for canonical, aliases in _SETUP_ALIASES:
            for alias in sorted(aliases, key=len, reverse=True):
                if cand == alias or cand.startswith(alias + ' ') or cand.endswith(' ' + alias):
                    return canonical
    return t


def get_client() -> anthropic.Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key or api_key == "your_anthropic_api_key_here":
        raise ValueError("ANTHROPIC_API_KEY is not set in .env file")
    return anthropic.Anthropic(api_key=api_key)



def response_text(response) -> str:
    """Concatenate the text blocks of a Messages API response.

    `response.content` is a list of blocks and the FIRST one is not necessarily
    text. On Claude Opus 5.5 adaptive thinking is on by default, so content[0] is
    typically a ThinkingBlock — indexing it raises
    "'ThinkingBlock' object has no attribute 'text'". Always select by .type.
    """
    parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
    return "".join(parts).strip()


# The per-trade JSON grows with the number of diary lines, and on Opus 5.5 adaptive
# thinking spends from the SAME max_tokens budget, so a tight cap truncates the
# JSON mid-string and json.loads() reports a meaningless column number instead of
# the real problem. Sized for a full day of trades plus thinking.
DIARY_MAX_TOKENS = 16000


def raise_if_truncated(response, what: str = "analysis") -> None:
    """Fail loudly when the model hit the token ceiling.

    Without this the caller parses a half-written JSON string and surfaces
    "Unterminated string starting at: line N column M", which points at the
    output rather than the cause.
    """
    if getattr(response, "stop_reason", None) == "max_tokens":
        raise ValueError(
            f"The {what} response hit the {DIARY_MAX_TOKENS}-token limit and was cut off. "
            "Split the diary into fewer trades per run, or raise DIARY_MAX_TOKENS."
        )


def analyze_diary_entry(image_path: str, entry_date: str, trades_context: list[dict]) -> dict:
    """
    Send diary screenshot + trades context to Claude for analysis.

    Args:
        image_path: absolute path to the uploaded image
        entry_date: ISO date string e.g. '2026-05-19'
        trades_context: list of trade dicts for that date with:
            trade_group, ticker, instrument_type, side, avg_entry, avg_exit,
            first_entry_time, last_exit_time, net_pnl
    Returns:
        Parsed dict with diary_date, overall_summary, patterns_identified,
        improvement_areas, trade_analyses
    """
    client = get_client()

    # Read and encode image
    image_bytes = Path(image_path).read_bytes()
    image_b64 = base64.standard_b64encode(image_bytes).decode('utf-8')

    ext = Path(image_path).suffix.lower()
    media_type_map = {
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.webp': 'image/webp',
        '.gif': 'image/gif',
    }
    media_type = media_type_map.get(ext, 'image/jpeg')

    # Build trades context string
    context_lines = []
    for t in trades_context:
        line = (
            f"- trade_group: {t['trade_group']} | ticker: {t['ticker']} | "
            f"instrument: {t['instrument_type']} | side: {t['side']} | "
            f"avg_entry: ${t.get('avg_entry', 'N/A')} | avg_exit: ${t.get('avg_exit', 'N/A')} | "
            f"first_entry_time: {t.get('first_entry_time', 'N/A')} | "
            f"last_exit_time: {t.get('last_exit_time', 'N/A')} | "
            f"net_pnl: ${t.get('net_pnl', 0):.2f}"
        )
        context_lines.append(line)

    trades_context_str = '\n'.join(context_lines) if context_lines else "No trades found for this date."

    user_text = f"""Entry date: {entry_date}

Trades executed on this date (use these to match diary mentions):
{trades_context_str}

Please analyze this trading diary screenshot. For each trade you find mentioned:
1. Match it to the best trade_group from the list above using ticker + price/time
2. Extract all fields per the schema
3. Generate appropriate tags
4. Provide one sentence of coaching feedback

Return only the JSON object."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=DIARY_MAX_TOKENS,
        system=DIARY_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": user_text,
                    }
                ],
            }
        ],
    )

    raise_if_truncated(response, "diary photo analysis")
    return _parse_response(response_text(response), entry_date)


def analyze_diary_text(text_content: str, entry_date: str, trades_context: list[dict]) -> dict:
    """Analyze typed/CSV diary notes (no image) using Claude text API."""
    client = get_client()

    context_lines = []
    for t in trades_context:
        line = (
            f"- trade_group: {t['trade_group']} | ticker: {t['ticker']} | "
            f"side: {t['side']} | avg_entry: ${t.get('avg_entry', 'N/A')} | "
            f"avg_exit: ${t.get('avg_exit', 'N/A')} | "
            f"first_entry_time: {t.get('first_entry_time', 'N/A')} | "
            f"net_pnl: ${t.get('net_pnl', 0):.2f}"
        )
        context_lines.append(line)
    trades_context_str = '\n'.join(context_lines) if context_lines else "No trades found for this date."

    response = client.messages.create(
        model=MODEL,
        max_tokens=DIARY_MAX_TOKENS,
        system=DIARY_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"""Entry date: {entry_date}

Trades executed on this date (match diary lines to these):
{trades_context_str}

Typed diary notes to analyze:
{text_content}

Parse each diary line, match to the trade records above, and return the JSON analysis.
For each "Source: X" note create a tag with type "source". Return only the JSON object."""
        }],
    )

    raise_if_truncated(response, "diary analysis")
    raw = response_text(response)
    if raw.startswith('```'):
        raw = re.sub(r'^```(?:json)?\n?', '', raw)
        raw = re.sub(r'\n?```$', '', raw)

    result = json.loads(raw)
    result.setdefault('diary_date', entry_date)
    result.setdefault('overall_summary', '')
    result.setdefault('patterns_identified', [])
    result.setdefault('improvement_areas', [])
    result.setdefault('trade_analyses', [])
    return result


def _parse_response(raw_text: str, entry_date: str) -> dict:
    if raw_text.startswith('```'):
        raw_text = re.sub(r'^```(?:json)?\n?', '', raw_text)
        raw_text = re.sub(r'\n?```$', '', raw_text)
    result = json.loads(raw_text)
    result.setdefault('diary_date', entry_date)
    result.setdefault('overall_summary', '')
    result.setdefault('patterns_identified', [])
    result.setdefault('improvement_areas', [])
    result.setdefault('trade_analyses', [])
    return result


def save_analysis_to_db(conn, diary_entry_id: int, analysis: dict):
    """
    Persist trade_analysis rows and trade_tags from Claude's response.
    Uses INSERT OR REPLACE so re-uploading a diary updates existing analysis.
    """
    for ta in analysis.get('trade_analyses', []):
        trade_group = ta.get('trade_group')
        if not trade_group:
            continue

        ta['strategy'] = normalize_strategy(ta.get('strategy'))

        # Upsert trade_analysis (includes target_price)
        conn.execute("""
            INSERT INTO trade_analysis
                (trade_group, ticker, date, strategy, stop_loss, target_price,
                 risk_per_trade, risk_reward, r_multiple, entry_reason, exit_reason,
                 mistakes, emotional_state, notes, ai_feedback,
                 match_confidence, match_notes, diary_entry_id, idea_source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(trade_group) DO UPDATE SET
                strategy=excluded.strategy,
                stop_loss=excluded.stop_loss,
                target_price=excluded.target_price,
                risk_per_trade=excluded.risk_per_trade,
                risk_reward=excluded.risk_reward,
                r_multiple=excluded.r_multiple,
                entry_reason=excluded.entry_reason,
                exit_reason=excluded.exit_reason,
                mistakes=excluded.mistakes,
                emotional_state=excluded.emotional_state,
                notes=excluded.notes,
                ai_feedback=excluded.ai_feedback,
                match_confidence=excluded.match_confidence,
                match_notes=excluded.match_notes,
                diary_entry_id=excluded.diary_entry_id,
                idea_source=excluded.idea_source
        """, (
            trade_group,
            ta.get('ticker', ''),
            analysis.get('diary_date', ''),
            ta.get('strategy'),
            ta.get('stop_loss'),
            ta.get('target_price'),
            ta.get('risk_per_trade'),
            ta.get('risk_reward'),
            ta.get('r_multiple'),
            ta.get('entry_reason'),
            ta.get('exit_reason'),
            ta.get('mistakes'),
            ta.get('emotional_state'),
            ta.get('notes'),
            ta.get('ai_feedback'),
            ta.get('match_confidence'),
            ta.get('match_notes'),
            diary_entry_id,
            ta.get('idea_source'),
        ))

        # Clear and reinsert tags for this trade_group (AI source only)
        conn.execute(
            "DELETE FROM trade_tags WHERE trade_group = ? AND source = 'ai'",
            (trade_group,)
        )
        for tag in ta.get('tags', []):
            if tag.get('type') and tag.get('value'):
                conn.execute(
                    "INSERT INTO trade_tags (trade_group, tag_type, tag_value, source) VALUES (?,?,?,?)",
                    (trade_group, tag['type'], tag['value'], 'ai')
                )

    conn.commit()


def build_trades_context(conn, entry_date: str, account_id: int) -> list[dict]:
    """
    Fetch all trades for a given date and account, compute avg entry/exit prices
    from executions JSON for Claude's context.
    """
    cursor = conn.execute(
        "SELECT * FROM trades WHERE date = ? AND account_id = ?",
        (entry_date, account_id)
    )
    rows = cursor.fetchall()

    context = []
    for row in rows:
        trade = dict(row)
        execs = json.loads(trade.get('executions') or '[]')

        buy_fills = [e for e in execs if e.get('action') == 'BOT']
        sell_fills = [e for e in execs if e.get('action') == 'SOLD']

        avg_entry = None
        avg_exit = None
        first_entry_time = None
        last_exit_time = None

        if buy_fills:
            total_qty = sum(e.get('qty', 0) for e in buy_fills)
            if total_qty > 0:
                avg_entry = round(
                    sum(e.get('qty', 0) * e.get('price', 0) for e in buy_fills) / total_qty, 2
                )
            times = [e.get('time', '') for e in buy_fills if e.get('time')]
            if times:
                first_entry_time = min(times)

        if sell_fills:
            total_qty = sum(e.get('qty', 0) for e in sell_fills)
            if total_qty > 0:
                avg_exit = round(
                    sum(e.get('qty', 0) * e.get('price', 0) for e in sell_fills) / total_qty, 2
                )
            times = [e.get('time', '') for e in sell_fills if e.get('time')]
            if times:
                last_exit_time = max(times)

        # For SHORT trades, avg_entry comes from SOLD fills and avg_exit from BOT fills
        if trade.get('side') == 'SHORT':
            avg_entry, avg_exit = avg_exit, avg_entry
            first_entry_time, last_exit_time = last_exit_time, first_entry_time

        context.append({
            'trade_group': trade['trade_group'],
            'ticker': trade['ticker'],
            'instrument_type': trade['instrument_type'],
            'side': trade['side'],
            'avg_entry': avg_entry,
            'avg_exit': avg_exit,
            'first_entry_time': first_entry_time,
            'last_exit_time': last_exit_time,
            'net_pnl': trade.get('net_pnl', 0),
        })

    return context


INSIGHTS_PROMPT = """You are a professional trading coach. Analyze the following trading performance data and provide actionable insights.

Return your analysis in clear markdown with these sections:
## Performance Summary
## Best Strategy
## Areas to Improve
## Top 3 Action Items
## Risk Management Assessment

Be specific, data-driven, and constructive. Focus on patterns and improvements."""


def generate_insights(trades_summary: dict) -> str:
    """
    Generate AI coaching insights from aggregated performance data.
    Returns markdown-formatted text.
    """
    client = get_client()

    summary_text = json.dumps(trades_summary, indent=2)

    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        messages=[
            {
                "role": "user",
                "content": f"{INSIGHTS_PROMPT}\n\nPerformance Data:\n```json\n{summary_text}\n```"
            }
        ],
    )

    return response_text(response)


# ── Brain AI Chatbot ────────────────────────────────────────────────────────────

BRAIN_SYSTEM_PROMPT = """You are "Brain", an expert AI trading coach embedded in a personal trading journal.
You have access to the trader's complete trading history, diary entries, and performance data.

Your capabilities:
- Answer specific questions about performance with data references
- Identify patterns in strategy, timing, emotional state, and risk management
- Generate custom reports and breakdowns on demand
- Provide actionable, data-driven coaching feedback

Format responses in markdown. Be concise, specific, and reference actual numbers from the data.
If the data doesn't support a conclusion, say so — never fabricate numbers."""


def build_brain_context(conn, account_id) -> str:
    """Build a compact trading context string for Brain's system prompt."""
    rows = conn.execute("""
        SELECT t.date, t.ticker, t.side, t.instrument_type, t.net_pnl, t.gross_pnl,
               ta.strategy, ta.r_multiple, ta.emotional_state, ta.mistakes,
               ta.stop_loss, ta.target_price
        FROM trades t
        LEFT JOIN trade_analysis ta ON t.trade_group = ta.trade_group
        WHERE (? IS NULL OR t.account_id = ?)
        ORDER BY t.date DESC, t.id DESC
        LIMIT 300
    """, (account_id, account_id)).fetchall()

    trades = [dict(r) for r in rows]
    if not trades:
        return "No trade data available."

    total_pnl = sum(t['net_pnl'] or 0 for t in trades)
    wins = [t for t in trades if (t['net_pnl'] or 0) > 0]
    losses = [t for t in trades if (t['net_pnl'] or 0) < 0]
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    avg_win = sum(t['net_pnl'] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t['net_pnl'] for t in losses) / len(losses) if losses else 0
    gross_wins = sum(t['net_pnl'] for t in wins)
    gross_losses = abs(sum(t['net_pnl'] for t in losses))
    profit_factor = round(gross_wins / gross_losses, 2) if gross_losses else 'N/A'

    # Strategy breakdown
    strat: dict = {}
    for t in trades:
        s = t['strategy'] or 'No Strategy'
        if s not in strat:
            strat[s] = {'count': 0, 'wins': 0, 'pnl': 0.0}
        strat[s]['count'] += 1
        if (t['net_pnl'] or 0) > 0:
            strat[s]['wins'] += 1
        strat[s]['pnl'] += t['net_pnl'] or 0

    strat_lines = []
    for s, st in sorted(strat.items(), key=lambda x: -x[1]['pnl']):
        wr = st['wins'] / st['count'] * 100 if st['count'] else 0
        strat_lines.append(
            f"  {s}: {st['count']} trades | {wr:.0f}% win rate | ${st['pnl']:.2f} total P&L | ${st['pnl']/st['count']:.2f} avg"
        )

    dates = sorted(set(t['date'] for t in trades if t['date']))
    date_range = f"{dates[0]} to {dates[-1]}" if dates else "N/A"

    trade_lines = []
    for t in trades[:50]:
        line = f"  {t['date']} | {t['ticker']} | {t['side']} | ${t['net_pnl']:.2f}"
        if t['strategy']:
            line += f" | {t['strategy']}"
        if t['r_multiple'] is not None:
            line += f" | {t['r_multiple']:.2f}R"
        if t['emotional_state']:
            line += f" | {t['emotional_state']}"
        if t['mistakes']:
            line += f" | MISTAKE: {t['mistakes']}"
        trade_lines.append(line)

    # Diary summaries
    diary_rows = conn.execute("""
        SELECT entry_date, ai_analysis FROM diary_entries
        WHERE (? IS NULL OR account_id = ?) AND ai_analysis IS NOT NULL
        ORDER BY entry_date DESC LIMIT 10
    """, (account_id, account_id)).fetchall()

    diary_lines = []
    for d in diary_rows:
        try:
            a = json.loads(dict(d)['ai_analysis'])
            summary = a.get('overall_summary', '')
            patterns = a.get('patterns_identified', [])
            if summary:
                diary_lines.append(f"  {dict(d)['entry_date']}: {summary}")
                if patterns:
                    diary_lines.append(f"    Patterns: {', '.join(patterns[:3])}")
        except Exception:
            pass

    return f"""=== ACCOUNT PERFORMANCE ===
Period: {date_range}
Total Trades: {len(trades)} | Win Rate: {win_rate:.1f}% | Net P&L: ${total_pnl:.2f}
Avg Win: ${avg_win:.2f} | Avg Loss: ${avg_loss:.2f} | Profit Factor: {profit_factor}

=== STRATEGY BREAKDOWN ===
{chr(10).join(strat_lines) or '  No strategy data'}

=== RECENT TRADES (newest first, up to 50) ===
{chr(10).join(trade_lines) or '  No trades'}

=== DIARY INSIGHTS ===
{chr(10).join(diary_lines) or '  No diary entries'}"""


WEEKLY_SUMMARY_PROMPT = """You are a professional trading coach producing a week-in-review.

Look across the ENTIRE week's data and identify PATTERNS only visible at the weekly scale.
Focus on BEHAVIORAL patterns across multiple days — not per-trade grading.

Rules:
- Reference actual tickers, dollar amounts, and frequencies when you have them
- anchor_mistake is the single most repeated behavioral failure of the week
- weekly_edge is the single most consistent thing that worked
- next_week_rule is ONE specific, actionable rule to apply next week
- Return ONLY valid JSON, no markdown fences

Required JSON schema:
{
  "week_narrative": "2-3 sentence synthesis of the week — themes, consistency, what changed day to day",
  "behavioral_patterns": ["pattern observed across multiple days 1", "pattern 2", "pattern 3"],
  "anchor_mistake": "The single most repeated mistake this week, with specific evidence",
  "weekly_edge": "The single most consistent edge or strength across the week",
  "next_week_rule": "One specific behavioral rule to apply next week",
  "emotion_trend": "How emotional state evolved across the week — was there a pattern?",
  "metrics_summary": {
    "total_trades": 0,
    "total_pnl": 0,
    "win_rate": 0,
    "best_day": "",
    "worst_day": ""
  }
}"""


def generate_weekly_summary(week_context: dict) -> dict:
    """Call Claude to generate a weekly behavioral synthesis."""
    client = get_client()

    trades = week_context["trades"]
    week_label = week_context["week_label"]

    by_day: dict[str, list] = {}
    for t in trades:
        d = t.get("date", "")
        by_day.setdefault(d, []).append(t)

    day_sections = []
    for day in sorted(by_day.keys()):
        day_trades = by_day[day]
        day_pnl = sum(t.get("net_pnl") or 0 for t in day_trades)
        lines = [f"--- {day} (${day_pnl:+.2f}, {len(day_trades)} trades) ---"]
        for t in day_trades:
            line = f"  {t.get('ticker', '')} {t.get('side', '')} ${t.get('net_pnl') or 0:.2f}"
            if t.get("strategy"):
                line += f" | {t['strategy']}"
            if t.get("r_multiple") is not None:
                line += f" | {t['r_multiple']:.2f}R"
            if t.get("emotional_state"):
                line += f" | {t['emotional_state']}"
            if t.get("mistakes"):
                line += f" | MISTAKE: {t['mistakes']}"
            lines.append(line)
        day_sections.append("\n".join(lines))

    all_pnl = [t.get("net_pnl") or 0 for t in trades]
    wins = [p for p in all_pnl if p > 0]
    losses = [p for p in all_pnl if p < 0]
    total = len(all_pnl)

    wr_str = f"{len(wins)/total*100:.1f}%" if total else "N/A"
    avg_win_str = f"${sum(wins)/len(wins):.2f}" if wins else "N/A"
    avg_loss_str = f"${sum(losses)/len(losses):.2f}" if losses else "N/A"

    user_content = (
        f"Week: {week_label}\n"
        f"Total P&L: ${sum(all_pnl):.2f} | Trades: {total} | "
        f"Win Rate: {wr_str} | Avg Win: {avg_win_str} | Avg Loss: {avg_loss_str}\n\n"
        "Trading data by day:\n"
        + "\n\n".join(day_sections)
        + "\n\nGenerate the weekly behavioral synthesis JSON."
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=WEEKLY_SUMMARY_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )

    raw = response_text(response)
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)

    result = json.loads(raw)
    for key in ("week_narrative", "behavioral_patterns", "anchor_mistake", "weekly_edge", "next_week_rule", "emotion_trend", "metrics_summary"):
        result.setdefault(key, "" if key != "behavioral_patterns" else [])
    return result


def generate_brain_response(messages: list[dict], context: str) -> str:
    """Send full conversation history + trade context to Claude Brain."""
    client = get_client()

    claude_messages = []
    context_injected = False
    for msg in messages:
        role = msg.get('role', 'user')
        content = msg.get('content', '')
        if role == 'user' and not context_injected:
            content = f"[Trading data]\n{context}\n\n[Question]\n{content}"
            context_injected = True
        claude_messages.append({"role": role, "content": content})

    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=BRAIN_SYSTEM_PROMPT,
        messages=claude_messages,
    )
    return response_text(response)
