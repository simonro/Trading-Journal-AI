import re
import json
from collections import Counter

import broker_formats
import csv
import io
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except ImportError:  # shouldn't happen — the app requires Python 3.11+
    ZoneInfo = None


MONTH_MAP = {
    'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4,
    'MAY': 5, 'JUN': 6, 'JUL': 7, 'AUG': 8,
    'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12
}

# Futures multipliers ($ per point)
FUTURES_MULTIPLIERS = {
    # Equity index
    '/ES': 50, '/MES': 5, '/NQ': 20, '/MNQ': 2,
    '/YM': 5, '/MYM': 0.5, '/RTY': 50, '/M2K': 5, '/EMD': 100,
    # Energy
    '/CL': 1000, '/MCL': 100, '/QM': 500, '/NG': 10000, '/QG': 2500,
    '/RB': 42000, '/HO': 42000,
    # Metals
    '/GC': 100, '/MGC': 10, '/SI': 5000, '/SIL': 1000, '/HG': 25000, '/PL': 50,
    # Rates
    '/ZB': 1000, '/UB': 1000, '/ZN': 1000, '/ZF': 1000, '/ZT': 2000,
    # Currencies
    '/6E': 125000, '/M6E': 12500, '/6J': 12500000, '/6B': 62500, '/6A': 100000,
    '/6C': 100000,
    # Grains
    '/ZC': 50, '/ZS': 50, '/ZW': 50,
    # Crypto
    '/BTC': 5, '/MBT': 0.1, '/ETH': 50, '/MET': 0.1,
}


def normalize_date(date_str: str) -> str:
    """Convert M/D/YY or M/D/YYYY to YYYY-MM-DD."""
    parts = date_str.strip().split('/')
    if len(parts) != 3:
        return date_str
    m, d, y = parts
    if len(y) == 2:
        y = '20' + y
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"


# Thinkorswim's Account Statement export stamps every execution using the
# desktop app's local system clock, not exchange time. A trader outside the
# US gets fill times in their own timezone, while everything downstream — chart candles from Alpaca, the
# hold-time math, session/day bucketing — assumes Eastern time, matching
# the exchange. Converting once here, at parse time, keeps every timestamp
# that reaches the database already in ET, so the rest of the app never
# needs to know a conversion happened. The default is exchange time itself,
# a no-op, so only users who pick another zone in Settings > General get
# their fills shifted.
IMPORT_LOCAL_TZ = 'America/New_York'
EXCHANGE_TZ = 'America/New_York'


def _to_exchange_time(date_str: str, time_str: str, from_tz: str = IMPORT_LOCAL_TZ) -> tuple[str, str]:
    """Convert a 'YYYY-MM-DD' + 'HH:MM[:SS]' pair from from_tz to exchange
    (Eastern) time. Returns (iso_date, time_str), both possibly shifted to
    the previous or next calendar day if the conversion crosses midnight.
    Falls back to the unconverted input on any parsing failure, so one bad
    row can't crash the whole import."""
    if not date_str or not time_str or ZoneInfo is None:
        return date_str, time_str
    naive = None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            naive = datetime.strptime(f"{date_str} {time_str}", fmt)
            break
        except ValueError:
            continue
    if naive is None:
        return date_str, time_str
    try:
        localized = naive.replace(tzinfo=ZoneInfo(from_tz))
        eastern = localized.astimezone(ZoneInfo(EXCHANGE_TZ))
    except Exception:
        return date_str, time_str
    return eastern.strftime('%Y-%m-%d'), eastern.strftime('%H:%M:%S')


def _strike_label(strike) -> str:
    return str(int(strike)) if strike and float(strike) == int(float(strike)) else str(strike or 0)


def trade_group_key(date_str: str, ticker: str, instrument_type: str, seq: int,
                    option_expiry: str | None = None, option_strike: float | None = None,
                    option_type: str | None = None, option_strike2: float | None = None) -> str:
    """Build trade group key. Options include contract details to avoid collisions.
    A vertical carries both strikes ('635-630'), so two verticals sharing one strike never collide."""
    base = f"{date_str}_{ticker}_{instrument_type}"
    if instrument_type == 'OPTION' and option_expiry:
        strike = _strike_label(option_strike)
        if option_strike2:
            strike += f"-{_strike_label(option_strike2)}"
        base += f"_{option_expiry}_{strike}_{option_type or ''}"
    return f"{base}_{seq}"


def parse_option_description(description: str) -> dict | None:
    """
    Parse option descriptions including vertical spreads:
    'SOLD -4 AMD 100 (Weeklys) 22 MAY 26 430 CALL @9.50 CBOE'
    'BOT +2 TSLA 100 21 MAR 25 250 PUT @3.40'
    'BOT +1 VERTICAL MSFT 100 (Weeklys) 27 FEB 26 400/405 CALL @1.85 CBOE'
    """
    DATE_PART = (r'.*?(\d{1,2})\s+(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(\d{2})')

    # Vertical spread: ticker follows "VERTICAL", strikes are "400/405" (first listed, second listed).
    # Booked as one fill at the net price; option_strike2 keeps verticals that share a strike apart.
    vertical = re.compile(
        r'^(BOT|SOLD)\s+[+-]?(\d+)\s+VERTICAL\s+([A-Z][A-Z0-9.]*)\s+\d+'
        + DATE_PART +
        r'\s+([\d.]+)/([\d.]+)\s+(CALL|PUT)\s+@([\d.]+)',
        re.IGNORECASE
    )
    m = vertical.match(description.strip())
    if m:
        action, qty, ticker, day, month_str, year_2d, first_strike, second_strike, opt_type, price = m.groups()
        year = 2000 + int(year_2d)
        month = MONTH_MAP[month_str.upper()]
        expiry = f"{year:04d}-{month:02d}-{int(day):02d}"
        return {
            'action': action.upper(),
            'qty': int(qty),
            'ticker': ticker.upper(),
            'price': float(price),
            'instrument_type': 'OPTION',
            'option_expiry': expiry,
            'option_strike': float(first_strike),
            'option_strike2': float(second_strike),
            'option_type': opt_type.upper(),
        }

    # Single-leg option
    single = re.compile(
        r'^(BOT|SOLD)\s+[+-]?(\d+)\s+([A-Z]+)\s+100'
        + DATE_PART +
        r'\s+([\d.]+)\s+(CALL|PUT)\s+@([\d.]+)',
        re.IGNORECASE
    )
    m = single.match(description.strip())
    if not m:
        return None

    action, qty, ticker, day, month_str, year_2d, strike, opt_type, price = m.groups()
    year = 2000 + int(year_2d)
    month = MONTH_MAP[month_str.upper()]
    expiry = f"{year:04d}-{month:02d}-{int(day):02d}"

    return {
        'action': action.upper(),
        'qty': int(qty),
        'ticker': ticker.upper(),
        'price': float(price),
        'instrument_type': 'OPTION',
        'option_expiry': expiry,
        'option_strike': float(strike),
        'option_type': opt_type.upper(),
    }


def parse_stock_description(description: str) -> dict | None:
    """
    Parse stock descriptions like:
    'BOT +400 INTC @107.67'
    'SOLD -397 INTC @106.93'
    """
    pattern = re.compile(
        r'^(BOT|SOLD)\s+[+-]?(\d[\d,]*)\s+([A-Z]+(?:\.[A-Z]+)?)\s+@([\d.]+)',
        re.IGNORECASE
    )
    m = pattern.match(description.strip())
    if not m:
        return None

    action, qty_str, ticker, price = m.groups()
    qty = int(qty_str.replace(',', ''))

    return {
        'action': action.upper(),
        'qty': qty,
        'ticker': ticker.upper(),
        'price': float(price),
        'instrument_type': 'STOCK',
        'option_expiry': None,
        'option_strike': None,
        'option_type': None,
    }


def parse_futures_description(description: str) -> dict | None:
    """
    Parse futures descriptions like:
    'BOT 1 /ES @5200.00'
    'SOLD -2 /NQH26 @19500.00'
    """
    pattern = re.compile(
        r'^(BOT|SOLD|BUY|SELL)\s+[+-]?(\d+)\s+(\/[A-Z]+[A-Z0-9]*)(?::[A-Z0-9]+)?\s+@([\d.]+)',
        re.IGNORECASE
    )
    m = pattern.match(description.strip())
    if not m:
        return None

    action, qty, ticker, price = m.groups()
    # Normalize futures action
    action_map = {'BUY': 'BOT', 'SELL': 'SOLD'}
    action = action_map.get(action.upper(), action.upper())

    return {
        'action': action,
        'qty': int(qty),
        'ticker': ticker.upper(),
        'price': float(price),
        'instrument_type': 'FUTURE',
        'option_expiry': None,
        'option_strike': None,
        'option_type': None,
    }


def parse_cash_description(description: str) -> dict | None:
    """Try option first (more specific), then stock."""
    result = parse_option_description(description)
    if result:
        return result
    return parse_stock_description(description)


def clean_amount(value: str) -> float:
    """Convert '$1,234.56' or '-1,234.56' or '($1,234.56)' to float."""
    if not value or not value.strip():
        return 0.0
    s = value.strip().replace('$', '').replace(',', '').replace('"', '')
    if s.startswith('(') and s.endswith(')'):
        s = '-' + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return 0.0


