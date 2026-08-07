# SPDX-License-Identifier: Apache-2.0
"""Tests for recurrence.py — next-occurrence arithmetic.

Covers all RecurUnit variants plus the required DST-gap, DST-fold, and
missed-occurrence scenarios called out by the work item and AC8.5–AC8.6.

All clocks are frozen; no test reads the real system time.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from datetime import time as dt_time
from zoneinfo import ZoneInfo

import pytest

from getstuffdone.models import Recurrence, RecurUnit
from getstuffdone.recurrence import next_occurrence

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NY = ZoneInfo("America/New_York")  # UTC-5/UTC-4 (DST)
_SYD = ZoneInfo("Australia/Sydney")  # AEST/AEDT
_UTC_TZ = ZoneInfo("UTC")

_AT_9AM = dt_time(9, 0)
_AT_MIDNIGHT = dt_time(0, 0)
_AT_230AM = dt_time(2, 30)


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _make_recur(unit: RecurUnit, interval: int = 1, days: tuple[str, ...] = ()) -> Recurrence:
    return Recurrence(unit=unit, interval=interval, days=days)


# ---------------------------------------------------------------------------
# @every=1d — daily
# ---------------------------------------------------------------------------


class TestDailyRecurrence:
    def test_advances_one_day(self) -> None:
        recur = _make_recur(RecurUnit.day)
        now = _utc(2026, 8, 10, 14, 0)  # Monday 2pm UTC (10am ET)
        result = next_occurrence(recur, now, _NY, _AT_9AM)
        local = result.astimezone(_NY)
        assert local.date() == date(2026, 8, 11)
        assert local.hour == 9
        assert local.minute == 0

    def test_advances_two_days(self) -> None:
        recur = _make_recur(RecurUnit.day, interval=2)
        now = _utc(2026, 8, 10, 14, 0)
        result = next_occurrence(recur, now, _NY, _AT_9AM)
        local = result.astimezone(_NY)
        assert local.date() == date(2026, 8, 12)

    def test_result_strictly_after_now(self) -> None:
        recur = _make_recur(RecurUnit.day)
        now = _utc(2026, 8, 10, 14, 0)
        result = next_occurrence(recur, now, _NY, _AT_9AM)
        assert result > now

    def test_midnight_when_no_time_of_day(self) -> None:
        recur = _make_recur(RecurUnit.day)
        now = _utc(2026, 8, 10, 0, 0)
        result = next_occurrence(recur, now, _UTC_TZ)
        local = result.astimezone(_UTC_TZ)
        assert local.date() == date(2026, 8, 11)
        assert local.hour == 0
        assert local.minute == 0

    def test_missed_occurrence_does_not_accumulate(self) -> None:
        """AC8.6: after missing 5 days, next occurrence is tomorrow, not 5x."""
        recur = _make_recur(RecurUnit.day)
        # The original @not-before was 2026-08-05, and now is 5 days later.
        now = _utc(2026, 8, 10, 12, 0)
        result = next_occurrence(recur, now, _UTC_TZ, _AT_9AM)
        local = result.astimezone(_UTC_TZ)
        # Should be exactly 1 day from now, not 5 catch-up days.
        assert local.date() == date(2026, 8, 11)


# ---------------------------------------------------------------------------
# @every=1w — weekly
# ---------------------------------------------------------------------------


class TestWeeklyRecurrence:
    def test_advances_one_week(self) -> None:
        recur = _make_recur(RecurUnit.week)
        now = _utc(2026, 8, 10, 0, 0)  # Monday
        result = next_occurrence(recur, now, _UTC_TZ, _AT_9AM)
        local = result.astimezone(_UTC_TZ)
        assert local.date() == date(2026, 8, 17)

    def test_advances_two_weeks(self) -> None:
        recur = _make_recur(RecurUnit.week, interval=2)
        now = _utc(2026, 8, 10, 0, 0)
        result = next_occurrence(recur, now, _UTC_TZ, _AT_9AM)
        local = result.astimezone(_UTC_TZ)
        assert local.date() == date(2026, 8, 24)


# ---------------------------------------------------------------------------
# @every=1m — monthly
# ---------------------------------------------------------------------------


class TestMonthlyRecurrence:
    def test_advances_one_month(self) -> None:
        recur = _make_recur(RecurUnit.month)
        now = _utc(2026, 8, 10, 0, 0)
        result = next_occurrence(recur, now, _UTC_TZ, _AT_9AM)
        local = result.astimezone(_UTC_TZ)
        assert local.date() == date(2026, 9, 10)

    def test_clamps_end_of_month(self) -> None:
        """March 31 + 1 month → April 30 (clamped)."""
        recur = _make_recur(RecurUnit.month)
        now = _utc(2026, 3, 31, 0, 0)
        result = next_occurrence(recur, now, _UTC_TZ, _AT_9AM)
        local = result.astimezone(_UTC_TZ)
        assert local.date() == date(2026, 4, 30)

    def test_advances_two_months(self) -> None:
        recur = _make_recur(RecurUnit.month, interval=2)
        now = _utc(2026, 8, 10, 0, 0)
        result = next_occurrence(recur, now, _UTC_TZ, _AT_9AM)
        local = result.astimezone(_UTC_TZ)
        assert local.date() == date(2026, 10, 10)


# ---------------------------------------------------------------------------
# @every=weekday
# ---------------------------------------------------------------------------


class TestWeekdayRecurrence:
    def test_monday_gives_tuesday(self) -> None:
        recur = _make_recur(RecurUnit.weekday)
        # 2026-08-10 is Monday
        now = _utc(2026, 8, 10, 14, 0)
        result = next_occurrence(recur, now, _NY, _AT_9AM)
        local = result.astimezone(_NY)
        assert local.date() == date(2026, 8, 11)  # Tuesday

    def test_friday_gives_monday(self) -> None:
        recur = _make_recur(RecurUnit.weekday)
        # 2026-08-14 is Friday
        now = _utc(2026, 8, 14, 14, 0)
        result = next_occurrence(recur, now, _NY, _AT_9AM)
        local = result.astimezone(_NY)
        assert local.date() == date(2026, 8, 17)  # Monday

    def test_saturday_gives_monday(self) -> None:
        recur = _make_recur(RecurUnit.weekday)
        # 2026-08-15 is Saturday
        now = _utc(2026, 8, 15, 0, 0)
        result = next_occurrence(recur, now, _NY, _AT_9AM)
        local = result.astimezone(_NY)
        assert local.date() == date(2026, 8, 17)  # Monday

    def test_sunday_gives_monday(self) -> None:
        recur = _make_recur(RecurUnit.weekday)
        # 2026-08-16 is Sunday
        now = _utc(2026, 8, 16, 0, 0)
        result = next_occurrence(recur, now, _NY, _AT_9AM)
        local = result.astimezone(_NY)
        assert local.date() == date(2026, 8, 17)  # Monday

    def test_result_is_always_weekday(self) -> None:
        recur = _make_recur(RecurUnit.weekday)
        for day_offset in range(7):
            now = _utc(2026, 8, 10 + day_offset, 12, 0)
            result = next_occurrence(recur, now, _UTC_TZ, _AT_9AM)
            assert result.astimezone(_UTC_TZ).weekday() < 5


# ---------------------------------------------------------------------------
# @every=mon,thu — named days-of-week
# ---------------------------------------------------------------------------


class TestDOWRecurrence:
    def test_mon_thu_from_monday(self) -> None:
        recur = _make_recur(RecurUnit.dow, days=("mon", "thu"))
        # 2026-08-10 is Monday — next occurrence should be Thursday
        now = _utc(2026, 8, 10, 14, 0)
        result = next_occurrence(recur, now, _NY, _AT_9AM)
        local = result.astimezone(_NY)
        assert local.date() == date(2026, 8, 13)  # Thursday

    def test_mon_thu_from_thursday(self) -> None:
        recur = _make_recur(RecurUnit.dow, days=("mon", "thu"))
        # 2026-08-13 is Thursday — next should be Monday
        now = _utc(2026, 8, 13, 14, 0)
        result = next_occurrence(recur, now, _NY, _AT_9AM)
        local = result.astimezone(_NY)
        assert local.date() == date(2026, 8, 17)  # Monday

    def test_single_dow(self) -> None:
        recur = _make_recur(RecurUnit.dow, days=("wed",))
        now = _utc(2026, 8, 10, 0, 0)  # Monday
        result = next_occurrence(recur, now, _UTC_TZ, _AT_9AM)
        local = result.astimezone(_UTC_TZ)
        assert local.date() == date(2026, 8, 12)  # Wednesday

    def test_result_always_on_target_day(self) -> None:
        recur = _make_recur(RecurUnit.dow, days=("mon", "thu"))
        for day_offset in range(7):
            now = _utc(2026, 8, 10 + day_offset, 12, 0)
            result = next_occurrence(recur, now, _UTC_TZ, _AT_9AM)
            assert result.astimezone(_UTC_TZ).weekday() in (0, 3)  # 0=Mon, 3=Thu


# ---------------------------------------------------------------------------
# DST gap (spring forward) — required test
# ---------------------------------------------------------------------------


class TestDSTGap:
    """Recurrence through a DST spring-forward gap resolves forward."""

    # US/Eastern springs forward on the second Sunday in March.
    # In 2024 that is 2024-03-10: at 2:00am EST, clocks jump to 3:00am EDT.
    # Local times 2:00am–2:59am on that date do not exist.

    def test_daily_at_2_30am_through_dst_gap(self) -> None:
        """A daily task scheduled at 2:30am resolves forward through the gap."""
        recur = _make_recur(RecurUnit.day)
        # "now" is 2024-03-09 at 3am ET (8am UTC in EST = UTC-5)
        now = _utc(2024, 3, 9, 8, 0)  # 2024-03-09 03:00 EST
        result = next_occurrence(recur, now, _NY, _AT_230AM)

        # The next local date is 2024-03-10 (spring-forward day).
        # 2:30am does not exist; the result must be strictly after "now"
        # and must not be before the gap end (3:00am EDT = 7:00am UTC).
        assert result > now
        # The result must be on 2024-03-10 (not a day earlier or later than expected).
        local = result.astimezone(_NY)
        assert local.date() == date(2024, 3, 10)
        # Must be at or after the gap end (3:00am EDT).
        assert (local.hour, local.minute) >= (3, 0)

    def test_result_is_timezone_aware(self) -> None:
        recur = _make_recur(RecurUnit.day)
        now = _utc(2024, 3, 9, 8, 0)
        result = next_occurrence(recur, now, _NY, _AT_230AM)
        assert result.tzinfo is not None


# ---------------------------------------------------------------------------
# DST fold (fall back) — required test
# ---------------------------------------------------------------------------


class TestDSTFold:
    """Recurrence through a DST fall-back fold resolves to the later UTC instant."""

    # US/Eastern falls back on the first Sunday in November.
    # In 2024 that is 2024-11-03: at 2:00am EDT, clocks fall back to 1:00am EST.
    # Local time 1:00am–1:59am exists twice on that date.

    def test_daily_at_1_30am_through_dst_fold(self) -> None:
        """A daily task at 1:30am resolves to the later UTC instant in a fold."""
        recur = _make_recur(RecurUnit.day)
        # "now" is 2024-11-02 at 8:00am UTC (4am EDT, well before the fold)
        now = _utc(2024, 11, 2, 8, 0)
        _fold_time = dt_time(1, 30)
        result = next_occurrence(recur, now, _NY, _fold_time)

        # The target date is 2024-11-03 (fall-back day).
        # 1:30am exists twice; "resolve forward" → later UTC instant (fold=1).
        local = result.astimezone(_NY)
        assert local.date() == date(2024, 11, 3)
        assert local.hour == 1
        assert local.minute == 30
        # At fold=1 the offset is EST (UTC-5), so 1:30am EST = 6:30am UTC.
        # At fold=0 the offset is EDT (UTC-4), so 1:30am EDT = 5:30am UTC.
        # The later one is 6:30am UTC.
        assert result.astimezone(UTC).hour == 6
        assert result.astimezone(UTC).minute == 30

    def test_non_ambiguous_time_unaffected_by_fold(self) -> None:
        """A task at a time outside the fold window is unaffected."""
        recur = _make_recur(RecurUnit.day)
        now = _utc(2024, 11, 2, 8, 0)
        result = next_occurrence(recur, now, _NY, _AT_9AM)
        local = result.astimezone(_NY)
        assert local.date() == date(2024, 11, 3)
        assert local.hour == 9


# ---------------------------------------------------------------------------
# General: result is always strictly after "now"
# ---------------------------------------------------------------------------


class TestResultAlwaysAfterNow:
    @pytest.mark.parametrize(
        "unit,interval,days",
        [
            (RecurUnit.day, 1, ()),
            (RecurUnit.day, 3, ()),
            (RecurUnit.week, 1, ()),
            (RecurUnit.month, 1, ()),
            (RecurUnit.weekday, 1, ()),
            (RecurUnit.dow, 1, ("mon",)),
            (RecurUnit.dow, 1, ("mon", "thu")),
        ],
    )
    def test_always_strictly_after_now(
        self, unit: RecurUnit, interval: int, days: tuple[str, ...]
    ) -> None:
        recur = Recurrence(unit=unit, interval=interval, days=days)
        now = _utc(2026, 8, 10, 14, 30)  # arbitrary Monday afternoon UTC
        result = next_occurrence(recur, now, _NY, _AT_9AM)
        assert result > now, f"Expected result > now for unit={unit}, interval={interval}"

    def test_result_is_utc_aware(self) -> None:
        recur = _make_recur(RecurUnit.day)
        now = _utc(2026, 8, 10, 14, 0)
        result = next_occurrence(recur, now, _NY, _AT_9AM)
        assert result.tzinfo is not None
        assert result.utcoffset().total_seconds() == 0  # type: ignore[union-attr]
