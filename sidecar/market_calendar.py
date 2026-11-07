"""When the New York Stock Exchange is open, so the briefs stop reciting Friday.

On Saturday 2026-10-31 and Sunday 11-01 his phone was told "S&P 500 down
1.77%" six times - Friday's close, in every brief, because the market data keeps
the last session's numbers and nothing asked whether there had been a session.
The 20:00 brief repeated the 16:15 one every weekday for the same reason.

A small committed table, deliberately: the NYSE's full-day closures for 2026
and 2027, as the exchange observes them (a Saturday holiday is taken on the
Friday before, a Sunday one on the Monday after; New Year's Day 2028 falls on a
Saturday and is not observed in 2027). Half days are trading days. Past 2027
only weekends are known, and `holiday_table_covers()` says so - the gate fails
when the table runs out, so it is extended before it goes stale.
"""
from __future__ import annotations

import datetime as dt

NYSE_HOLIDAYS = {
    # 2026
    dt.date(2026, 1, 1),    # New Year's Day
    dt.date(2026, 1, 19),   # Martin Luther King Jr. Day
    dt.date(2026, 2, 16),   # Washington's Birthday
    dt.date(2026, 4, 3),    # Good Friday
    dt.date(2026, 5, 25),   # Memorial Day
    dt.date(2026, 6, 19),   # Juneteenth
    dt.date(2026, 7, 3),    # Independence Day (observed; the 4th is a Saturday)
    dt.date(2026, 9, 7),    # Labor Day
    dt.date(2026, 11, 26),  # Thanksgiving
    dt.date(2026, 12, 25),  # Christmas
    # 2027
    dt.date(2027, 1, 1),    # New Year's Day
    dt.date(2027, 1, 18),   # Martin Luther King Jr. Day
    dt.date(2027, 2, 15),   # Washington's Birthday
    dt.date(2027, 3, 26),   # Good Friday
    dt.date(2027, 5, 31),   # Memorial Day
    dt.date(2027, 6, 18),   # Juneteenth (observed; the 19th is a Saturday)
    dt.date(2027, 7, 5),    # Independence Day (observed; the 4th is a Sunday)
    dt.date(2027, 9, 6),    # Labor Day
    dt.date(2027, 11, 25),  # Thanksgiving
    dt.date(2027, 12, 24),  # Christmas (observed; the 25th is a Saturday)
}
LAST_COVERED = dt.date(2027, 12, 31)

OPEN = dt.time(9, 30)
CLOSE = dt.time(16, 0)


def holiday_table_covers(day: dt.date) -> bool:
    return day <= LAST_COVERED


def is_trading_day(day: dt.date | dt.datetime) -> bool:
    d = day.date() if isinstance(day, dt.datetime) else day
    return d.weekday() < 5 and d not in NYSE_HOLIDAYS


def previous_trading_day(day: dt.date) -> dt.date:
    d = day - dt.timedelta(days=1)
    while not is_trading_day(d):
        d -= dt.timedelta(days=1)
    return d


def is_open(now: dt.datetime) -> bool:
    """Is the regular session running? (His machine runs on Eastern time.)"""
    return is_trading_day(now) and OPEN <= now.time() <= CLOSE


def session_state(now: dt.datetime) -> tuple[str, str]:
    """Which numbers are current: ("open", today) during the session, else
    ("closed", the session whose close the data still shows)."""
    today = now.date()
    if is_trading_day(today):
        if now.time() < OPEN:
            return "closed", previous_trading_day(today).isoformat()
        if now.time() <= CLOSE:
            return "open", today.isoformat()
        return "closed", today.isoformat()
    return "closed", previous_trading_day(today).isoformat()