def split_csv_sections(content: str) -> dict[str, list[list[str]]]:
    """
    Split Thinkorswim CSV into named sections.
    Returns dict: section_name -> list of rows (each row is list of strings).
    Sections are separated by blank lines + new header row.
    """
    sections = {}
    current_name = None
    current_rows = []

    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Blank line = potential section boundary. Excel-saved exports pad an
        # empty row with commas out to the column count, so a line that is
        # nothing but commas counts as blank too.
        if not line or not line.replace(',', '').strip():
            if current_name and current_rows:
                sections[current_name] = current_rows
            current_name = None
            current_rows = []
            i += 1
            continue

        # Detect section headers (lines that contain known header keywords but no data-like content)
        # Section name lines: "Cash Balance", "Futures Statements", "Account Order History", etc.
        if current_name is None:
            # This line could be a section title or a header row
            # Look ahead: if the NEXT non-blank line looks like a CSV header, treat this as section name
            # Actually Thinkorswim format: section title on its own line, then header row, then data
            # e.g.:
            #   "Cash Balance"
            #   "DATE,TIME,TYPE,REF #,..."
            #   data rows...
            # OR the section header IS the first line and the column headers follow
            # We identify sections by looking for lines that are NOT comma-separated data

            # Check if this looks like a section title (few commas, no @ signs, not a data row).
            # Ignore trailing empty fields (Excel padding) so a title like
            # "Cash Balance,,,,,,,," still counts as zero real commas.
            comma_count = line.rstrip(',').count(',')
            if comma_count <= 2 and not line.startswith('"5/') and not line.startswith('5/'):
                # Likely a section title
                current_name = line.strip('"').strip()
                i += 1
                continue
            else:
                # It's a header row for an unnamed section
                current_name = 'unknown'

        # Parse as CSV row
        try:
            reader = csv.reader(io.StringIO(line))
            row = next(reader)
            current_rows.append(row)
        except Exception:
            pass
        i += 1

    if current_name and current_rows:
        sections[current_name] = current_rows

    return sections


def find_cash_balance_section(sections: dict) -> list[list[str]] | None:
    """Find the Cash Balance section rows."""
    for name, rows in sections.items():
        if 'cash' in name.lower() or (rows and 'DESCRIPTION' in str(rows[0])):
            return rows
    return None


def find_futures_section(sections: dict) -> list[list[str]] | None:
    """Find the Futures Statements section rows."""
    for name, rows in sections.items():
        if 'future' in name.lower():
            return rows
    return None


def find_trade_history_section(sections: dict) -> list[list[str]] | None:
    """Find the Account Trade History section (not Order History)."""
    for name, rows in sections.items():
        if 'trade history' in name.lower() and 'order' not in name.lower():
            return rows
    return None


def _parse_trade_history_expiry(exp_str: str) -> str | None:
    """Parse '22 MAY 26' → '2026-05-22'."""
    if not exp_str or not exp_str.strip():
        return None
    parts = exp_str.strip().split()
    if len(parts) != 3:
        return None
    day, month_str, year_2d = parts
    month = MONTH_MAP.get(month_str.upper())
    if not month:
        return None
    year = 2000 + int(year_2d)
    return f"{year:04d}-{month:02d}-{int(day):02d}"


def parse_trade_history_section(rows: list[list[str]], import_tz: str = IMPORT_LOCAL_TZ) -> list[dict]:
    """
    Parse Account Trade History section.
    Header: ,Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type
    Returns executions with computed amounts (price × qty × multiplier).
    """
    if not rows:
        return []

    header_idx = None
    for i, row in enumerate(rows):
        joined = ','.join(row).upper()
        if 'EXEC TIME' in joined and 'SYMBOL' in joined:
            header_idx = i
            break

    if header_idx is None:
        return []

    header = [h.strip().upper() for h in rows[header_idx]]
    col = {h: i for i, h in enumerate(header)}

    # Multi-leg orders print the first leg with the exec time and the spread type, then one
    # row per further leg with both left blank. A VERTICAL is skipped whole: Cash Balance
    # already books it as one fill at the net price, and adding its legs counted the short
    # leg on top of the net and lost the long leg, overstating the day's P&L.
    # Other multi-leg types (CALENDAR, DIAGONAL, CUSTOM) never parse in Cash Balance, so
    # every leg is taken from here, the blank rows inheriting the first leg's time.
    spread_idx = col.get('SPREAD', 2)
    net_idx = col.get('NET PRICE', 11)
    parent = None  # (spread type, date, time) of the last row that carried an exec time

    executions = []
    for row in rows[header_idx + 1:]:
        if len(row) < 7:
            parent = None
            continue

        exec_time_str = row[col.get('EXEC TIME', 1)].strip() if col.get('EXEC TIME', 1) < len(row) else ''
        spread = row[spread_idx].strip().upper() if spread_idx < len(row) else ''
        first_leg = False
        if exec_time_str and ' ' in exec_time_str:
            date_part, time_part = exec_time_str.split(' ', 1)
            parent = (spread, date_part, time_part)
            first_leg = True
        elif not exec_time_str and not spread and parent and parent[0] not in ('', 'STOCK', 'SINGLE', 'FUTURE'):
            spread, date_part, time_part = parent
        else:
            parent = None
            continue
        if spread == 'VERTICAL':
            continue
        multileg = spread not in ('', 'STOCK', 'SINGLE', 'FUTURE')

        side_str = row[col.get('SIDE', 3)].strip().upper() if col.get('SIDE', 3) < len(row) else ''
        if side_str not in ('BUY', 'SELL'):
            continue
        action = 'BOT' if side_str == 'BUY' else 'SOLD'

        qty_str = row[col.get('QTY', 4)].strip().lstrip('+-').replace(',', '') if col.get('QTY', 4) < len(row) else '0'
        try:
            qty = abs(int(qty_str))
        except ValueError:
            continue
        if qty == 0:
            continue

        symbol = row[col.get('SYMBOL', 6)].strip().upper() if col.get('SYMBOL', 6) < len(row) else ''
        if not symbol:
            continue

        exp_str    = row[col.get('EXP', 7)].strip()    if col.get('EXP', 7)    < len(row) else ''
        strike_str = row[col.get('STRIKE', 8)].strip() if col.get('STRIKE', 8) < len(row) else ''
        type_str   = row[col.get('TYPE', 9)].strip().upper() if col.get('TYPE', 9) < len(row) else 'STOCK'
        price_str  = row[col.get('PRICE', 10)].strip() if col.get('PRICE', 10) < len(row) else '0'

        try:
            # Thinkorswim sometimes prints a negative price on multi-lot fills in this
            # section (e.g. "-236.54"). A fill price is never negative; take abs()
            # so the cross-section dedup against Cash Balance matches correctly.
            price = abs(float(price_str))
        except ValueError:
            continue

        iso_date = normalize_date(date_part)
        # Shift from the machine's local timezone to exchange (Eastern) time —
        # see _to_exchange_time. date_part is kept in sync with iso_date so
        # every downstream field that reads either one sees the same, correct
        # value; a trade near midnight can legitimately move to the adjacent day.
        # date_part stays in the statement's M/D/YY form, which trade_group keys
        # are built from, so re-imports still match trades stored earlier.
        iso_date, time_part = _to_exchange_time(iso_date, time_part, import_tz)
        date_part = _raw_date(iso_date)

        if type_str in ('CALL', 'PUT'):
            instrument_type = 'OPTION'
            option_expiry = _parse_trade_history_expiry(exp_str)
            try:
                option_strike = float(strike_str)
            except ValueError:
                option_strike = None
            option_type = type_str
            multiplier = 100
        else:
            instrument_type = 'STOCK'
            option_expiry = None
            option_strike = None
            option_type = None
            multiplier = 1

        amount = price * qty * multiplier
        if action == 'BOT':
            amount = -amount

        ex = {
            'action': action,
            'qty': qty,
            'ticker': symbol,
            'price': price,
            'instrument_type': instrument_type,
            'option_expiry': option_expiry,
            'option_strike': option_strike,
            'option_type': option_type,
            'date': date_part,
            'iso_date': iso_date,
            'time': time_part,
            'amount': round(amount, 2),
            'commission': 0.0,
        }
        if multileg:
            # Cash Balance never books these legs, so they skip the cross-section dedup;
            # the first leg carries the order's net price so its fees can be matched to it.
            ex['_multileg'] = True
            if first_leg:
                try:
                    ex['_net_price'] = abs(float(row[net_idx].strip()))
                except (ValueError, IndexError):
                    pass
        executions.append(ex)

    return executions


