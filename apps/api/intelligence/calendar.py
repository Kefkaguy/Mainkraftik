"""XNYS session semantics adapted from KefTrade's tested intraday calendar.

Adaptation: daily target sessions and scheduled issuance; no intraday research DB dependencies.
"""

from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal

EASTERN = ZoneInfo("America/New_York")


@lru_cache(maxsize=128)
def schedule(start: date, end: date):
    frame = mcal.get_calendar("XNYS").schedule(start_date=start, end_date=end)
    return {
        i.date(): {"open": r.market_open.to_pydatetime(), "close": r.market_close.to_pydatetime()}
        for i, r in frame.iterrows()
    }


def future_sessions(origin: date, count: int = 5):
    return list(schedule(origin + timedelta(days=1), origin + timedelta(days=35)))[:count]


def cutoff_for(session: date):
    return datetime.combine(session, time(20), EASTERN).astimezone(UTC)


def issuance_for(session: date):
    return datetime.combine(session, time(20, 30), EASTERN).astimezone(UTC)


def issuance_window(instant: datetime):
    if instant.tzinfo is None:
        raise ValueError("An aware issuance timestamp is required")
    today = instant.astimezone(EASTERN).date()
    prior = schedule(today - timedelta(days=10), today)
    eligible = [d for d in prior if issuance_for(d) <= instant]
    if not eligible:
        return None, "Waiting for 20:30 America/New_York issuance"
    origin = max(eligible)
    entry = future_sessions(origin, 1)[0]
    if instant >= schedule(entry, entry)[entry]["open"]:
        return None, "Previous entry window has passed; waiting for the next scheduled issuance"
    return origin, None
