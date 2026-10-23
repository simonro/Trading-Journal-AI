"""Completed-bar indicators with explicit warm-up and availability times."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from .models import MINUTE, continuous, NY
from .calendar import trade_session, regular_windows


@dataclass(frozen=True)
class Candle:
    ts: datetime
    end: datetime
    close: float


def regular_minutes(trade, timeframe, length, through_close=True):
    """Deterministic seed: current RTH plus only the needed prior-session tail."""
    (prior_open, prior_close), (opening, closing) = regular_windows(trade)
    entry = trade.replay_at.replace(second=0, microsecond=0)
    boundary = entry.replace(minute=entry.minute - entry.minute % timeframe)
    current_count = int((boundary - opening) / (timeframe * MINUTE))
    shortfall = max(0, length - current_count)
    prior_start = prior_close - shortfall * timeframe * MINUTE
    if prior_start < prior_open:
        raise ValueError("regular_warmup_exceeds_previous_session")
    windows = [(prior_start, prior_close), (opening, closing if through_close else boundary)]
    return [start + i * MINUTE for start, end in windows
            for i in range(int((end - start) / MINUTE))]


def required_bars(trade, bars, timeframe, length, spy_bars=None, through_close=True):
    expected = regular_minutes(trade, timeframe, length, through_close)
    if not through_close:
        expected = expected[-length:]
    sources = [(trade.symbol, {b.ts: b for b in bars})]
    if spy_bars is not None:
        sources.append(("SPY", {b.ts: b for b in spy_bars}))
    # Time first, then symbol: report the first missing required minute.
    for ts in expected:
        for symbol, indexed in sources:
            if ts not in indexed:
                return [], [], f"regular_minute_missing|{symbol}|{ts.astimezone(NY).isoformat()}"
    return ([sources[0][1][ts] for ts in expected],
            [sources[1][1][ts] for ts in expected] if len(sources) > 1 else [], None)


def aggregate(bars, timeframe):
    groups = {}
    for b in bars:
        start = b.ts.replace(minute=b.ts.minute - b.ts.minute % timeframe)
        groups.setdefault(start, []).append(b)
    result = []
    for start, group in sorted(groups.items()):
        end = start + timedelta(minutes=timeframe)
        if continuous(group, start, end):
            result.append(Candle(start, end, group[-1].close))
    return result


def ema_values(candles, length):
    values, previous = {}, None
    for i, candle in enumerate(candles):
        if i < length-1:
            continue
        previous = (sum(c.close for c in candles[:length])/length if previous is None else
                    previous + 2/(length+1)*(candle.close-previous))
        values[candle.end] = previous
    return values


def vwap_targets(trade, bars):
    opening, closing = trade_session(trade)
    regular = [b for b in bars if opening <= b.ts < closing]
    if not continuous(regular, opening, closing):
        return {}, "vwap_session_coverage_insufficient"
    if any(b.volume > 0 and b.vwap is None for b in regular):
        return {}, "vwap_bar_values_missing"
    volume, notional, targets = 0, 0, {}
    for b in regular:
        # The current minute contributes only to the NEXT minute's target.
        if volume > 0:
            targets[b.ts.isoformat()] = notional / volume
        volume += b.volume
        notional += (b.vwap or 0) * b.volume
    entry_minute = trade.replay_at.replace(second=0, microsecond=0)
    entry_vwap = targets.get(entry_minute.isoformat())
    if entry_vwap is None:
        return {}, "vwap_at_entry_unavailable"
    if trade.direction * (entry_vwap - trade.entry) <= 0:
        return {}, "vwap_touch_wrong_side_at_entry"
    return targets, None


def rs_candles(bars):
    groups = {}
    for b in bars:
        start = b.ts.replace(minute=b.ts.minute - b.ts.minute % 5)
        groups.setdefault(start, []).append(b)
    return [(start, max(b.high for b in group), min(b.low for b in group), group[-1].close)
            for start, group in sorted(groups.items())
            if continuous(group, start, start + 5*MINUTE)]


def rrs_value(stock, spy):
    # Same raw RS/RW definition (length 12) as the Edge Scanner's live intraday
    # scanner (github.com/simonro/edge-scanner, scanner/indicators/rrs.py).
    # Match the scanner's 120-candle deque and first-TR-seeded Wilder EWM.
    stock, spy = stock[-120:], spy[-120:]
    if len(stock) < 13:
        return None
    def atr(candles):
        previous, value = None, None
        for _, high, low, close in candles:
            tr = high-low if previous is None else max(high-low, abs(high-previous), abs(low-previous))
            value = tr if value is None else value + (tr-value)/12
            previous = close
        return value
    a, b = atr(stock), atr(spy)
    if a <= 0 or b <= 0:
        return None
    move_stock, move_spy = stock[-1][3]-stock[-13][3], spy[-1][3]-spy[-13][3]
    return (move_stock - (move_spy/b)*a)/a


def rs_rw_exits(trade, bars, spy_bars, confirmation):
    entry = trade.replay_at.replace(second=0, microsecond=0)
    boundary = entry.replace(minute=entry.minute-entry.minute % 5)
    stock_minutes, spy_minutes, error = required_bars(
        trade, bars, 5, 13, spy_bars=spy_bars or [])
    if error:
        return {}, {}, error
    stock, spy = rs_candles(stock_minutes), rs_candles(spy_minutes)
    exits, values, armed, count = {}, {}, False, 0
    for i, c in enumerate(stock):
        value = rrs_value(stock[:i+1], spy[:i+1])
        end = c[0]+5*MINUTE
        if value is None:
            if end >= boundary:
                return {}, {}, "rs_rw_atr_unavailable"
            continue
        values[end.isoformat()] = value
        favorable = trade.direction*value > 0
        adverse = trade.direction*value < 0
        if end <= trade.replay_at:
            armed, count = favorable, 0
            continue
        if favorable:
            armed, count = True, 0
        elif adverse and armed:
            count += 1
            if count >= confirmation:
                exits[end] = "rs_rw"
        else:
            count = 0
    return exits, values, None


def prepare(trade, bars, rule, spy_bars=None):
    opening, closing = trade_session(trade)
    entry_minute = trade.replay_at.replace(second=0, microsecond=0)
    exits, stops, values = {}, {}, {}
    if rule.atr:
        length = rule.atr.length
        prior, _, error = required_bars(trade, bars, 1, length + 1, through_close=False)
        if error:
            return {}, {}, {}, error
        window = prior[-length-1:]
        atr = sum(max(b.high-b.low, abs(b.high-a.close), abs(b.low-a.close))
                  for a, b in zip(window, window[1:])) / length
        extreme = trade.entry
        for b in bars:
            if not entry_minute <= b.ts < closing:
                continue
            extreme = max(extreme, b.close) if trade.direction == 1 else min(extreme, b.close)
            stops[b.ts+MINUTE] = extreme - trade.direction*atr*rule.atr.multiple
        values["atr_at_open"] = atr
    if any(t.kind == "vwap_touch" for t in rule.tranches):
        targets, error = vwap_targets(trade, bars)
        if error:
            return {}, {}, {}, error
        values["vwap_targets"] = targets
    if rule.indicator and rule.indicator.kind == "rs_rw":
        exits, rs_values, error = rs_rw_exits(trade, bars, spy_bars, rule.indicator.confirmation)
        values["rs_rw"] = rs_values
        return exits, stops, values, error
    if not rule.indicator:
        return exits, stops, values, None
    ind = rule.indicator
    candles = aggregate(bars, ind.timeframe)
    n = 20 if ind.kind in ("ema20", "ema_cross") else 9
    if ind.kind != "vwap":
        regular, _, error = required_bars(trade, bars, ind.timeframe, n)
        if error:
            return {}, {}, {}, error
        candles = aggregate(regular, ind.timeframe)
        e9, e20 = ema_values(candles, 9), ema_values(candles, 20)
        previous = None
        for c in candles:
            short, long = e9.get(c.end), e20.get(c.end)
            if ind.kind == "ema_cross":
                current = None if short is None or long is None else short-long
                crossed = (current is not None and previous is not None and
                           trade.direction*previous >= 0 and trade.direction*current < 0)
                previous = current
                trigger = crossed
                value = current
            else:
                value = short if ind.kind == "ema9" else long
                trigger = value is not None and trade.direction*(c.close-value) < 0
            if c.end > trade.replay_at:
                values[c.end.isoformat()] = value
                if trigger:
                    exits[c.end] = ind.kind
    else:
        regular = [b for b in bars if opening <= b.ts < closing]
        if not continuous(regular, opening, closing):
            return {}, {}, {}, "vwap_session_coverage_insufficient"
        if any(b.vwap is None for b in regular if b.volume > 0):
            return {}, {}, {}, "vwap_bar_values_missing"
        volume, notional = 0, 0
        ends = {c.end for c in candles if c.end > trade.replay_at}
        for b in regular:
            volume += b.volume
            notional += (b.vwap or 0)*b.volume
            end = b.ts+MINUTE
            if end in ends:
                if volume <= 0:
                    return {}, {}, {}, "vwap_volume_insufficient"
                value = notional/volume
                values[end.isoformat()] = value
                if trade.direction*(b.close-value) < 0:
                    exits[end] = "vwap"
    return exits, stops, values, None