def aggregate_executions(fills: list[dict]) -> dict:
    """
    Aggregate individual fills into a single trade group summary.
    Returns: side, gross_pnl, net_pnl, commissions, executions JSON.
    Uses AMOUNT from CSV (already signed — sells positive, buys negative).
    """
    if not fills:
        return {
            'side': 'LONG', 'gross_pnl': 0.0, 'net_pnl': 0.0,
            'commissions': 0.0, 'executions': '[]'
        }

    # Determine side from first fill
    first_action = fills[0]['action']
    side = 'SHORT' if first_action == 'SOLD' else 'LONG'

    commissions = sum(abs(f.get('commission', 0.0)) for f in fills)

    # Detect open positions: shares bought != shares sold means no realized P&L yet
    qty_bot = sum(f['qty'] for f in fills if f['action'] == 'BOT')
    qty_sold = sum(f['qty'] for f in fills if f['action'] != 'BOT')
    # Same tolerance as grouping: 0.1 + 0.2 shares bought and 0.3 sold is a closed trade.
    is_open = not _is_flat(qty_bot - qty_sold)

    if is_open:
        # Open trade: no realized gain/loss, commissions are the only real cost
        gross_pnl = 0.0
        net_pnl = 0.0
    else:
        # Closed trade: sum signed amounts (buys negative, sells positive in AMOUNT col)
        gross_pnl = sum(f.get('amount', 0.0) for f in fills)
        net_pnl = gross_pnl - commissions

    # Serialize executions (drop 'amount' internal field, keep display fields)
    execs = []
    for f in fills:
        execs.append({
            'date': f.get('iso_date', f.get('date', '')),
            'time': f.get('time', ''),
            'action': f.get('action', ''),
            'qty': f.get('qty', 0),
            'price': f.get('price', 0.0),
            'commission': f.get('commission', 0.0),
        })
        if f.get('split_qty') is not None:
            execs[-1]['split_qty'] = f['split_qty']
            execs[-1]['split_part'] = f['split_part']

    return {
        'side': side,
        'gross_pnl': round(gross_pnl, 2),
        'net_pnl': round(net_pnl, 2),
        'commissions': round(commissions, 2),
        'executions': json.dumps(execs),
    }


def parse_cash_balance_section(rows: list[list[str]], date_filter: str | None = None, import_tz: str = IMPORT_LOCAL_TZ) -> list[dict]:
    """
    Parse rows from the Cash Balance section.
    Expects header: DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE
    Returns list of execution dicts.
    """
    if not rows:
        return []

    # Find header row
    header_idx = None
    for i, row in enumerate(rows):
        if len(row) >= 5 and 'DATE' in row[0].upper() and 'DESCRIPTION' in str(row):
            header_idx = i
            break

    if header_idx is None:
        return []

    header = [h.strip().upper() for h in rows[header_idx]]

    # Map column names to indices
    col = {}
    for i, h in enumerate(header):
        col[h] = i

    executions = []
    for row in rows[header_idx + 1:]:
        if len(row) < 5:
            continue

        row_type = row[col.get('TYPE', 2)].strip() if col.get('TYPE', 2) < len(row) else ''
        if row_type != 'TRD':
            continue

        date_val = row[col.get('DATE', 0)].strip() if col.get('DATE', 0) < len(row) else ''
        time_val = row[col.get('TIME', 1)].strip() if col.get('TIME', 1) < len(row) else ''
        # Same local-to-exchange shift as Trade History, so this section's
        # iso_date still matches Trade History's for cross-section commission
        # matching (see _cross_section_key, which keys on iso_date).
        iso_date_val, time_val = _to_exchange_time(normalize_date(date_val), time_val, import_tz)
        desc = row[col.get('DESCRIPTION', 4)].strip().strip('"') if col.get('DESCRIPTION', 4) < len(row) else ''

        # Remove Excel formula wrapper: ="value"
        if desc.startswith('="') and desc.endswith('"'):
            desc = desc[2:-1]

        misc_fees_idx = col.get('MISC FEES', 5)
        comm_idx = col.get('COMMISSIONS & FEES', 6)
        amount_idx = col.get('AMOUNT', 7)

        misc_fees = clean_amount(row[misc_fees_idx]) if misc_fees_idx < len(row) else 0.0
        commission = clean_amount(row[comm_idx]) if comm_idx < len(row) else 0.0
        amount = clean_amount(row[amount_idx]) if amount_idx < len(row) else 0.0

        # Total commission = misc fees + commissions & fees
        total_commission = abs(misc_fees) + abs(commission)

        parsed = parse_cash_description(desc) or parse_multileg_fee(desc)
        if not parsed:
            continue

        parsed.update({
            'date': _raw_date(iso_date_val),
            'iso_date': iso_date_val,
            'time': time_val,
            'amount': amount,
            'commission': total_commission,
            'raw_description': desc,
        })
        executions.append(parsed)

    return executions


MULTILEG_TYPES = ('IRON CONDOR', 'IRON BUTTERFLY', 'VERT ROLL', 'DIAG ROLL', 'CAL ROLL', 'CALENDAR',
                  'DIAGONAL', 'CUSTOM', 'STRADDLE', 'STRANGLE', 'BUTTERFLY', 'CONDOR', 'COMBO',
                  'COLLAR', 'COVERED', 'BACKRATIO')


def parse_multileg_fee(description: str) -> dict | None:
    """A Cash Balance line for a multi-leg order other than a VERTICAL:
    'BOT +2 CALENDAR AAPL 100 (Weeklys) 13 MAR 26/27 FEB 26 230 PUT @3.25 CBOE'.
    Its legs are booked from Trade History; this line only carries the fees, returned as a
    '_fee_only' record matched to the order's first leg by date, ticker, quantity and net price."""
    kinds = '|'.join(t.replace(' ', r'\s+') for t in MULTILEG_TYPES)
    m = re.match(
        r'^(BOT|SOLD)\s+[+-]?(\d+)\s+(?:-?\d+(?:/-?\d+)+\s+)?(?:' + kinds + r')\s+([A-Z][A-Z0-9.]*)\s.*@([\d.]+)',
        description.strip(), re.IGNORECASE)
    if not m:
        return None
    _action, qty, ticker, price = m.groups()
    return {'_fee_only': True, 'qty': int(qty), 'ticker': ticker.upper(), 'price': float(price)}


def parse_futures_section_rows(rows: list[list[str]], import_tz: str = IMPORT_LOCAL_TZ) -> list[dict]:
    """
    Parse rows from the Futures Statements section.
    Header: Trade Date,Exec Date,Exec Time,Type,Ref #,Description,Misc Fees,Commissions & Fees,Amount,Balance
    """
    if not rows:
        return []

    header_idx = None
    for i, row in enumerate(rows):
        joined = ','.join(row).upper()
        if 'EXEC TIME' in joined or 'EXEC DATE' in joined:
            header_idx = i
            break

    if header_idx is None:
        return []

    header = [h.strip().upper() for h in rows[header_idx]]
    col = {h: i for i, h in enumerate(header)}

    executions = []
    for row in rows[header_idx + 1:]:
        if len(row) < 5:
            continue

        row_type = row[col.get('TYPE', 3)].strip() if col.get('TYPE', 3) < len(row) else ''
        if row_type not in ('TRD', 'TRADE'):
            continue

        trade_date = row[col.get('TRADE DATE', 0)].strip() if col.get('TRADE DATE', 0) < len(row) else ''
        exec_time = row[col.get('EXEC TIME', 2)].strip() if col.get('EXEC TIME', 2) < len(row) else ''
        desc = row[col.get('DESCRIPTION', 5)].strip().strip('"') if col.get('DESCRIPTION', 5) < len(row) else ''

        misc_fees_idx = col.get('MISC FEES', 6)
        comm_idx = col.get('COMMISSIONS & FEES', 7)
        amount_idx = col.get('AMOUNT', 8)

        misc_fees = clean_amount(row[misc_fees_idx]) if misc_fees_idx < len(row) else 0.0
        commission = clean_amount(row[comm_idx]) if comm_idx < len(row) else 0.0
        amount = clean_amount(row[amount_idx]) if amount_idx < len(row) else 0.0
        total_commission = abs(misc_fees) + abs(commission)

        parsed = parse_futures_description(desc)
        if not parsed:
            continue

        # Same local-to-exchange shift as the other Thinkorswim sections.
        iso_trade_date, exec_time = _to_exchange_time(normalize_date(trade_date), exec_time, import_tz)

        parsed.update({
            'date': _raw_date(iso_trade_date),
            'iso_date': iso_trade_date,
            'time': exec_time,
            'amount': amount,
            'commission': total_commission,
            'raw_description': desc,
        })
        executions.append(parsed)

    return executions


def _fmt_strike(strike) -> str:
    try:
        return f"{float(strike):g}"
    except (TypeError, ValueError):
        return ''


def _contract_id(instr, expiry, strike, opt_type, strike2=None) -> str:
    """Instrument identity for fingerprints: two option contracts (or two verticals) on one
    ticker with the same time, size and price are different fills."""
    if (instr or 'STOCK') != 'OPTION':
        return instr or 'STOCK'
    second = f"/{_fmt_strike(strike2)}" if strike2 not in (None, '') else ''
    return f"OPTION:{expiry or ''}:{_fmt_strike(strike)}{second}:{(opt_type or '').upper()}"


def _vertical_second_strike(trade_group: str):
    """A stored vertical keeps its second strike only in its name ('..._635-630_PUT_1')."""
    if not _is_vertical_group(trade_group or ''):
        return None
    strikes = trade_group.split('_')[-3]
    lead = '-' if strikes.startswith('-') else ''
    first, _, rest = strikes.lstrip('-').partition('-')
    return rest or None


def execution_fingerprint(exec_dict: dict) -> str:
    """Create a unique key for duplicate detection. Uses iso_date so it matches stored executions."""
    date = exec_dict.get('iso_date') or exec_dict.get('date', '')
    contract = _contract_id(exec_dict.get('instrument_type'), exec_dict.get('option_expiry'),
                            exec_dict.get('option_strike'), exec_dict.get('option_type'),
                            exec_dict.get('option_strike2'))
    return (f"{date}|{exec_dict.get('time','')}|{exec_dict.get('ticker','')}|{contract}"
            f"|{exec_dict.get('action','')}|{exec_dict.get('qty','')}|{exec_dict.get('price','')}")


