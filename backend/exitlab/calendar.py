from functools import lru_cache
from datetime import date, timedelta
import pandas_market_calendars as mcal
from .models import NY


@lru_cache(maxsize=2048)
def session(day):
    schedule = mcal.get_calendar("NYSE").schedule(start_date=day, end_date=day)
    if schedule.empty:
        raise ValueError("not_a_regular_session")
    row = schedule.iloc[0]
    return row.market_open.to_pydatetime(), row.market_close.to_pydatetime()


def trade_session(trade):
    return session(trade.entered_at.astimezone(NY).date().isoformat())


@lru_cache(maxsize=2048)
def previous_session(day):
    """Previous exchange session, including holidays and its actual close."""
    end = date.fromisoformat(day) - timedelta(days=1)
    schedule = mcal.get_calendar("NYSE").schedule(
        start_date=end - timedelta(days=30), end_date=end)
    row = schedule.iloc[-1]
    return row.market_open.to_pydatetime(), row.market_close.to_pydatetime()


def regular_windows(trade):
    day = trade.entered_at.astimezone(NY).date().isoformat()
    return previous_session(day), session(day)
