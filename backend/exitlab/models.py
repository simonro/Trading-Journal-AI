from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
UTC = timezone.utc
MINUTE = timedelta(minutes=1)
ENGINE_VERSION = "exitlab-stop-first-v1"


def timestamp(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timezone_required")
    return dt.astimezone(UTC)


def iso(value):
    return value.astimezone(UTC).isoformat()


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             default=str, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    feed: str = "sip"
    vwap: float | None = None

    def __post_init__(self):
        if self.ts.tzinfo is None or self.ts.second or self.ts.microsecond:
            raise ValueError("bar_timestamp_must_be_aware_minute_start")
        values = [self.open, self.high, self.low, self.close, self.volume]
        if not all(math.isfinite(v) for v in values):
            raise ValueError("nonfinite_bar")
        if not (0 < self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high):
            raise ValueError("invalid_ohlc")
        if self.volume < 0 or self.feed not in ("sip", "iex"):
            raise ValueError("invalid_bar_volume_or_feed")
        if self.vwap is not None and (not math.isfinite(self.vwap) or self.vwap <= 0):
            raise ValueError("invalid_bar_vwap")


@dataclass(frozen=True)
class Trade:
    id: int
    symbol: str
    direction: int
    quantity: float
    entry: float
    exit: float
    entered_at: datetime
    exited_at: datetime
    stop: float | None = None
    source: str = "imported"

    fills: tuple = ()
    opening_last_at: datetime | None = None
    closing_first_at: datetime | None = None

    @property
    def scaled_entry(self):
        return bool(self.opening_last_at and
                    self.opening_last_at.replace(second=0, microsecond=0) !=
                    self.entered_at.replace(second=0, microsecond=0))

    @property
    def replay_at(self):
        return self.opening_last_at if self.scaled_entry else self.entered_at

    @property
    def boundary_price(self):
        if self.scaled_entry and self.fills:
            action = "BOT" if self.direction == 1 else "SOLD"
            return [f[3] for f in self.fills if f[1] == action][-1]
        return self.entry

    @property
    def risk(self):
        return abs(self.entry - self.stop) if self.stop is not None else None


def continuous(bars, start, end):
    expected = int((end - start).total_seconds() // 60)
    selected = [b for b in bars if start <= b.ts < end]
    return (len(selected) == expected and
            all(b.ts == start + i * MINUTE for i, b in enumerate(selected)))