def get_existing_fingerprints(conn, account_id: int) -> Counter:
    """Count the stored execution fingerprints of an account.
    Reads ticker from the trade row (not stored in executions JSON) to match execution_fingerprint format.
    A count, not a set: a 1-lot futures trader can have two identical fills, and one stored
    copy must not hide the second copy of a new file.
    """
    cursor = conn.execute(
        """SELECT ticker, executions, instrument_type, option_expiry, option_strike, option_type,
                  trade_group
           FROM trades WHERE account_id = ?""", (account_id,)
    )
    fingerprints = Counter()
    for row in cursor:
        ticker = row[0] or ''
        contract = _contract_id(row[2], row[3], row[4], row[5], _vertical_second_strike(row[6]))
        try:
            execs = json.loads(row[1] or '[]')
            for e in execs:
                qty = e.get('qty', '')
                if e.get('split_qty') is not None:
                    # A fill split at a reversal is one fill in the statement: count it once,
                    # at its original size.
                    if e.get('split_part') != 1:
                        continue
                    qty = e['split_qty']
                fp = f"{e.get('date','')}|{e.get('time','')}|{ticker}|{contract}|{e.get('action','')}|{qty}|{e.get('price','')}"
                fingerprints[fp] += 1
        except Exception:
            pass
    return fingerprints


def _make_group_meta(date_str: str, ticker: str, instr: str, fills: list[dict]) -> dict:
    return {
        'date': normalize_date(date_str),
        'raw_date': date_str,
        'ticker': ticker,
        'instrument_type': instr,
        'option_expiry': fills[0].get('option_expiry'),
        'option_strike': fills[0].get('option_strike'),
        'option_type': fills[0].get('option_type'),
    }


def group_executions_by_position(executions: list[dict]) -> tuple[dict, dict]:
    """
    Group executions into trades based on position open/close cycles.
    Each time a position returns to 0 shares/contracts, that completes one trade.
    Fills spanning multiple days are matched correctly (e.g., buy Monday, sell Wednesday).
    The trade date is the CLOSING fill's date so P&L is realized on the exit day.
    Multiple cycles per ticker produce separate numbered trades (_1, _2, ...).
    """
    # Sort all fills chronologically so multi-day positions process in order
    executions_sorted = sorted(
        executions,
        key=lambda ex: (ex.get('iso_date', ex['date']), ex.get('time', ''))
    )

    # Key by (ticker, instrument_type) — no date — so multi-day trades stay together.
    # Options are keyed by their specific contract to avoid mixing different strikes/expiries.
    by_ticker: dict[tuple, list[dict]] = {}
    for ex in executions_sorted:
        if ex['instrument_type'] == 'OPTION':
            k = (ex['ticker'], ex['instrument_type'],
                 ex.get('option_expiry', ''), ex.get('option_strike', ''), ex.get('option_type', ''),
                 ex.get('option_strike2') or '')
        else:
            k = (ex['ticker'], ex['instrument_type'])
        by_ticker.setdefault(k, []).append(ex)

    groups: dict[str, list[dict]] = {}
    group_meta: dict[str, dict] = {}

    for key_tuple, fills in by_ticker.items():
        ticker = key_tuple[0]
        instr = key_tuple[1]

        position = 0
        seq = 0
        current_fills: list[dict] = []

        for fill in _split_at_reversals(fills):
            if _is_flat(position):
                seq += 1
                current_fills = []
                position = 0

            qty = fill['qty']
            if fill['action'] == 'BOT':
                position += qty
            else:
                position -= qty

            current_fills.append(fill)

            if _is_flat(position):
                close_date = current_fills[-1]['date']
                key = trade_group_key(close_date, ticker, instr, seq,
                                      current_fills[0].get('option_expiry'),
                                      current_fills[0].get('option_strike'),
                                      current_fills[0].get('option_type'),
                                      current_fills[0].get('option_strike2'))
                groups[key] = list(current_fills)
                group_meta[key] = _make_group_meta(close_date, ticker, instr, current_fills)

        # Open/unclosed position — use date of the most recent fill
        if current_fills and not _is_flat(position):
            last_date = current_fills[-1]['date']
            key = trade_group_key(last_date, ticker, instr, seq,
                                  current_fills[0].get('option_expiry'),
                                  current_fills[0].get('option_strike'),
                                  current_fills[0].get('option_type'),
                                  current_fills[0].get('option_strike2'))
            groups[key] = list(current_fills)
            group_meta[key] = _make_group_meta(last_date, ticker, instr, current_fills)

    return groups, group_meta


def _split_at_reversals(fills: list[dict]) -> list[dict]:
    """One fill that flips the position (long 100, sell 200) closes one trade and opens the
    next. Split it at zero so each trade gets its own share of the size, amount and fee. The
    parts remember the original size (split_qty) so a re-import still sees one known fill."""
    out = []
    position = 0
    for fill in _rejoin_split_parts(fills):
        qty = fill['qty']
        signed = qty if fill['action'] == 'BOT' else -qty
        new = position + signed
        if (not _is_flat(position) and not _is_flat(new) and (position > 0) != (new > 0)
                and fill.get('split_qty') is None):
            close_qty = abs(position)
            for part_no, part_qty in ((1, close_qty), (2, qty - close_qty)):
                share = part_qty / qty
                part = dict(fill)
                part['qty'] = int(part_qty) if float(part_qty).is_integer() else round(part_qty, 6)
                part['amount'] = round(fill.get('amount', 0.0) * share, 2)
                part['commission'] = round(fill.get('commission', 0.0) * share, 4)
                part['split_qty'] = qty
                part['split_part'] = part_no
                out.append(part)
        else:
            out.append(fill)
        position = new
    return out


def _rejoin_split_parts(fills: list[dict]) -> list[dict]:
    """Put a stored reversal split back together when both halves are here, so the split is
    worked out again against every fill (an earlier fill imported later moves the zero point).
    A half whose partner is not part of this regroup stays as it is."""
    def ident(f):
        return (f.get('iso_date') or f.get('date'), f.get('time'), f.get('action'), f.get('price'), f.get('split_qty'))

    # First halves and second halves of the same statement line, in the order they occur:
    # the n-th first half pairs with the n-th second half, so two identical reversals in the
    # same second each keep their own pair.
    firsts: dict[tuple, list] = {}
    seconds: dict[tuple, list] = {}
    for i, f in enumerate(fills):
        if f.get('split_qty') is not None:
            (firsts if f.get('split_part') == 1 else seconds).setdefault(ident(f), []).append(i)
    partner: dict[int, int] = {}
    for key, ones in firsts.items():
        for a, b in zip(ones, seconds.get(key, [])):
            partner[a] = b
    folded = set(partner.values())
    out = []
    for i, f in enumerate(fills):
        if i in folded:
            continue  # merged into its first half
        if i not in partner:
            out.append(f)
            continue
        pair = (f, fills[partner[i]])
        whole = {k: v for k, v in f.items() if k not in ('split_qty', 'split_part', '_old_group')}
        whole['qty'] = f['split_qty']
        whole['amount'] = round(sum(h.get('amount', 0.0) for h in pair), 2)
        whole['commission'] = round(sum(h.get('commission', 0.0) for h in pair), 4)
        # both stored trades the halves came from are replaced by this regroup
        whole['_old_groups'] = sorted({h.get('_old_group') for h in pair if h.get('_old_group')})
        out.append(whole)
    return out


def _is_flat(position) -> bool:
    """Position is back to zero. Tolerance so fractional-share fills (IBKR) still close out."""
    return abs(position) < 1e-6


def _option_pos_key(ticker: str, instr: str, expiry, strike, opt_type) -> tuple:
    return (ticker, instr, expiry or '', strike or 0, opt_type or '')


def _stock_pos_key(ticker: str, instr: str) -> tuple:
    return (ticker, instr)


def _exec_pos_key(ex: dict) -> tuple:
    instr = ex.get('instrument_type', 'STOCK')
    if instr == 'OPTION':
        return _option_pos_key(ex['ticker'], instr,
                                ex.get('option_expiry'), ex.get('option_strike'), ex.get('option_type'))
    return _stock_pos_key(ex['ticker'], instr)


def load_open_positions_from_db(conn, account_id: int) -> list[dict]:
    """
    Return open trade records from the DB (positions where qty_bot != qty_sold).
    Each record includes the parsed executions list.
    """
    rows = conn.execute(
        """SELECT id, trade_group, ticker, instrument_type,
                  option_expiry, option_strike, option_type, side, executions
           FROM trades WHERE account_id = ?""",
        (account_id,)
    ).fetchall()
    open_trades = []
    for row in rows:
        d = dict(row)
        execs = json.loads(d['executions'] or '[]')
        qty_bot = sum(e.get('qty', 0) for e in execs if e.get('action') == 'BOT')
        qty_sold = sum(e.get('qty', 0) for e in execs if e.get('action') == 'SOLD')
        if not _is_flat(qty_bot - qty_sold):
            d['parsed_execs'] = execs
            open_trades.append(d)
    return open_trades


