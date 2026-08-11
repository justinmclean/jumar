# SPDX-License-Identifier: Apache-2.0
"""Recurrence arithmetic for @every= rules.

Public API
----------
next_occurrence(recur, now, tz, time_of_day) -> datetime
    Return the first UTC-aware datetime strictly after *now* that satisfies
    *recur*.  DST gaps are resolved forward (first valid local time after the
    gap); DST folds use fold=1 (the later UTC instant, i.e. post-fold).
"""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, timedelta
from datetime import time as dt_time
from zoneinfo import ZoneInfo

from jumar.models import Recurrence, RecurUnit

# ---------------------------------------------------------------------------
# Named day-of-week map (3-letter abbreviation → Python weekday number 0=Mon)
# ---------------------------------------------------------------------------

_DOW_MAP: dict[str, int] = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


# ---------------------------------------------------------------------------
# DST-aware local-to-UTC resolution
# ---------------------------------------------------------------------------


def _resolve_local(d: date, t: dt_time, tz: ZoneInfo) -> datetime:
    """Combine *d* and *t* in *tz*, resolving DST gaps and folds forward.

    DST **gap** (spring forward — the local time does not exist):
        The local datetime is constructed with fold=0 (pre-transition offset).
        If the round-trip UTC→local gives a *different* local time, we are in a
        gap.  The fold=0 UTC value already represents a moment *after* the
        transition (the pre-transition offset applied to a non-existent local
        time overshoots into post-transition territory), so we return it
        directly.

    DST **fold** (fall back — the local time is ambiguous):
        fold=0 is the first (earlier UTC) occurrence; fold=1 is the second
        (later UTC) occurrence.  "Resolve forward" means the later UTC instant,
        so we use fold=1 and return its UTC conversion.
    """
    # Construct with fold=0 (pre-transition, or unambiguous).
    dt0 = datetime(d.year, d.month, d.day, t.hour, t.minute, t.second, tzinfo=tz)
    utc0 = dt0.astimezone(UTC)

    # Round-trip: if the local time we get back differs, we were in a gap.
    local_back = utc0.astimezone(tz)
    if (local_back.hour, local_back.minute, local_back.second, local_back.date()) != (
        t.hour,
        t.minute,
        t.second,
        d,
    ):
        # In a gap: fold=0 UTC already points past the gap end — return it.
        return utc0

    # Check for a fold (ambiguous local time): fold=1 gives a different UTC.
    dt1 = datetime(d.year, d.month, d.day, t.hour, t.minute, t.second, tzinfo=tz, fold=1)
    utc1 = dt1.astimezone(UTC)

    if utc1 != utc0:
        # Ambiguous — "forward" means the later UTC instant.
        return utc1 if utc1 > utc0 else utc0

    return utc0


# ---------------------------------------------------------------------------
# Month arithmetic (no relativedelta dependency)
# ---------------------------------------------------------------------------


def _add_months(d: date, months: int) -> date:
    """Return *d* advanced by *months* calendar months, clamped to month end."""
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day)


# ---------------------------------------------------------------------------
# Next-occurrence logic per RecurUnit
# ---------------------------------------------------------------------------


def _next_interval(
    base: date,
    unit: RecurUnit,
    interval: int,
) -> date:
    """Return the next date that is *interval* units after *base*."""
    if unit is RecurUnit.day:
        return base + timedelta(days=interval)
    if unit is RecurUnit.week:
        return base + timedelta(weeks=interval)
    if unit is RecurUnit.month:
        return _add_months(base, interval)
    raise AssertionError(f"Unhandled unit: {unit!r}")  # pragma: no cover


def _next_weekday_date(from_date: date) -> date:
    """Return the next calendar weekday (Mon–Fri) strictly after *from_date*."""
    d = from_date + timedelta(days=1)
    while d.weekday() >= 5:  # 5=Sat, 6=Sun
        d += timedelta(days=1)
    return d


def _next_dow_date(from_date: date, target_weekdays: frozenset[int]) -> date:
    """Return the next date whose weekday is in *target_weekdays*, strictly after *from_date*."""
    d = from_date + timedelta(days=1)
    while d.weekday() not in target_weekdays:
        d += timedelta(days=1)
    return d


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def next_occurrence(
    recur: Recurrence,
    now: datetime,
    tz: ZoneInfo,
    time_of_day: dt_time | None = None,
) -> datetime:
    """Return the first UTC-aware datetime strictly after *now* satisfying *recur*.

    Parameters
    ----------
    recur:
        Parsed recurrence rule (unit, interval, days).
    now:
        The run's eligibility instant (UTC-aware).  The first returned
        occurrence will be strictly after this instant.
    tz:
        IANA timezone used for wall-clock arithmetic.  Recurrence is expressed
        in local time so that a daily item fires at the same local hour even
        across DST transitions.
    time_of_day:
        Wall-clock time preserved from the original ``@not-before=`` literal.
        When ``None``, midnight (00:00) is used.
    """
    tod = time_of_day if time_of_day is not None else dt_time(0, 0)
    local_now = now.astimezone(tz)
    base_date = local_now.date()

    if recur.unit in (RecurUnit.day, RecurUnit.week, RecurUnit.month):
        candidate_date = _next_interval(base_date, recur.unit, recur.interval)
        candidate = _resolve_local(candidate_date, tod, tz)
        # Guard: if the candidate is not strictly after now (can only happen for
        # sub-day intervals, which are not supported, but be defensive).
        while candidate <= now:
            candidate_date = _next_interval(candidate_date, recur.unit, recur.interval)
            candidate = _resolve_local(candidate_date, tod, tz)
        return candidate

    if recur.unit is RecurUnit.weekday:
        candidate_date = _next_weekday_date(base_date)
        candidate = _resolve_local(candidate_date, tod, tz)
        while candidate <= now:
            candidate_date = _next_weekday_date(candidate_date)
            candidate = _resolve_local(candidate_date, tod, tz)
        return candidate

    if recur.unit is RecurUnit.dow:
        target_days = frozenset(_DOW_MAP[d] for d in recur.days)
        candidate_date = _next_dow_date(base_date, target_days)
        candidate = _resolve_local(candidate_date, tod, tz)
        while candidate <= now:
            candidate_date = _next_dow_date(candidate_date, target_days)
            candidate = _resolve_local(candidate_date, tod, tz)
        return candidate

    raise AssertionError(f"Unhandled RecurUnit: {recur.unit!r}")  # pragma: no cover
