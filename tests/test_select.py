# SPDX-License-Identifier: Apache-2.0
"""Tests for select.py — Stage 2 selection: all AC2.1–AC2.10."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jumar.models import Capability, ItemStatus, Recurrence, Schedule, TodoItem
from jumar.select import CycleError, select_next

# ---------------------------------------------------------------------------
# Frozen test clock
# ---------------------------------------------------------------------------

NOW = datetime(2026, 8, 7, 12, 0, 0, tzinfo=UTC)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_item(
    item_id: str,
    *,
    line_no: int = 1,
    priority: int | None = None,
    depends: tuple[str, ...] = (),
    status: ItemStatus = ItemStatus.pending,
    schedule: Schedule | None = None,
) -> TodoItem:
    return TodoItem(
        item_id=item_id,
        text=f"Do {item_id}",
        raw_line=f"- [ ] Do {item_id}\n",
        line_no=line_no,
        status=status,
        context=(),
        authored_subtasks=(),
        meta={},
        priority=priority,
        depends=depends,
        capabilities=frozenset({Capability.read_fs}),
        schedule=schedule,
    )


def _make_schedule(
    not_before: str | None = None,
    due: str | None = None,
    recur: Recurrence | None = None,
    tz: str = "UTC",
) -> Schedule:
    return Schedule(
        not_before=not_before,
        not_before_literal=not_before,
        due=due,
        due_literal=due,
        recur=recur,
        tz=tz,
    )


# ---------------------------------------------------------------------------
# AC2.1 — File order with no priorities
# ---------------------------------------------------------------------------


def test_file_order_no_priorities() -> None:
    items = [
        _make_item("a", line_no=1),
        _make_item("b", line_no=2),
        _make_item("c", line_no=3),
    ]
    result = select_next(items, now=NOW)
    assert result.selected is not None
    assert result.selected.item_id == "a"


def test_file_order_selects_first_of_two() -> None:
    items = [_make_item("x", line_no=5), _make_item("y", line_no=10)]
    result = select_next(items, now=NOW)
    assert result.selected is not None
    assert result.selected.item_id == "x"


# ---------------------------------------------------------------------------
# AC2.2 — @priority=1 beats an earlier unprioritised item
# ---------------------------------------------------------------------------


def test_priority_beats_file_order() -> None:
    items = [
        _make_item("first", line_no=1, priority=None),
        _make_item("urgent", line_no=2, priority=1),
    ]
    result = select_next(items, now=NOW)
    assert result.selected is not None
    assert result.selected.item_id == "urgent"


def test_lower_priority_number_wins() -> None:
    items = [
        _make_item("medium", line_no=1, priority=5),
        _make_item("high", line_no=2, priority=2),
    ]
    result = select_next(items, now=NOW)
    assert result.selected is not None
    assert result.selected.item_id == "high"


# ---------------------------------------------------------------------------
# AC2.3 — Depending on unfinished item → skipped and listed as blocked
# ---------------------------------------------------------------------------


def test_dependency_on_unfinished_blocks_item() -> None:
    a = _make_item("a", line_no=1)
    b = _make_item("b", line_no=2, depends=("a",))
    result = select_next([a, b], now=NOW)
    assert result.selected is not None
    assert result.selected.item_id == "a"
    blocked_ids = [item.item_id for item, _ in result.blocked]
    assert "b" in blocked_ids


def test_blocked_item_reason_names_dependency() -> None:
    a = _make_item("prerequisite", line_no=1)
    b = _make_item("dependent", line_no=2, depends=("prerequisite",))
    result = select_next([a, b], now=NOW)
    _, reason = result.blocked[0]
    assert "prerequisite" in reason


def test_all_items_blocked_selects_none() -> None:
    # If every item depends on something unfinished, nothing is eligible.
    a = _make_item("a", line_no=1, depends=("b",))
    b = _make_item("b", line_no=2, depends=("a",))
    # This is actually a cycle — it will raise CycleError.
    with pytest.raises(CycleError):
        select_next([a, b], now=NOW)


def test_dependency_done_in_source_unblocks() -> None:
    a = _make_item("a", line_no=1, status=ItemStatus.done)
    b = _make_item("b", line_no=2, depends=("a",))
    result = select_next([a, b], now=NOW)
    assert result.selected is not None
    assert result.selected.item_id == "b"
    assert result.blocked == []


# ---------------------------------------------------------------------------
# AC2.4 — Dependency cycle → CycleError naming the cycle
# ---------------------------------------------------------------------------


def test_two_item_cycle_raises() -> None:
    a = _make_item("alpha", depends=("beta",))
    b = _make_item("beta", depends=("alpha",))
    with pytest.raises(CycleError) as exc_info:
        select_next([a, b], now=NOW)
    cycle = exc_info.value.cycle
    assert "alpha" in cycle
    assert "beta" in cycle


def test_three_item_cycle_raises() -> None:
    a = _make_item("a", depends=("c",))
    b = _make_item("b", depends=("a",))
    c = _make_item("c", depends=("b",))
    with pytest.raises(CycleError) as exc_info:
        select_next([a, b, c], now=NOW)
    cycle = exc_info.value.cycle
    assert len(cycle) >= 3


def test_cycle_raises_before_any_selection() -> None:
    # Even with an eligible item d, the cycle in a/b/c must be caught first.
    a = _make_item("a", line_no=1, depends=("b",))
    b = _make_item("b", line_no=2, depends=("a",))
    d = _make_item("d", line_no=3)
    with pytest.raises(CycleError):
        select_next([a, b, d], now=NOW)


def test_self_cycle_raises() -> None:
    a = _make_item("self", depends=("self",))
    with pytest.raises(CycleError):
        select_next([a], now=NOW)


# ---------------------------------------------------------------------------
# AC2.5 — done_ids and source-done items are not re-selected
# ---------------------------------------------------------------------------


def test_journal_done_not_reselected() -> None:
    items = [_make_item("a"), _make_item("b")]
    result = select_next(items, now=NOW, done_ids=frozenset({"a"}))
    assert result.selected is not None
    assert result.selected.item_id == "b"


def test_source_done_not_reselected() -> None:
    items = [
        _make_item("a", status=ItemStatus.done),
        _make_item("b"),
    ]
    result = select_next(items, now=NOW)
    assert result.selected is not None
    assert result.selected.item_id == "b"


def test_all_done_returns_none() -> None:
    items = [_make_item("a"), _make_item("b")]
    result = select_next(items, now=NOW, done_ids=frozenset({"a", "b"}))
    assert result.selected is None
    assert result.deferred == []
    assert result.blocked == []


def test_done_in_journal_used_as_dependency() -> None:
    # When a dep is in done_ids, the dependent becomes eligible.
    a = _make_item("a", line_no=1)
    b = _make_item("b", line_no=2, depends=("a",))
    result = select_next([a, b], now=NOW, done_ids=frozenset({"a"}))
    assert result.selected is not None
    assert result.selected.item_id == "b"


# ---------------------------------------------------------------------------
# AC2.6 — not_before in future → deferred; in past → selected
# ---------------------------------------------------------------------------


def test_not_before_future_defers_item() -> None:
    future_ts = "2030-01-01T00:00:00+00:00"
    item = _make_item("a", schedule=_make_schedule(not_before=future_ts))
    result = select_next([item], now=NOW)
    assert result.selected is None
    assert len(result.deferred) == 1
    deferred_item, eligible_at = result.deferred[0]
    assert deferred_item.item_id == "a"
    assert eligible_at == future_ts


def test_not_before_past_selects_item() -> None:
    past_ts = "2020-01-01T00:00:00+00:00"
    item = _make_item("a", schedule=_make_schedule(not_before=past_ts))
    result = select_next([item], now=NOW)
    assert result.selected is not None
    assert result.selected.item_id == "a"
    assert result.deferred == []


def test_not_before_equal_to_now_is_eligible() -> None:
    now_ts = NOW.isoformat()
    item = _make_item("a", schedule=_make_schedule(not_before=now_ts))
    result = select_next([item], now=NOW)
    assert result.selected is not None
    assert result.selected.item_id == "a"


# ---------------------------------------------------------------------------
# AC2.7 — Overdue item is selected ahead of non-overdue item with better priority
# ---------------------------------------------------------------------------


def test_overdue_beats_better_priority() -> None:
    overdue_ts = "2026-08-06T00:00:00+00:00"  # yesterday — overdue
    overdue_item = _make_item(
        "overdue",
        line_no=2,
        priority=10,
        schedule=_make_schedule(due=overdue_ts),
    )
    high_pri = _make_item("high_pri", line_no=1, priority=1)
    result = select_next([high_pri, overdue_item], now=NOW)
    assert result.selected is not None
    assert result.selected.item_id == "overdue"


def test_most_overdue_selected_first() -> None:
    very_overdue_ts = "2026-08-01T00:00:00+00:00"  # 6 days ago
    slightly_overdue_ts = "2026-08-06T00:00:00+00:00"  # 1 day ago
    very = _make_item("very", line_no=2, schedule=_make_schedule(due=very_overdue_ts))
    slight = _make_item("slight", line_no=1, schedule=_make_schedule(due=slightly_overdue_ts))
    result = select_next([slight, very], now=NOW)
    assert result.selected is not None
    assert result.selected.item_id == "very"


def test_non_overdue_sorted_by_priority_then_due() -> None:
    due_soon = "2026-08-10T00:00:00+00:00"
    due_later = "2026-08-20T00:00:00+00:00"
    p1_late = _make_item("p1_late", line_no=1, priority=1, schedule=_make_schedule(due=due_later))
    p2_soon = _make_item("p2_soon", line_no=2, priority=2, schedule=_make_schedule(due=due_soon))
    result = select_next([p1_late, p2_soon], now=NOW)
    assert result.selected is not None
    # priority 1 wins over priority 2 even though due_later
    assert result.selected.item_id == "p1_late"


# ---------------------------------------------------------------------------
# AC2.8 — now is frozen: eligibility does not change between calls
# ---------------------------------------------------------------------------


def test_now_is_frozen_across_calls() -> None:
    # Item's not_before is between two "real" clock readings,
    # but since now is frozen at the same value both calls must agree.
    not_before_ts = "2026-08-07T14:00:00+00:00"  # 2 h after NOW
    item = _make_item("a", schedule=_make_schedule(not_before=not_before_ts))

    frozen_now = NOW  # 12:00 — item not yet eligible
    result1 = select_next([item], now=frozen_now)
    result2 = select_next([item], now=frozen_now)

    assert result1.selected is None
    assert result2.selected is None
    assert result1.deferred[0][0].item_id == "a"
    assert result2.deferred[0][0].item_id == "a"


def test_now_frozen_while_another_item_runs() -> None:
    # Simulates a long-running second item that would make a third eligible.
    not_before_ts = "2026-08-07T13:00:00+00:00"  # 1 h after NOW
    item_a = _make_item("a", line_no=1)
    item_b = _make_item("b", line_no=2, schedule=_make_schedule(not_before=not_before_ts))

    frozen_now = NOW  # 12:00 — b not yet eligible

    # First call (a is selected)
    r1 = select_next([item_a, item_b], now=frozen_now)
    assert r1.selected is not None and r1.selected.item_id == "a"

    # Second call with same frozen_now (a is now in done_ids; b still not eligible)
    r2 = select_next([item_a, item_b], now=frozen_now, done_ids=frozenset({"a"}))
    assert r2.selected is None
    assert any(it.item_id == "b" for it, _ in r2.deferred)


# ---------------------------------------------------------------------------
# AC2.9 — Item depending on a deferred item is itself blocked;
#          the report names the dependency's eligibility instant.
# ---------------------------------------------------------------------------


def test_blocked_item_names_deferred_deps_instant() -> None:
    future_ts = "2026-09-01T00:00:00+00:00"
    dep = _make_item("dep", line_no=1, schedule=_make_schedule(not_before=future_ts))
    waiting = _make_item("waiting", line_no=2, depends=("dep",))

    result = select_next([dep, waiting], now=NOW)
    assert result.selected is None

    # dep is deferred
    assert any(it.item_id == "dep" for it, _ in result.deferred)

    # waiting is blocked and the reason names the deferred instant
    assert len(result.blocked) == 1
    blocked_item, reason = result.blocked[0]
    assert blocked_item.item_id == "waiting"
    assert future_ts in reason


def test_blocked_item_not_in_deferred_list() -> None:
    future_ts = "2026-09-01T00:00:00+00:00"
    dep = _make_item("dep", schedule=_make_schedule(not_before=future_ts))
    waiting = _make_item("waiting", depends=("dep",))

    result = select_next([dep, waiting], now=NOW)
    deferred_ids = [it.item_id for it, _ in result.deferred]
    # 'waiting' must not appear in the deferred list — it is blocked, not deferred
    assert "waiting" not in deferred_ids


# ---------------------------------------------------------------------------
# AC2.10 — All items deferred → exit cleanly with next eligibility instant
# ---------------------------------------------------------------------------


def test_all_deferred_no_selected_item() -> None:
    t1 = "2026-09-01T00:00:00+00:00"
    t2 = "2026-10-01T00:00:00+00:00"
    a = _make_item("a", schedule=_make_schedule(not_before=t1))
    b = _make_item("b", schedule=_make_schedule(not_before=t2))

    result = select_next([a, b], now=NOW)
    assert result.selected is None
    assert len(result.deferred) == 2
    assert result.blocked == []


def test_all_deferred_reports_earliest_eligible_instant() -> None:
    t1 = "2026-09-01T00:00:00+00:00"  # earlier
    t2 = "2026-10-01T00:00:00+00:00"  # later
    a = _make_item("a", schedule=_make_schedule(not_before=t1))
    b = _make_item("b", schedule=_make_schedule(not_before=t2))

    result = select_next([a, b], now=NOW)
    assert result.next_eligible_at is not None
    # The earliest deferred instant is t1
    nea = datetime.fromisoformat(result.next_eligible_at)
    if nea.tzinfo is None:
        nea = nea.replace(tzinfo=UTC)
    t1_dt = datetime.fromisoformat(t1)
    assert nea == t1_dt


def test_next_eligible_at_none_when_no_deferred() -> None:
    items = [_make_item("a"), _make_item("b")]
    result = select_next(items, now=NOW)
    assert result.next_eligible_at is None


# ---------------------------------------------------------------------------
# Additional negative cases
# ---------------------------------------------------------------------------


def test_empty_item_list_returns_none() -> None:
    result = select_next([], now=NOW)
    assert result.selected is None
    assert result.deferred == []
    assert result.blocked == []
    assert result.next_eligible_at is None


def test_failed_status_not_selected() -> None:
    items = [
        _make_item("a", status=ItemStatus.failed),
        _make_item("b"),
    ]
    result = select_next(items, now=NOW)
    assert result.selected is not None
    assert result.selected.item_id == "b"


def test_skipped_by_human_not_selected() -> None:
    items = [
        _make_item("a", status=ItemStatus.skipped_by_human),
        _make_item("b"),
    ]
    result = select_next(items, now=NOW)
    assert result.selected is not None
    assert result.selected.item_id == "b"


def test_external_dependency_unknown_id_blocks() -> None:
    # Depending on an id not in the item list is treated as "not done".
    item = _make_item("a", depends=("nonexistent-id",))
    result = select_next([item], now=NOW)
    assert result.selected is None
    assert len(result.blocked) == 1
    blocked_item, reason = result.blocked[0]
    assert blocked_item.item_id == "a"
    assert "nonexistent-id" in reason