def _rebuild_fill_from_db_exec(e: dict, trade_meta: dict) -> dict:
    """Reconstruct a full fill dict from a stored execution + trade metadata."""
    instr = trade_meta['instrument_type']
    multiplier = 100 if instr == 'OPTION' else 1
    price = e.get('price', 0.0)
    qty = e.get('qty', 0)
    amount = price * qty * multiplier
    if e.get('action') == 'BOT':
        amount = -amount
    return {
        'action': e.get('action', ''),
        'qty': qty,
        'ticker': trade_meta['ticker'],
        'price': price,
        'instrument_type': instr,
        'option_expiry': trade_meta.get('option_expiry'),
        'option_strike': trade_meta.get('option_strike'),
        'option_type': trade_meta.get('option_type'),
        'date': e.get('date', ''),
        'iso_date': e.get('date', ''),
        'time': e.get('time', ''),
        'amount': round(amount, 2),
        'commission': e.get('commission', 0.0),
        **({'split_qty': e['split_qty'], 'split_part': e.get('split_part')}
           if e.get('split_qty') is not None else {}),
    }


def _raw_date(iso_date: str) -> str:
    """YYYY-MM-DD back to the statement's M/D/YY, the form trade_group keys are built from."""
    try:
        y, m, d = iso_date.split('-')
        return f"{int(m)}/{int(d)}/{y[2:]}"
    except ValueError:
        return iso_date


def _point_value(ticker: str, instr: str):
    if instr == 'OPTION':
        return 100
    if instr != 'FUTURE':
        return 1
    for root in sorted(FUTURES_MULTIPLIERS, key=len, reverse=True):
        if ticker.upper().startswith(root):
            return FUTURES_MULTIPLIERS[root]
    return None


def overlapping_db_fills(conn, account_id: int, new_execs: list[dict]) -> tuple[list[dict], set[str]]:
    """Stored fills that must be regrouped together with the new ones.

    Trade groups are numbered by position cycle within one import (_1, _2, ...). A later
    import that brings more fills for a ticker already stored that day would number its
    cycles from 1 again and overwrite the stored trade of the same name, losing it. So for
    each stock or future with new fills, the stored imported trades that share a day with
    them (or are still open) are rebuilt from their executions and grouped with the new
    fills, and the caller replaces them. Futures with an unknown point value are left alone.
    Returns (rebuilt fills tagged with '_old_group', the old trade_groups they came from).
    """
    days: dict[tuple, set] = {}
    for ex in new_execs:
        instr = ex.get('instrument_type', 'STOCK')
        if instr == 'OPTION':
            # Single-leg contracts only: a stored vertical keeps no second strike to rebuild from.
            if ex.get('option_strike2'):
                continue
            key = (ex['ticker'], instr, ex.get('option_expiry'), ex.get('option_strike'), ex.get('option_type'))
        else:
            key = (ex['ticker'], instr, None, None, None)
        days.setdefault(key, set()).add(ex.get('iso_date') or normalize_date(ex['date']))

    fills: list[dict] = []
    old_groups: set[str] = set()
    for (ticker, instr, expiry, strike, opt_type), dates in days.items():
        # Option fills come back from the DB already at 100 per contract.
        mult = 1 if instr == 'OPTION' else _point_value(ticker, instr)
        if mult is None:
            continue
        rows = conn.execute(
            """SELECT trade_group, ticker, instrument_type, option_expiry, option_strike, option_type,
                      executions, gross_pnl
               FROM trades WHERE account_id = ? AND ticker = ? AND instrument_type = ?
               AND COALESCE(source, 'imported') = 'imported'""",
            (account_id, ticker, instr),
        ).fetchall()
        for row in rows:
            meta = dict(row)
            if instr == 'OPTION':
                if (meta['option_expiry'] != expiry or meta['option_type'] != opt_type
                        or meta['option_strike'] is None or strike is None
                        or abs(float(meta['option_strike']) - float(strike)) > 1e-9):
                    continue
                if _is_vertical_group(meta['trade_group']):
                    continue
            execs = json.loads(meta['executions'] or '[]')
            bot = sum(e.get('qty', 0) for e in execs if e.get('action') == 'BOT')
            sold = sum(e.get('qty', 0) for e in execs if e.get('action') == 'SOLD')
            if not ({e.get('date', '') for e in execs} & dates) and _is_flat(bot - sold):
                continue
            # The point value the trade was booked with (a generic-template multiplier
            # column, or a statement priced in other units) beats the table's.
            row_mult = _stored_point_value(execs, meta.get('gross_pnl')) if instr == 'FUTURE' else None
            old_groups.add(meta['trade_group'])
            for e in execs:
                f = _rebuild_fill_from_db_exec(e, meta)
                f['amount'] = round(f['amount'] * (row_mult or mult), 2)
                # keys carry the date the way this broker's parser wrote it
                slashed = '/' in meta['trade_group'].split('_')[0]
                f['date'] = _raw_date(f['iso_date']) if slashed else f['iso_date']
                f['_old_group'] = meta['trade_group']
                fills.append(f)
    return fills, old_groups


def _stored_point_value(execs: list[dict], gross_pnl) -> float | None:
    """Point value implied by a closed stored trade: its gross P&L over its price moves."""
    if not gross_pnl:
        return None
    moves = sum(e.get('price', 0) * e.get('qty', 0) * (1 if e.get('action') != 'BOT' else -1) for e in execs)
    if abs(moves) < 1e-9:
        return None
    implied = gross_pnl / moves
    return implied if implied > 0 else None


def _is_vertical_group(trade_group: str) -> bool:
    """A vertical's key carries both strikes ('..._635-630_PUT_1')."""
    parts = trade_group.split('_')
    return len(parts) >= 3 and '-' in parts[-3].lstrip('-')


def _cross_section_key(ex: dict) -> str:
    """Fingerprint for deduplicating across CSV sections (no time field — formats differ)."""
    return f"{ex.get('iso_date','')}|{ex.get('ticker','')}|{_exec_contract(ex)}|{ex.get('action','')}|{ex.get('qty','')}|{ex.get('price','')}"


def _exec_contract(ex: dict) -> str:
    return _contract_id(ex.get('instrument_type'), ex.get('option_expiry'), ex.get('option_strike'),
                        ex.get('option_type'), ex.get('option_strike2'))


def parse_thinkorswim_csv(content: str, account_id: int, conn=None, import_tz: str = IMPORT_LOCAL_TZ) -> tuple[list[dict], int]:
    """
    Full CSV parse pipeline.
    Returns (list of trade dicts ready for DB insert, skipped_count).
    """
    content = content.lstrip('﻿')

    sections = split_csv_sections(content)
    cash_rows = find_cash_balance_section(sections)
    futures_rows = find_futures_section(sections)
    trade_history_rows = find_trade_history_section(sections)

    all_executions = []
    multileg_fees = []
    if cash_rows:
        for ex in parse_cash_balance_section(cash_rows, import_tz=import_tz):
            (multileg_fees if ex.get('_fee_only') else all_executions).append(ex)
    if futures_rows:
        all_executions.extend(parse_futures_section_rows(futures_rows, import_tz=import_tz))

    # Merge Trade History: add fills not already represented in Cash Balance / Futures.
    # Use (iso_date, ticker, action, qty, price) to match across sections — time formats differ.
    # Thinkorswim aggregates partial fills in Trade History (e.g., two CB fills of 40+60 appear as 100).
    # Check cumulative qty per (date, ticker, action, price) so those aggregated entries are skipped.
    if trade_history_rows:
        cb_exact_keys = {_cross_section_key(ex) for ex in all_executions}
        cb_qty_map: dict[tuple, int] = {}
        for ex in all_executions:
            k = (ex.get('iso_date', ''), ex.get('ticker', ''), _exec_contract(ex), ex.get('action', ''), ex.get('price', 0.0))
            cb_qty_map[k] = cb_qty_map.get(k, 0) + ex.get('qty', 0)
        for ex in parse_trade_history_section(trade_history_rows, import_tz=import_tz):
            if ex.pop('_multileg', False):
                net = ex.pop('_net_price', None)
                if net is not None:
                    for fee in multileg_fees:
                        if (not fee.get('_used') and fee['iso_date'] == ex['iso_date']
                                and fee['ticker'] == ex['ticker'] and fee['qty'] == ex['qty']
                                and abs(fee['price'] - net) < 1e-9):
                            ex['commission'] = fee['commission']
                            fee['_used'] = True
                            break
                all_executions.append(ex)
                continue
            if _cross_section_key(ex) in cb_exact_keys:
                continue
            k = (ex.get('iso_date', ''), ex.get('ticker', ''), _exec_contract(ex), ex.get('action', ''), ex.get('price', 0.0))
            if cb_qty_map.get(k, 0) >= ex.get('qty', 0):
                continue  # CB partial fills already cover this aggregated TH fill
            all_executions.append(ex)

    return build_trades_from_executions(all_executions, account_id, conn)


