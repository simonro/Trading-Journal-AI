"""Readers for broker exports beyond Thinkorswim, IBKR and the generic template:
Tradovate, NinjaTrader, TopstepX, Webull, tastytrade, TradeStation and Power E*TRADE.

Each reader turns the export's fill rows into the common execution shape (see
csv_parser.parse_generic_rows) and hands them to build_trades_from_executions, so
grouping, duplicate checks and P&L work exactly as for the other brokers.

Tradovate, NinjaTrader, TopstepX and Webull header names, row filters and side labels follow LuxAlgo's trade-journal importers
(packages/importers, MIT License, Copyright (c) 2026 LuxAlgo Global, LLC;
https://github.com/LuxAlgo/trade-journal), which were cross-checked against parsers
that read real exports. This is a Python rewrite, not a copy; the license notice is
in THIRD_PARTY_NOTICES.md.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Callable
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo('America/New_York')
DEFAULT_TIMEZONE = 'America/New_York'

# Zone abbreviations that exports print after a time. Webull writes "EST" all year,
# so an abbreviation names the region's zone (with its DST), not a fixed offset.
_ZONE_WORDS = {
    'ET': 'America/New_York', 'EST': 'America/New_York', 'EDT': 'America/New_York',
    'CT': 'America/Chicago', 'CST': 'America/Chicago', 'CDT': 'America/Chicago',
    'MT': 'America/Denver', 'MST': 'America/Denver', 'MDT': 'America/Denver',
    'PT': 'America/Los_Angeles', 'PST': 'America/Los_Angeles', 'PDT': 'America/Los_Angeles',
    'UTC': 'UTC', 'GMT': 'UTC', 'Z': 'UTC',
}

MONTH_CODES = 'FGHJKMNQUVXZ'


class SourceAccountsError(ValueError):
    """The file holds fills from several broker accounts; the user must pick one."""

    def __init__(self, accounts: list[str]):
        self.accounts = accounts
        super().__init__(
            f"This file has fills from {len(accounts)} accounts ({', '.join(accounts)}). "
            "Pick which one to import into this journal account."
        )


def _norm(header: str) -> str:
    return re.sub(r'[^a-z0-9]', '', header.strip().lower())


@dataclass
class Spec:
    key: str
    label: str
    required: list[list[str]]          # alias groups that must all be in the header
    symbol: list[str]
    side: list[str]
    quantity: list[str]
    price: list[str]
    timestamp: list[str]
    fees: list[list[str]] = field(default_factory=list)   # each group summed
    account: list[str] = field(default_factory=list)
    futures_only: bool = False
    forbidden: list[str] = field(default_factory=list)    # header that rules the format out
    no_fees_warning: str | None = None
    # A row is a fill only when these columns have a value (TopstepX lists cancelled
    # orders with their size, so the size alone does not mean anything was filled).
    filled_marker: list[str] = field(default_factory=list)
    # Turns one export row into a canonical row (keys below), None for a row that is not
    # a fill (cash movements, cancelled orders), or a short reason string for a row that
    # is a trade the journal cannot book yet (counted and named in a warning).
    prepare: Callable[[dict], dict | str | None] | None = None


SPECS = [
    Spec(
        key='tradovate', label='Tradovate',
        required=[['orderid'], ['bs'], ['contract'], ['filledqty'], ['filltime']],
        symbol=['contract'], side=['bs'], quantity=['filledqty'],
        price=['avgfillprice', 'avgprice'], timestamp=['filltime'],
        fees=[['commission', 'commissions'], ['fees']],
        account=['account'], futures_only=True,
        no_fees_warning="Tradovate's Orders export has no commissions, so these trades are "
                        "imported with $0 fees. Add fees on each trade if you want net P&L.",
    ),
    Spec(
        key='topstepx', label='TopstepX',
        required=[['contractname'], ['executeprice'], ['filledat']],
        symbol=['contractname'], side=['side'], quantity=['size', 'qty'],
        price=['executeprice'], timestamp=['filledat'],
        fees=[['fees', 'commissions', 'commission']],
        account=['accountname', 'account'], futures_only=True,
        filled_marker=['filledat'],
        no_fees_warning="This TopstepX export has no fee column, so these trades are imported "
                        "with $0 fees.",
    ),
    Spec(
        key='ninjatrader', label='NinjaTrader',
        required=[['instrument'], ['action'], ['price'], ['time'], ['quantity', 'qty']],
        forbidden=['date', 'symbol'],
        symbol=['instrument'], side=['action'], quantity=['quantity', 'qty'],
        price=['price'], timestamp=['time'],
        fees=[['commission']],
        account=['account', 'accountdisplayname', 'accountname'], futures_only=True,
    ),
    Spec(
        key='webull', label='Webull',
        required=[['symbol'], ['side'], ['status'], ['filled', 'filledtotalqty']],
        symbol=['symbol'], side=['side'], quantity=['filled', 'filledqty', 'filledtotalqty'],
        price=['avgprice', 'averagefillprice', 'priceavgprice'],
        timestamp=['filledtime', 'timefilled'],
        fees=[['commission', 'commissions'], ['fee', 'fees']],
    ),
]

# Readers with a prepare() step read these canonical keys.
_CANON = dict(symbol=['_symbol'], side=['_side'], quantity=['_qty'], price=['_price'],
              timestamp=['_time'], fees=[['_fee']])


_TASTY_DELIVERY_ACTIONS = {'BUY_TO_OPEN', 'SELL_TO_CLOSE', 'SELL_TO_OPEN', 'BUY_TO_CLOSE'}
_FUTURES_LIKE = re.compile(r'^/|^[A-Z0-9]{1,4}[FGHJKMNQUVXZ]\d{1,2}$')


def _tasty_prepare(rec: dict):
    kind = rec.get('type', '')
    if kind not in ('Trade', 'Receive Deliver'):
        return None                                   # Money Movement: cash, not a trade
    sub = rec.get('subtype', '')
    instrument = rec.get('instrumenttype', '')
    action = rec.get('action', '').replace('_', ' ')
    qty = rec.get('quantity', '')
    zero_price = False
    if kind == 'Receive Deliver':
        if sub == 'Expiration':
            zero_price = True                         # option removed at expiry, worth 0
        elif sub in ('Assignment', 'Exercise') and instrument == 'Equity Option':
            # The option leaves at 0; the shares come in on their own Receive Deliver row.
            action = 'BUY TO CLOSE' if sub == 'Assignment' else 'SELL TO CLOSE'
            zero_price = True
        elif (instrument == 'Equity' and sub.upper().replace(' ', '_') in _TASTY_DELIVERY_ACTIONS
              and _number(rec.get('value', ''))):
            pass                                      # the shares of an assignment or exercise
        else:
            # Corporate actions (special dividends, splits, symbol changes), transfers and
            # cash settlements carry no trade price: nothing to book as a fill.
            return f"tastytrade {sub or 'Receive Deliver'}"
    if instrument not in ('Equity', 'Equity Option'):
        return f"tastytrade {instrument or 'unknown'}"
    option = instrument == 'Equity Option'
    try:
        mult = float(rec.get('multiplier') or (100 if option else 1))
    except ValueError:
        mult = 100.0 if option else 1.0
    if zero_price:
        price = '0'
    else:
        # Value is exact; Average Price is rounded to the cent (100 AES sold for $994.10
        # shows 9.94). Value also carries the option multiplier.
        value, n = _number(rec.get('value', '')), _number(qty)
        avg = _number(rec.get('averageprice', ''))
        if value and n:
            price = repr(abs(value) / abs(n) / (mult if option else 1))
        else:
            price = '' if avg is None else repr(abs(avg) / (mult if option else 1))
    row = {'_symbol': rec.get('symbol', ''), '_side': action, '_qty': qty, '_price': price,
           '_time': rec.get('date', ''), '_fee': _fee_sum(rec.get('commissions', ''), rec.get('fees', '')),
           '_zero_ok': zero_price}
    if instrument == 'Equity Option':
        row['_option'] = _option_from_columns(rec.get('underlyingsymbol') or rec.get('rootsymbol', ''),
                                              rec.get('expirationdate', ''), rec.get('strikeprice', ''),
                                              rec.get('callorput', ''))
    return row


def _tradestation_prepare(rec: dict):
    side = rec.get('transaction', '')
    if not re.match(r'^(buy|sell)', side.strip().lower()):
        # Expired, Assigned, Exercised and other activity: counted and named, so an
        # option that expired is not left open without a word.
        return f"TradeStation {side.strip() or 'blank'}"
    if _FUTURES_LIKE.match(rec.get('symbol', '').strip().upper()):
        return "TradeStation futures"
    row = {'_symbol': rec.get('symbol', '').strip(), '_side': side, '_qty': rec.get('quantity', ''),
           '_price': rec.get('price', ''), '_fee': rec.get('commission', ''),
           # "02/02/2026 08:11:32:000": milliseconds after a colon
           '_time': re.sub(r'(\d{2}:\d{2}:\d{2}):(\d{1,3})$', r'\1.\2', rec.get('activitytime', '').strip()),
           '_account': rec.get('accountnumber', '')}
    if rec.get('callput', '').strip():
        qty, price = _number(rec.get('quantity', '')), _number(rec.get('price', ''))
        amount, fee = _number(rec.get('amount', '')), _number(rec.get('commission', '')) or 0
        if not qty or not price:
            return f"TradeStation option with quantity {rec.get('quantity', '')}"
        qty = abs(qty)
        # Option quantity is in shares in the activity file (200 = 2 contracts). The
        # Amount column (price x shares, give or take the commission) tells which.
        def near(x):
            return amount is not None and abs(abs(amount) - x) <= abs(fee) + 0.01 * x + 0.01
        if near(price * qty) and qty % 100 == 0:
            contracts = qty // 100
        elif near(price * qty * 100):
            contracts = qty
        else:
            return "TradeStation option whose quantity does not match its amount"
        row['_qty'] = str(int(contracts))
        row['_option'] = _option_from_columns(rec.get('underlyingsymbol', ''), rec.get('expiredate', ''),
                                              rec.get('strikeprice', ''), rec.get('callput', ''))
    return row


_ETRADE_DESC = re.compile(r'^(Buy to Cover|Sell Short|Buy|Sell)\s+[\d,.]+\s+(.*?)\s+@', re.I)
_ETRADE_OPTION = re.compile(r'^([A-Z][a-z]{2})-(\d{1,2})-(\d{2})\s+([\d.]+)\s+(Call|Put)s?$', re.I)


def _etrade_prepare(rec: dict):
    fill = rec.get('fill', '')
    m = re.match(r'^\s*([\d,.]+)\s*@\s*([\d,.]+)\s*$', fill)
    if not m:
        return None                                   # "--": nothing filled
    d = _ETRADE_DESC.match(rec.get('description', '').strip())
    if not d:
        return "Power E*TRADE order with an unreadable description"
    if _FUTURES_LIKE.match(rec.get('symbol', '').strip().upper()):
        return "Power E*TRADE futures"
    desc = rec.get('description', '').strip()
    row = {'_symbol': rec.get('symbol', ''), '_side': d.group(1), '_qty': m.group(1), '_price': m.group(2),
           '_time': rec.get('time', '').replace(',', ''), '_fee': '', '_account': rec.get('account', ''),
           '_closing': bool(re.search(r'\bto Close\b|^Buy to Cover', desc, re.I))}
    what = d.group(2).strip()
    o = _ETRADE_OPTION.match(what)
    if o:
        mon, day, yy, strike, cp = o.groups()
        month = MONTH_NUMBERS.get(mon.upper())
        if not month:
            return "Power E*TRADE option with an unreadable expiry"
        row['_option'] = {'ticker': rec.get('symbol', '').strip().upper(),
                          'option_expiry': f"{2000 + int(yy):04d}-{month:02d}-{int(day):02d}",
                          'option_strike': float(strike),
                          'option_type': 'CALL' if cp.lower() == 'call' else 'PUT'}
    elif what.upper() != rec.get('symbol', '').strip().upper():
        return "Power E*TRADE order that is not a stock or single option"
    return row


SPECS += [
    Spec(key='tastytrade', label='tastytrade',
         required=[['date'], ['type'], ['action'], ['instrumenttype'], ['averageprice'], ['multiplier']],
         prepare=_tasty_prepare, **_CANON),
    Spec(key='tradestation', label='TradeStation',
         required=[['transaction'], ['activitytime'], ['callput'], ['commission']],
         prepare=_tradestation_prepare, account=['_account'], **_CANON),
    Spec(key='etrade', label='Power E*TRADE',
         required=[['symbol'], ['status'], ['fill'], ['description'], ['time']],
         prepare=_etrade_prepare, account=['_account'], **_CANON,
         no_fees_warning="Power E*TRADE's Orders export has no commissions, so these trades are "
                         "imported with $0 fees. Add fees on each trade if you want net P&L."),
]
SPEC_BY_KEY = {s.key: s for s in SPECS}
LABELS = {s.key: s.label for s in SPECS}


# ── Reading the CSV ───────────────────────────────────────────────────────────

def _records(content: str, spec: 'Spec | None' = None) -> tuple[list[str], list[tuple[int, dict]]]:
    """Header (normalized) and (line number, {normalized header: value}) per data row.
    The header is the first of the opening rows that a reader recognises (Power E*TRADE
    puts a title line above it), else the first non-empty row."""
    rows = list(csv.reader(io.StringIO(content.lstrip('\ufeff'))))
    at = None
    for i, r in enumerate(rows[:15]):
        cols = [_norm(h) for h in r]
        if any(_header_matches(cols, s) for s in ([spec] if spec else SPECS)):
            at = i
            break
    if at is None:
        at = next((i for i, r in enumerate(rows) if any(c.strip() for c in r)), None)
    if at is None:
        return [], []
    header = [_norm(h) for h in rows[at]]
    out = []
    for n, cells in enumerate(rows[at + 1:], start=at + 2):
        if not any(c.strip() for c in cells):
            continue
        rec = {}
        for i, h in enumerate(header):
            if h and h not in rec:
                rec[h] = cells[i].strip() if i < len(cells) else ''
        out.append((n, rec))
    return header, out


MONTH_NUMBERS = {m: i for i, m in enumerate(
    ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'], start=1)}


def _fee_sum(*values: str) -> str:
    total = 0.0
    for v in values:
        n = _number(v) if v and v.strip() not in ('--', '-') else 0.0
        if n is None:
            return v                                  # left unreadable on purpose: reported
        total += abs(n)
    return repr(total)


def _option_from_columns(underlying: str, expiry: str, strike: str, call_put: str) -> dict | None:
    """Option contract from separate columns: 'IWM', '2/20/26' or '02/20/2026', '225.0', 'PUT'."""
    m = re.match(r'^\s*(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})\s*$', expiry or '')
    k = _number(strike or '')
    cp = (call_put or '').strip().upper()
    if not (underlying.strip() and m and k and cp in ('C', 'P', 'CALL', 'PUT')):
        return None
    mo, dd, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    year = yy if yy > 99 else 2000 + yy
    return {'ticker': underlying.strip().upper(), 'option_expiry': f"{year:04d}-{mo:02d}-{dd:02d}",
            'option_strike': k, 'option_type': 'CALL' if cp.startswith('C') else 'PUT'}


def _pick(rec: dict, aliases: list[str]) -> str:
    for a in aliases:
        v = rec.get(a)
        if v:
            return v
    return ''


def _header_matches(header: list[str], spec: Spec) -> bool:
    cols = set(header)
    return (all(any(a in cols for a in group) for group in spec.required)
            and not any(f in cols for f in spec.forbidden))


def detect(content: str) -> str | None:
    header, _ = _records(content[:20000])
    for spec in SPECS:
        if _header_matches(header, spec):
            return spec.key
    return None


def missing_columns(content: str, key: str) -> list[str]:
    header, _ = _records(content[:20000], SPEC_BY_KEY[key])
    cols = set(header)
    return ['/'.join(g) for g in SPEC_BY_KEY[key].required if not any(a in cols for a in g)]


# ── Values ────────────────────────────────────────────────────────────────────

def _number(text: str) -> float | None:
    t = text.strip().replace('$', '').replace(',', '').replace('@', '').strip()
    neg = t.startswith('(') and t.endswith(')')
    t = t.strip('()')
    if not t:
        return None
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def _side(text: str) -> str | None:
    t = re.sub(r'[_-]+', ' ', text.strip().lower())
    if re.match(r'^(buy|bot|bought|b|bid|long|buy to cover|buytocover|buy to open|buy to close)\b', t):
        return 'BOT'
    if re.match(r'^(sell|sld|sold|s|ask|short|ss|sell short|sellshort|sell to open|sell to close)\b', t):
        return 'SOLD'
    return None


_DT_FORMATS = [
    '%m/%d/%Y %H:%M:%S.%f', '%m/%d/%Y %H:%M:%S', '%m/%d/%Y %H:%M',
    '%m/%d/%Y %I:%M:%S %p', '%m/%d/%Y %I:%M %p',
    '%m/%d/%y %H:%M:%S', '%m/%d/%y %H:%M', '%m/%d/%y %I:%M:%S %p', '%m/%d/%y %I:%M %p',
    '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M',
]


_DT_FORMATS_DAYFIRST = [f.replace('%m/%d', '%d/%m') for f in _DT_FORMATS]


def slash_date_order(values: list[str]) -> str:
    """'dmy' when a slash date in the file can only be day-first (13/05/2026), else 'mdy'.
    NinjaTrader writes dates in the PC's locale; a file where every day is the 12th or
    earlier cannot be told apart and is read month-first, the US default."""
    day_first = month_first = False
    for v in values:
        m = re.match(r'^\s*(\d{1,2})/(\d{1,2})/\d{2,4}', v)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            day_first |= a > 12
            month_first |= b > 12
    if day_first and month_first:
        raise ValueError("The dates in this file mix day-first and month-first order, so they cannot be read safely.")
    return 'dmy' if day_first else 'mdy'


def parse_timestamp(text: str, zone: str, order: str = 'mdy') -> datetime | None:
    """Read a fill time and return it in US Eastern. An offset or zone word in the value
    wins; a bare time is read in `zone`. order='dmy' reads 05/11/2026 as 5 November."""
    t = text.strip()
    if not t:
        return None
    tz = None
    m = re.match(r'^(.*?)\s*\b(ET|EST|EDT|CT|CST|CDT|MT|MST|MDT|PT|PST|PDT|UTC|GMT)$', t)
    if m:
        t, tz = m.group(1).strip(), ZoneInfo(_ZONE_WORDS[m.group(2)])
    dt = None
    iso = t.replace(' ', 'T', 1) if re.match(r'^\d{4}-\d{2}-\d{2} ', t) else t
    if re.match(r'^\d{4}-\d{2}-\d{2}T', iso):
        try:
            dt = datetime.fromisoformat(iso.replace('Z', '+00:00'))
        except ValueError:
            dt = None
    if dt is None:
        # "11/05/2025 09:30:01 +00:00" or "-05:00" after the time
        off = re.match(r'^(.*?)\s*([+-]\d{2}):?(\d{2})$', t)
        offset = None
        if off and re.search(r'\d:\d', off.group(1)):
            t = off.group(1)
            sign = -1 if off.group(2).startswith('-') else 1
            offset = timezone(sign * timedelta(hours=abs(int(off.group(2))), minutes=int(off.group(3))))
        for fmt in (_DT_FORMATS_DAYFIRST if order == 'dmy' else _DT_FORMATS):
            try:
                dt = datetime.strptime(t, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            return None
        if offset is not None:
            dt = dt.replace(tzinfo=offset)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz or ZoneInfo(zone))
    return dt.astimezone(ET)


def futures_ticker(raw: str, trade_year: int) -> str | None:
    """'ESZ5', 'ESZ25', '/ESZ25', 'ES 12-25', 'CON.F.US.EP.Z25' -> '/ESZ25' (our Schwab style)."""
    s = raw.strip().upper()
    m = re.match(r'^CON\.F\.[A-Z]+\.([A-Z0-9]+)\.([FGHJKMNQUVXZ])(\d{2})$', s)
    if m:
        root = _TOPSTEPX_ROOTS.get(m.group(1), m.group(1))
        month, year = m.group(2), int(m.group(3))
        return f"/{root}{month}{year % 100:02d}"
    m = re.match(r'^/?([A-Z0-9]+)\s+(\d{1,2})-(\d{2}|\d{4})$', s)
    if m:
        month_n = int(m.group(2))
        if not 1 <= month_n <= 12:
            return None
        return f"/{m.group(1)}{MONTH_CODES[month_n - 1]}{int(m.group(3)) % 100:02d}"
    m = re.match(r'^/?([A-Z0-9]+?)([FGHJKMNQUVXZ])(\d{1,4})$', s)
    if m:
        root, month, y = m.group(1), m.group(2), m.group(3)
        if len(y) == 1:
            # One-digit year: the first year ending in that digit from last year on.
            year = trade_year - 1
            while year % 10 != int(y):
                year += 1
        elif len(y) == 2:
            year = 2000 + int(y)
        elif len(y) == 4:
            year = int(y)
        else:
            return None
        return f"/{root}{month}{year % 100:02d}"
    return None


# TopstepX contract ids use exchange product codes that differ from the usual roots.
_TOPSTEPX_ROOTS = {
    'EP': 'ES', 'ENQ': 'NQ', 'CLE': 'CL', 'MCLE': 'MCL', 'GCE': 'GC', 'SIE': 'SI',
    'NGE': 'NG', 'HGE': 'HG', 'EU6': '6E', 'JY6': '6J', 'BP6': '6B', 'DA6': '6A',
    'CA6': '6C', 'MGC': 'MGC', 'MES': 'MES', 'MNQ': 'MNQ', 'RTY': 'RTY', 'M2K': 'M2K',
    'YM': 'YM', 'MYM': 'MYM',
}


def _futures_root(ticker: str) -> str:
    m = re.match(r'^/([A-Z0-9]+?)[FGHJKMNQUVXZ]\d{2}$', ticker)
    return f"/{m.group(1)}" if m else ticker


# ── Parse ─────────────────────────────────────────────────────────────────────

def parse_rows(content: str, key: str, zone: str = DEFAULT_TIMEZONE,
               source_account: str | None = None, warnings: list | None = None,
               open_positions=None) -> list[dict]:
    """Fill rows of a known export -> execution dicts in time order. Raises ValueError
    naming every bad row; rows that are not fills (nothing filled) are left out.
    open_positions: signed sizes by position_key at the start of the file, or a function
    that takes {position_key: (date, time) of the file's first fill} and returns them."""
    from csv_parser import FUTURES_MULTIPLIERS, parse_ibkr_option_symbol

    spec = SPEC_BY_KEY[key]
    try:
        ZoneInfo(zone)
    except Exception:
        raise ValueError(f"Unknown timezone '{zone}'.")
    header, records = _records(content, spec)

    skipped_kinds: dict[str, int] = {}
    if spec.prepare:
        prepared = []
        for n, rec in records:
            out = spec.prepare(rec)
            if out is None:
                continue
            if isinstance(out, str):
                skipped_kinds[out] = skipped_kinds.get(out, 0) + 1
                continue
            prepared.append((n, out))
        records = prepared
        if skipped_kinds and warnings is not None:
            warnings.append("Not imported: " + ", ".join(f"{v} {k} row(s)" for k, v in sorted(skipped_kinds.items()))
                            + ". These rows carry no fill this reader can book (corporate actions, transfers, "
                              "cash settlements, expirations reported without a fill, futures). A position they "
                              "touch may stay open; adjust it by hand.")

    # Rows with something filled. A partly filled order that was then cancelled still
    # filled shares, so the filled quantity decides, not the status.
    fills_raw = []
    for n, rec in records:
        qty_text = _pick(rec, spec.quantity)
        if key == 'webull' and '/' in qty_text:
            qty_text = qty_text.split('/')[0]          # "Filled/Total Qty" = "5/10"
        qty = _number(qty_text) if qty_text else None
        if qty is None or abs(qty) < 1e-12:
            continue
        if spec.filled_marker and not _pick(rec, spec.filled_marker):
            continue
        fills_raw.append((n, rec, qty_text))

    # Webull lists crypto (BTCUSD, SHIBUSD) in the same export; prices like 0.000 cannot
    # be booked, and crypto is not a journal asset type yet.
    crypto = set()
    if key == 'webull':
        keep = []
        for f in fills_raw:
            sym = _pick(f[1], spec.symbol).strip().upper()
            if re.match(r'^[A-Z]{2,6}USD$', sym):
                crypto.add(sym)
            else:
                keep.append(f)
        fills_raw = keep
        if crypto and warnings is not None:
            warnings.append(f"Crypto rows were left out ({', '.join(sorted(crypto))}); the journal does not import crypto yet.")

    accounts = sorted({_pick(rec, spec.account) for _, rec, _ in fills_raw if spec.account} - {''})
    if source_account:
        if source_account not in accounts:
            raise ValueError(f"Account '{source_account}' is not in this file. "
                             f"Accounts in the file: {', '.join(accounts) or 'none'}.")
        fills_raw = [f for f in fills_raw if _pick(f[1], spec.account) == source_account]
    elif len(accounts) > 1:
        raise SourceAccountsError(accounts)

    order_of_dates = slash_date_order([_pick(rec, spec.timestamp) for _, rec, _ in fills_raw])
    if order_of_dates == 'dmy' and warnings is not None:
        warnings.append("Dates in this file are day-first (day/month/year) and were read that way.")

    problems, parsed = [], []
    for order, (n, rec, qty_text) in enumerate(fills_raw):
        why = []
        raw_symbol = _pick(rec, spec.symbol)
        action = _side(_pick(rec, spec.side))
        qty = _number(qty_text) if qty_text else None
        price_text = _pick(rec, spec.price)
        if key == 'webull' and '/' in price_text:
            price_text = price_text.split('/')[-1]      # "Price/Avg Price" = "185.00/185.50"
        price = _number(price_text) if price_text else None
        when = parse_timestamp(_pick(rec, spec.timestamp), zone, order_of_dates)

        if not raw_symbol:
            why.append("symbol is empty")
        if not action:
            why.append(f"side '{_pick(rec, spec.side)}' is not buy or sell")
        if qty is None or qty == 0:
            why.append(f"quantity '{qty_text}' is not a number above zero")
        if price is None or price < 0 or (price == 0 and not rec.get('_zero_ok')):
            why.append(f"price '{price_text}' is not a number above zero")
        if when is None:
            why.append(f"time '{_pick(rec, spec.timestamp)}' could not be read")

        fees = 0.0
        for group in spec.fees:
            v = _pick(rec, group)
            if v:
                f = _number(v)
                if f is None:
                    why.append(f"fee '{v}' is not a number")
                else:
                    fees += abs(f)

        instr, ticker, mult, option = 'STOCK', raw_symbol.strip().upper(), 1, None
        if '_option' in rec:
            option = rec['_option']
            if not option:
                why.append(f"option '{raw_symbol}' has no readable expiry, strike or call/put")
            else:
                instr, ticker, mult = 'OPTION', option['ticker'], 100
        elif raw_symbol and when:
            if spec.futures_only:
                ticker = futures_ticker(raw_symbol, when.year)
                if not ticker:
                    why.append(f"contract '{raw_symbol}' is not a futures contract this importer reads")
                else:
                    instr = 'FUTURE'
                    mult = FUTURES_MULTIPLIERS.get(_futures_root(ticker))
                    if mult is None:
                        why.append(f"no known point value for {ticker} ({_futures_root(ticker)}); "
                                   "use the generic template with a multiplier column for this contract")
            else:
                compact = re.sub(r'\s+', '', ticker)
                if re.match(r'^[A-Z.]{1,6}\d{6}[CP]\d{8}$', compact):
                    option = parse_ibkr_option_symbol(compact)
                    if not option:
                        why.append(f"option symbol '{raw_symbol}' could not be read")
                    else:
                        instr, ticker, mult = 'OPTION', option['ticker'], 100
                elif re.search(r'\d', ticker) and re.search(r'[CP]\d', ticker):
                    why.append(f"symbol '{raw_symbol}' looks like an option but is not in OCC form")

        if why:
            problems.append(f"line {n}: " + "; ".join(why))
            continue
        qty = abs(qty)
        qty = int(qty) if qty == int(qty) else round(qty, 6)
        parsed.append({'order': order, 'when': when, 'action': action, 'qty': qty, 'ticker': ticker,
                       'price': price, 'instr': instr, 'mult': mult, 'option': option,
                       'commission': fees,
                       # "Sell to Close", "Buy to Cover": the broker says this fill closes
                       'closing': bool(rec.get('_closing')
                                       or re.search(r'\b(close|cover)\b', _pick(rec, spec.side), re.I))})

    if problems:
        more = f" (and {len(problems) - 8} more)" if len(problems) > 8 else ""
        raise ValueError(f"{len(problems)} row(s) could not be read, so nothing was imported{more}. "
                         + " | ".join(problems[:8]))
    if not parsed:
        if crypto or skipped_kinds:
            return []
        raise ValueError(f"This {spec.label} file has no filled orders to import.")

    # Newest-first exports (Webull, Tradovate, NinjaTrader grids) are put in time order.
    # Fills in the same second keep their file order, reversed with the file.
    if parsed[0]['when'] > parsed[-1]['when']:
        parsed.reverse()
        for i, p in enumerate(parsed):
            p['order'] = i
    parsed.sort(key=lambda p: (p['when'], p['order']))

    if warnings is not None and spec.no_fees_warning and not any(p['commission'] for p in parsed):
        warnings.append(spec.no_fees_warning)

    executions = [_execution(p) for p in parsed]
    if callable(open_positions):
        # the journal's position for each symbol just before the file's first fill of it
        first_fill = {}
        for ex in executions:
            first_fill.setdefault(position_key(ex), (ex['date'], ex['time']))
        open_positions = open_positions(first_fill)
    if warnings is not None:
        # A fill the broker marks as closing, with no open position in the journal or
        # earlier in the file, closes something opened before the file's date range. It
        # would show as an open trade the other way round.
        position = dict(open_positions or {})
        orphans = set()
        for p, ex in zip(parsed, executions):
            k = position_key(ex)
            if p['closing'] and abs(position.get(k, 0)) < 1e-9:
                orphans.add(ex['ticker'])
            position[k] = position.get(k, 0) + (ex['qty'] if ex['action'] == 'BOT' else -ex['qty'])
        if orphans:
            names = sorted(orphans)
            warnings.append(
                f"{len(names)} symbol(s) have closing fills for positions opened before this file's "
                f"dates ({', '.join(names[:10])}{'...' if len(names) > 10 else ''}). They show as open "
                "trades; import an earlier file that covers the opening to fix them.")

    return _split_reversals(executions, open_positions or {})


def _execution(p: dict) -> dict:
    when, opt = p['when'], p['option'] or {}
    amount = p['price'] * p['qty'] * p['mult']
    if p['action'] == 'BOT':
        amount = -amount
    iso = when.strftime('%Y-%m-%d')
    return {
        'action': p['action'], 'qty': p['qty'], 'ticker': p['ticker'], 'price': p['price'],
        'instrument_type': p['instr'],
        'option_expiry': opt.get('option_expiry'), 'option_strike': opt.get('option_strike'),
        'option_type': opt.get('option_type'),
        'date': iso, 'iso_date': iso, 'time': when.strftime('%H:%M:%S'),
        'amount': round(amount, 2), 'commission': round(p['commission'], 2),
        'raw_description': f"{p['action']} {p['qty']} {p['ticker']} @{p['price']}",
        '_mult': p['mult'],
    }


def position_key(ex: dict) -> tuple:
    return (ex['ticker'], ex['instrument_type'], ex.get('option_expiry'),
            ex.get('option_strike'), ex.get('option_type'))


def _split_reversals(executions: list[dict], start: dict) -> list[dict]:
    """A single fill that flips the position (long 1, sell 2) closes one trade and opens
    the next. Split it at zero so the two trades stay apart; the fee is shared by size.
    `start` holds positions already open in the journal (signed, by position_key), so a
    flip that closes a position from an earlier import is split at the right size."""
    position: dict[tuple, float] = dict(start)
    out = []
    for ex in executions:
        k = position_key(ex)
        pos = position.get(k, 0)
        signed = ex['qty'] if ex['action'] == 'BOT' else -ex['qty']
        new = pos + signed
        if pos and new and (pos > 0) != (new > 0):
            close_qty = abs(pos)
            parts = [close_qty, ex['qty'] - close_qty]
            first_fee = round(ex['commission'] * close_qty / ex['qty'], 2)
            for i, q in enumerate(parts):
                part = dict(ex)
                part['qty'] = int(q) if q == int(q) else round(q, 6)
                part['commission'] = first_fee if i == 0 else round(ex['commission'] - first_fee, 2)
                amount = ex['price'] * q * ex['_mult']
                part['amount'] = round(-amount if ex['action'] == 'BOT' else amount, 2)
                part['raw_description'] = f"{ex['action']} {part['qty']} {ex['ticker']} @{ex['price']}"
                out.append(part)
        else:
            out.append(ex)
        position[k] = new
    for ex in out:
        ex.pop('_mult', None)
    return out