def build_trades_from_executions(all_executions: list[dict], account_id: int, conn=None, *,
                                 commit: bool = True, dedup: bool = True) -> tuple[list[dict], int]:
    """
    Broker-agnostic half of the import pipeline. Takes execution dicts in the
    common shape (action BOT/SOLD, qty, ticker, price, instrument_type, option_*,
    date, iso_date, time, amount, commission) and returns (trade dicts, skipped).

    Steps: DB-level duplicate detection, merging fills into open option
    positions already stored, grouping by position open/close cycles, and
    aggregating each group into a trade row. Every broker parser ends here so
    grouping and dedup behave identically regardless of the CSV format.

    commit=False leaves the caller's transaction open (the Edge Trader sync runs a whole
    Apply, or a dry run it rolls back, in one transaction). dedup=False skips the
    per-account duplicate check for a caller that already removed known fills itself.
    """
    if not all_executions:
        return [], 0

    # DB-level dedup only — never dedupe within same file (Thinkorswim legitimately
    # emits identical time/price/qty fills for large split orders)
    existing_fps = get_existing_fingerprints(conn, account_id) if (conn and dedup) else Counter()
    skipped = 0
    unique_executions = []
    for exec_dict in all_executions:
        fp = execution_fingerprint(exec_dict)
        if existing_fps[fp] > 0:
            existing_fps[fp] -= 1  # each stored copy covers one copy in the file
            skipped += 1
        else:
            # Enrich with ticker/date for serialization
            exec_copy = dict(exec_dict)
            unique_executions.append(exec_copy)

    # Merge new fills into existing open OPTION positions from DB.
    # Options use a specific contract key (expiry/strike/type) so the match is unambiguous.
    # Stock positions are skipped here — day-trading cycles are too ambiguous to auto-merge.
    if conn:
        open_positions = load_open_positions_from_db(conn, account_id)
        open_by_key: dict[tuple, dict] = {}
        for pos in open_positions:
            if pos['instrument_type'] != 'OPTION':
                continue  # only merge options
            k = _option_pos_key(pos['ticker'], pos['instrument_type'],
                                pos.get('option_expiry'), pos.get('option_strike'), pos.get('option_type'))
            open_by_key[k] = pos

        absorbed = []
        remaining = []
        for ex in unique_executions:
            if ex.get('instrument_type') != 'OPTION':
                remaining.append(ex)
                continue
            k = _option_pos_key(ex['ticker'], ex['instrument_type'],
                                ex.get('option_expiry'), ex.get('option_strike'), ex.get('option_type'))
            # A single-leg contract goes through the regroup below with its stored open
            # trade, so a later file that closes it and opens a new one makes two trades.
            # Verticals keep no second strike to rebuild from, so they are merged in place.
            pos = open_by_key.get(k)
            if (pos and _is_vertical_group(pos['trade_group'])
                    and _fmt_strike(ex.get('option_strike2')) == _fmt_strike(_vertical_second_strike(pos['trade_group']))
                    and ex.get('option_strike2') not in (None, '')):
                absorbed.append((ex, pos))
            else:
                remaining.append(ex)

        # Group absorbed fills by their matched open position
        pos_updates: dict[str, list[dict]] = {}
        for ex, pos in absorbed:
            tg = pos['trade_group']
            pos_updates.setdefault(tg, {'pos': pos, 'new_fills': []})['new_fills'].append(ex)

        for tg, update in pos_updates.items():
            pos = update['pos']
            new_fills = update['new_fills']
            old_fills = [_rebuild_fill_from_db_exec(e, pos) for e in pos['parsed_execs']]
            all_fills = sorted(old_fills + new_fills,
                               key=lambda f: (f.get('iso_date', ''), f.get('time', '')))
            agg = aggregate_executions(all_fills)

            # Use closing fill date for closed positions
            qty_b = sum(f['qty'] for f in all_fills if f['action'] == 'BOT')
            qty_s = sum(f['qty'] for f in all_fills if f['action'] == 'SOLD')
            if _is_flat(qty_b - qty_s) and all_fills:
                side = pos['side']
                exit_action = 'SOLD' if side == 'LONG' else 'BOT'
                exit_fills = sorted([f for f in all_fills if f['action'] == exit_action],
                                    key=lambda f: (f.get('iso_date',''), f.get('time','')))
                new_date = exit_fills[-1].get('iso_date', all_fills[-1].get('iso_date', pos.get('date','')))
            else:
                new_date = max(f.get('iso_date', '') for f in all_fills) or pos.get('date', '')

            conn.execute(
                "UPDATE trades SET executions=?, gross_pnl=?, net_pnl=?, commissions=?, date=? WHERE trade_group=? AND account_id=?",
                (agg['executions'], agg['gross_pnl'], agg['net_pnl'], agg['commissions'],
                 new_date, tg, account_id)
            )

        if commit:
            conn.commit()
        unique_executions = remaining

    replaced: set[str] = set()
    if conn and unique_executions:
        stored, replaced = overlapping_db_fills(conn, account_id, unique_executions)
        unique_executions = unique_executions + stored

    groups, group_meta = group_executions_by_position(unique_executions)

    trades = []
    for key, fills in groups.items():
        meta = group_meta[key]
        agg = aggregate_executions(fills)
        trades.append({
            'account_id': account_id,
            'trade_group': key,
            'date': meta['date'],
            'ticker': meta['ticker'],
            'instrument_type': meta['instrument_type'],
            'side': agg['side'],
            'gross_pnl': agg['gross_pnl'],
            'net_pnl': agg['net_pnl'],
            'commissions': agg['commissions'],
            'executions': agg['executions'],
            'option_expiry': meta['option_expiry'],
            'option_strike': meta['option_strike'],
            'option_type': meta['option_type'],
            'source': 'imported',
            'replaces': sorted({f['_old_group'] for f in fills if f.get('_old_group')}
                               | {g for f in fills for g in f.get('_old_groups', [])}),
        })

    return trades, skipped


# ── Interactive Brokers (IBKR) Activity Statement ─────────────────────────────
#
# IBKR's Activity Statement CSV is one flat file where every line is prefixed
# with its section name and a row kind:
#
#   Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,...
#   Trades,Data,Order,Stocks,USD,AVGO,"2026-01-06, 09:52:00",-4,239.78,252.40,959.12,-0.35,...
#   Trades,SubTotal,,Stocks,USD,AVGO,,66,,,4099.00,-3.35,...
#
# Quantity is signed (buys positive, sells negative), Proceeds is signed the
# same way Thinkorswim's AMOUNT column is (buys negative, sells positive) and
# already includes the contract multiplier, and Comm/Fee is negative. That
# maps straight onto the common execution shape, so the grouping / dedup
# pipeline in build_trades_from_executions is reused unchanged.


def split_ibkr_sections(content: str) -> dict[str, list[dict[str, str]]]:
    """
    Split an IBKR Activity Statement CSV into named sections.
    Returns dict: section_name -> list of records (dict column_name -> value).
    A section may carry several Header rows (the Trades section repeats its
    header per asset category, sometimes with different columns), so each
    Data row is keyed by the most recent Header seen in that section.
    """
    sections: dict[str, list[dict[str, str]]] = {}
    headers: dict[str, list[str]] = {}

    reader = csv.reader(io.StringIO(content))
    for row in reader:
        if len(row) < 3:
            continue
        section, kind = row[0].strip(), row[1].strip()
        if kind == 'Header':
            headers[section] = [h.strip() for h in row[2:]]
            sections.setdefault(section, [])
        elif kind == 'Data':
            header = headers.get(section)
            if not header:
                continue
            values = row[2:]
            record = {h: (values[i].strip() if i < len(values) else '') for i, h in enumerate(header)}
            sections.setdefault(section, []).append(record)
        # SubTotal / Total / Notes rows are summaries, not fills: skip them.

    return sections


def parse_ibkr_option_symbol(symbol: str) -> dict | None:
    """
    Parse the two option symbol styles IBKR prints in statements:
      'AAPL 17JAN26 150 C'        (statement style)
      'AAPL  260117C00150000'     (OCC style, 21 chars)
    Returns dict with ticker, option_expiry (YYYY-MM-DD), option_strike, option_type.
    """
    s = symbol.strip().upper()

    m = re.match(r'^([A-Z.]+)\s+(\d{1,2})([A-Z]{3})(\d{2})\s+([\d.]+)\s+([CP])$', s)
    if m:
        ticker, day, mon, yy, strike, cp = m.groups()
        month = MONTH_MAP.get(mon)
        if not month:
            return None
        return {
            'ticker': ticker,
            'option_expiry': f"{2000 + int(yy):04d}-{month:02d}-{int(day):02d}",
            'option_strike': float(strike),
            'option_type': 'CALL' if cp == 'C' else 'PUT',
        }

    m = re.match(r'^([A-Z.]+)\s*(\d{2})(\d{2})(\d{2})([CP])(\d{8})$', s)
    if m:
        ticker, yy, mm, dd, cp, strike_raw = m.groups()
        return {
            'ticker': ticker,
            'option_expiry': f"{2000 + int(yy):04d}-{int(mm):02d}-{int(dd):02d}",
            'option_strike': int(strike_raw) / 1000.0,
            'option_type': 'CALL' if cp == 'C' else 'PUT',
        }

    return None


def _ibkr_instrument_type(asset_category: str) -> str | None:
    cat = asset_category.strip().lower()
    if not cat:
        return None
    if 'option' in cat:
        return 'OPTION'
    if 'future' in cat:
        return 'FUTURE'
    if 'stock' in cat or 'equit' in cat or 'etf' in cat:
        return 'STOCK'
    # Forex, bonds, CFDs, warrants, cash rows: not something the journal tracks.
    return None


def _ibkr_qty(value: str) -> float | int | None:
    """'-4' -> 4, '1,250' -> 1250, '0.5' -> 0.5 (fractional shares keep the decimal)."""
    s = value.strip().replace(',', '').lstrip('+-')
    if not s:
        return None
    try:
        q = float(s)
    except ValueError:
        return None
    if q == 0:
        return None
    return int(q) if q == int(q) else round(q, 6)


def _ibkr_datetime(value: str) -> tuple[str, str] | None:
    """'2026-01-06, 09:52:00' -> ('2026-01-06', '09:52:00'). Date-only rows get an empty time."""
    s = value.strip().strip('"')
    if not s:
        return None
    if ',' in s:
        date_part, time_part = [p.strip() for p in s.split(',', 1)]
    elif ' ' in s:
        date_part, time_part = s.split(' ', 1)
    else:
        date_part, time_part = s, ''
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date_part):
        return None
    return date_part, time_part.strip()


def parse_ibkr_trades_section(records: list[dict[str, str]]) -> list[dict]:
    """
    Turn the Trades section of an IBKR Activity Statement into execution dicts.
    IBKR emits one row per order by default (DataDiscriminator 'Order'); when a
    statement is configured to show executions it emits 'Trade' rows instead,
    and some layouts include both. If both are present only the 'Trade' rows
    (the real fills) are used so nothing is counted twice. 'ClosedLot' rows are
    tax-lot detail and always skipped.
    """
    if not records:
        return []

    kinds = {r.get('DataDiscriminator', '').strip() for r in records}
    use_kind = 'Trade' if 'Trade' in kinds else 'Order'

    executions = []
    for r in records:
        if r.get('DataDiscriminator', '').strip() != use_kind:
            continue

        instrument_type = _ibkr_instrument_type(r.get('Asset Category', ''))
        if not instrument_type:
            continue

        symbol = r.get('Symbol', '').strip().upper()
        if not symbol:
            continue

        qty = _ibkr_qty(r.get('Quantity', ''))
        if qty is None:
            continue
        signed = r.get('Quantity', '').strip().replace(',', '')
        action = 'SOLD' if signed.startswith('-') else 'BOT'

        dt = _ibkr_datetime(r.get('Date/Time', '') or r.get('Date', ''))
        if not dt:
            continue
        iso_date, time_part = dt

        price = clean_amount(r.get('T. Price', '') or r.get('Price', ''))
        proceeds = clean_amount(r.get('Proceeds', ''))
        commission = abs(clean_amount(r.get('Comm/Fee', '') or r.get('Comm in USD', '')))

        option_expiry = option_strike = option_type = None
        ticker = symbol
        if instrument_type == 'OPTION':
            parsed = parse_ibkr_option_symbol(symbol)
            if not parsed:
                continue
            ticker = parsed['ticker']
            option_expiry = parsed['option_expiry']
            option_strike = parsed['option_strike']
            option_type = parsed['option_type']
        elif instrument_type == 'FUTURE':
            # IBKR prints 'ESH6'; the rest of the app uses the Thinkorswim '/ESH6'
            # convention for futures (multiplier lookup, chart proxy), so match it.
            ticker = symbol if symbol.startswith('/') else '/' + symbol

        # Proceeds is already signed like Thinkorswim's AMOUNT (buys negative,
        # sells positive) and already includes the option/futures multiplier.
        # Fall back to price * qty when a statement leaves Proceeds blank.
        if proceeds == 0.0 and price:
            multiplier = 100 if instrument_type == 'OPTION' else 1
            proceeds = price * qty * multiplier
            if action == 'BOT':
                proceeds = -proceeds

        executions.append({
            'action': action,
            'qty': qty,
            'ticker': ticker,
            'price': abs(price),
            'instrument_type': instrument_type,
            'option_expiry': option_expiry,
            'option_strike': option_strike,
            'option_type': option_type,
            'date': iso_date,
            'iso_date': iso_date,
            'time': time_part,
            'amount': round(proceeds, 2),
            'commission': round(commission, 2),
            'raw_description': f"{action} {qty} {symbol} @{price}",
        })

    return executions


def parse_ibkr_csv(content: str, account_id: int, conn=None, import_tz: str = IMPORT_LOCAL_TZ) -> tuple[list[dict], int]:
    """
    IBKR Activity Statement CSV parse pipeline (Client Portal -> Performance &
    Reports -> Statements -> Activity -> CSV). Same output contract as
    parse_thinkorswim_csv: (trade dicts ready for DB insert, skipped_count).
    import_tz is accepted for a uniform call signature across brokers but
    currently unused here — IBKR statements haven't been confirmed to have
    the same local-clock timestamp issue Thinkorswim exports do.
    """
    content = content.lstrip('﻿')
    sections = split_ibkr_sections(content)

    trade_records = sections.get('Trades')
    if trade_records is None:
        raise ValueError(
            "No 'Trades' section found. Export an IBKR Activity Statement as CSV "
            "with the Trades section enabled."
        )

    executions = parse_ibkr_trades_section(trade_records)
    return build_trades_from_executions(executions, account_id, conn)


# ── Generic CSV (any broker, one row per execution) ───────────────────────────
#
# For brokers without a dedicated parser. The user copies their fills into the
# template at frontend/public/templates/generic_trades_template.csv, one row per
# fill.
#
# Required columns: date, time, symbol, side, quantity, price
# Optional columns: commission, asset_type, expiry, strike, put_call, multiplier
#
# Header names are case-insensitive and extra columns are ignored, so a broker
# export that already uses these headers imports as-is. Rows that cannot be read
# are never skipped silently: the import stops and names the line, because a
# missing fill changes every P&L number after it.

GENERIC_REQUIRED = ('date', 'time', 'symbol', 'side', 'quantity', 'price')
GENERIC_OPTIONAL = ('commission', 'asset_type', 'expiry', 'strike', 'put_call', 'multiplier')

_GENERIC_ALIASES = {
    'ticker': 'symbol', 'qty': 'quantity', 'shares': 'quantity', 'contracts': 'quantity',
    'fill_price': 'price', 'execution_price': 'price', 'action': 'side', 'buy_sell': 'side',
    'fees': 'commission', 'commissions': 'commission', 'comm': 'commission',
    'type': 'asset_type', 'instrument': 'asset_type', 'instrument_type': 'asset_type',
    'expiration': 'expiry', 'exp': 'expiry', 'call_put': 'put_call', 'right': 'put_call',
    'strike_price': 'strike',
}


def _generic_header(cells):
    """Map canonical column names to indexes, or None if this is not a generic header."""
    index = {}
    for i, raw in enumerate(cells):
        key = re.sub(r'[\s/-]+', '_', raw.strip().lower()).strip('_')
        key = _GENERIC_ALIASES.get(key, key)
        if key in GENERIC_REQUIRED or key in GENERIC_OPTIONAL:
            index.setdefault(key, i)
    return index if all(k in index for k in GENERIC_REQUIRED) else None


def _generic_date(value):
    """YYYY-MM-DD, or US M/D/YYYY. Day-first dates are refused: 03/04 is ambiguous."""
    v = value.strip()
    m = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})$', v)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{2,4})$', v)
        if not m:
            return None
        mo, d, y = map(int, m.groups())
        if y < 100:
            y += 2000
    try:
        return datetime(y, mo, d).strftime('%Y-%m-%d')
    except ValueError:
        return None


def _generic_time(value):
    """'9:31', '09:31:05', '9:31:05 AM', '14:02' -> 'HH:MM:SS' (24 hour)."""
    v = value.strip().upper()
    m = re.match(r'^(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)?$', v)
    if not m:
        return None
    h, mi, sec, ap = m.groups()
    h, mi, sec = int(h), int(mi), int(sec or 0)
    if ap == 'PM' and h < 12:
        h += 12
    if ap == 'AM' and h == 12:
        h = 0
    if h > 23 or mi > 59 or sec > 59:
        return None
    return f"{h:02d}:{mi:02d}:{sec:02d}"


def _generic_side(value):
    """BUY, BOT, B, BUY TO OPEN, BUY TO COVER -> BOT. SELL, SOLD, S, SELL SHORT -> SOLD."""
    v = value.strip().upper()
    if v in ('B', 'BOT', 'BOUGHT') or v.startswith('BUY'):
        return 'BOT'
    if v in ('S', 'SLD', 'SOLD', 'SHORT') or v.startswith('SELL'):
        return 'SOLD'
    return None


def _generic_asset(value, symbol):
    v = value.strip().upper()
    if not v:
        return 'FUTURE' if symbol.startswith('/') else 'STOCK'
    if v in ('STOCK', 'STK', 'EQUITY', 'ETF', 'SHARE', 'SHARES'):
        return 'STOCK'
    if v in ('OPTION', 'OPT', 'OPTIONS'):
        return 'OPTION'
    if v in ('FUTURE', 'FUT', 'FUTURES'):
        return 'FUTURE'
    return None


def _futures_multiplier(ticker):
    """Longest known root that prefixes the contract, so /MES wins over /ES."""
    for root in sorted(FUTURES_MULTIPLIERS, key=len, reverse=True):
        if ticker.upper().startswith(root):
            return FUTURES_MULTIPLIERS[root]
    return None


def _num(text):
    return float(text.replace(',', '').replace('$', '').strip())


def parse_generic_rows(content):
    """Read the generic template into execution dicts. Raises ValueError naming every bad line."""
    rows = list(csv.reader(io.StringIO(content.lstrip('\ufeff'))))

    header_at, col = None, None
    for i, cells in enumerate(rows[:20]):
        found = _generic_header(cells)
        if found:
            header_at, col = i, found
            break
    if col is None:
        raise ValueError(
            "This does not look like the generic template. The header row needs these columns: "
            + ", ".join(GENERIC_REQUIRED) + "."
        )

    def cell(cells, name):
        i = col.get(name)
        return cells[i].strip() if i is not None and i < len(cells) else ''

    executions, problems = [], []
    for n, cells in enumerate(rows[header_at + 1:], start=header_at + 2):
        if not any(c.strip() for c in cells):
            continue
        why = []

        symbol = cell(cells, 'symbol').upper()
        date = _generic_date(cell(cells, 'date'))
        time_ = _generic_time(cell(cells, 'time'))
        action = _generic_side(cell(cells, 'side'))
        asset = _generic_asset(cell(cells, 'asset_type'), symbol)

        try:
            qty = abs(_num(cell(cells, 'quantity')))
            if qty == 0:
                raise ValueError
            qty = int(qty) if qty == int(qty) else round(qty, 6)
        except ValueError:
            qty = None
        try:
            price = abs(_num(cell(cells, 'price')))
        except ValueError:
            price = None
        try:
            commission = abs(_num(cell(cells, 'commission') or '0'))
        except ValueError:
            commission = None

        if not symbol:
            why.append("symbol is empty")
        if not date:
            why.append(f"date '{cell(cells, 'date')}' is not YYYY-MM-DD or MM/DD/YYYY")
        if not time_:
            why.append(f"time '{cell(cells, 'time')}' is not HH:MM or HH:MM:SS")
        if not action:
            why.append(f"side '{cell(cells, 'side')}' is not BUY or SELL")
        if qty is None:
            why.append(f"quantity '{cell(cells, 'quantity')}' is not a number above zero")
        if price is None:
            why.append(f"price '{cell(cells, 'price')}' is not a number")
        if commission is None:
            why.append(f"commission '{cell(cells, 'commission')}' is not a number")
        if not asset:
            why.append(f"asset_type '{cell(cells, 'asset_type')}' is not STOCK, OPTION or FUTURE")

        option_expiry = option_strike = option_type = None
        multiplier = 1
        ticker = symbol
        if asset == 'OPTION':
            option_expiry = _generic_date(cell(cells, 'expiry'))
            pc = cell(cells, 'put_call').upper()
            option_type = 'CALL' if pc in ('C', 'CALL') else 'PUT' if pc in ('P', 'PUT') else None
            try:
                option_strike = _num(cell(cells, 'strike'))
            except ValueError:
                option_strike = None
            if not (option_expiry and option_type and option_strike is not None):
                why.append("options need expiry, strike and put_call")
            multiplier = 100
        elif asset == 'FUTURE':
            ticker = symbol if symbol.startswith('/') else '/' + symbol
            multiplier = _futures_multiplier(ticker)

        override = cell(cells, 'multiplier')
        if override:
            try:
                multiplier = _num(override)
            except ValueError:
                why.append(f"multiplier '{override}' is not a number")
        if asset == 'FUTURE' and not multiplier:
            # Guessing 1 would understate a /ES trade fifty times over.
            why.append(f"no known point value for {ticker}; add a multiplier column (50 for /ES, for example)")

        if why:
            problems.append(f"line {n}: " + "; ".join(why))
            continue

        amount = price * qty * multiplier
        if action == 'BOT':
            amount = -amount

        executions.append({
            'action': action,
            'qty': qty,
            'ticker': ticker,
            'price': price,
            'instrument_type': asset,
            'option_expiry': option_expiry,
            'option_strike': option_strike,
            'option_type': option_type,
            'date': date,
            'iso_date': date,
            'time': time_,
            'amount': round(amount, 2),
            'commission': round(commission, 2),
            'raw_description': f"{action} {qty} {ticker} @{price}",
        })

    if problems:
        more = f" (and {len(problems) - 8} more)" if len(problems) > 8 else ""
        raise ValueError(
            f"{len(problems)} row(s) could not be read, so nothing was imported{more}. "
            + " | ".join(problems[:8])
        )
    if not executions:
        raise ValueError("The file has the template header but no trade rows.")
    return executions


def parse_generic_csv(content, account_id, conn=None, import_tz: str = IMPORT_LOCAL_TZ):
    """Generic template pipeline. Same output contract as the broker parsers.
    import_tz is accepted for a uniform call signature but unused — this
    template is filled in by hand, so there's no local-clock export to convert."""
    return build_trades_from_executions(parse_generic_rows(content), account_id, conn)


# ── Broker dispatch ────────────────────────────────────────────────────────────

BROKER_PARSERS = {
    'thinkorswim': parse_thinkorswim_csv,
    'ibkr': parse_ibkr_csv,
    'generic': parse_generic_csv,
}

BROKER_LABELS = {
    'thinkorswim': 'Thinkorswim',
    'ibkr': 'Interactive Brokers',
    'generic': 'the generic template',
    **broker_formats.LABELS,
}


def detect_broker(content: str) -> str | None:
    """Sniff the CSV format. Returns a BROKER_PARSERS key or None if unrecognised."""
    head = content.lstrip('﻿')[:4000]
    first_lines = [ln.strip() for ln in head.splitlines()[:5] if ln.strip()]
    if any(ln.startswith(('Statement,Header', 'Trades,Header', 'Account Information,Header'))
           for ln in first_lines):
        return 'ibkr'
    if 'DataDiscriminator' in content and re.search(r'^[\w /&-]+,(Header|Data),', head, re.MULTILINE):
        return 'ibkr'
    upper = head.upper()
    if ('CASH BALANCE' in upper or 'ACCOUNT STATEMENT' in upper
            or 'ACCOUNT TRADE HISTORY' in upper or 'FUTURES STATEMENTS' in upper):
        return 'thinkorswim'
    found = broker_formats.detect(content)
    if found:
        return found
    # Checked last: the first non-empty row is a header with the template's columns.
    for cells in csv.reader(io.StringIO(head)):
        if not any(c.strip() for c in cells):
            continue
        return 'generic' if _generic_header(cells) else None
    return None


def _position_sizes_before(conn, account_id: int):
    """Signed size of each position just before a file's first fill for it, keyed like
    broker_formats.position_key, so a file that starts by closing or flipping a position
    is read right. Only stored fills earlier than the file count: a re-import of the
    same file must not see its own fills as an open position."""
    def sizes(first_fill: dict) -> dict:
        out = {}
        tickers = sorted({k[0] for k in first_fill})
        if not tickers:
            return out
        rows = conn.execute(
            f"SELECT ticker, instrument_type, option_expiry, option_strike, option_type, executions "
            f"FROM trades WHERE account_id = ? AND ticker IN ({','.join('?' * len(tickers))})",
            (account_id, *tickers)).fetchall()
        for row in rows:
            key = (row['ticker'], row['instrument_type'], row['option_expiry'],
                   row['option_strike'], row['option_type'])
            cutoff = first_fill.get(key)
            if cutoff is None:
                continue
            for e in json.loads(row['executions'] or '[]'):
                when = (e.get('iso_date') or normalize_date(e.get('date', '')), e.get('time') or '')
                if when < cutoff:
                    qty = e.get('qty', 0)
                    out[key] = out.get(key, 0) + (qty if e.get('action') == 'BOT' else -qty)
        return out
    return sizes


def parse_broker_csv(content: str, broker: str, account_id: int, conn=None, *,
                     timezone: str | None = None, source_account: str | None = None,
                     warnings: list | None = None,
                     import_tz: str = IMPORT_LOCAL_TZ) -> tuple[list[dict], int]:
    """
    Route a CSV to the right broker parser. broker is a BROKER_PARSERS key, a
    broker_formats key, or 'auto'. An explicit broker that clearly does not match
    the file raises a ValueError with a hint, instead of importing zero trades silently.
    timezone and source_account apply to the broker_formats exports only; notes the
    user should see (missing fees, for example) are appended to warnings.
    import_tz: the local timezone Thinkorswim's desktop app was running in
    when it wrote the export, used to convert fill times to exchange (Eastern)
    time. Defaults to IMPORT_LOCAL_TZ; callers should normally pass the
    user's configured value from Settings > General instead.
    """
    key = (broker or 'auto').strip().lower()
    detected = detect_broker(content)

    if key == 'auto':
        if not detected:
            raise ValueError(
                "Could not recognise this CSV. Pick the broker from the dropdown. "
                "Supported exports: Thinkorswim account statement, Interactive Brokers "
                "Activity Statement, Tradovate Orders, NinjaTrader Executions, TopstepX "
                "trades, Webull order history, tastytrade transactions, TradeStation activity, "
                "Power E*TRADE orders. Otherwise copy your fills into the "
                "generic template (Import page, 'Broker not listed?')."
            )
        key = detected

    if key not in BROKER_PARSERS and key not in broker_formats.SPEC_BY_KEY:
        raise ValueError(f"Unsupported broker '{broker}'. Supported: {', '.join(BROKER_LABELS.values())}.")

    if key in broker_formats.SPEC_BY_KEY:
        # The chosen broker's own columns decide, so a file another reader would also
        # recognise is not refused.
        missing = broker_formats.missing_columns(content, key)
        if missing:
            hint = (f" It looks like an export from {BROKER_LABELS[detected]}." if detected and detected != key else "")
            raise ValueError(
                f"This file does not have the columns of a {BROKER_LABELS[key]} export "
                f"(missing: {', '.join(missing)}).{hint}"
            )
        executions = broker_formats.parse_rows(
            content, key, timezone or broker_formats.DEFAULT_TIMEZONE, source_account, warnings,
            open_positions=_position_sizes_before(conn, account_id) if conn else None)
        return build_trades_from_executions(executions, account_id, conn)

    if detected and detected != key:
        raise ValueError(
            f"This file looks like an export from {BROKER_LABELS[detected]}, but "
            f"{BROKER_LABELS[key]} is selected. Change the broker dropdown and try again."
        )

    return BROKER_PARSERS[key](content, account_id, conn, import_tz)
