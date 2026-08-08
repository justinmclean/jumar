# SPDX-License-Identifier: Apache-2.0
"""Stage 2 — Select: choose the single next eligible TodoItem.

Public API
----------
CycleError    – raised when @depends creates a cycle; .cycle names the item_ids.
SelectResult  – output of select_next().
select_next() – apply time+dependency gates, order, and return one item.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from getstuffdone.models import ItemStatus, TodoItem

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class CycleError(Exception):
    """Raised when @depends forms a cycle before any item is executed.

    Attributes
    ----------
    cycle : tuple[str, ...]
        The item_ids that form the cycle.  The first id repeats at the end.
    """

    def __init__(self, cycle: tuple[str, ...]) -> None:
        self.cycle = cycle
        super().__init__("Dependency cycle detected: " + " -> ".join(cycle))


@dataclass
class SelectResult:
    """Output of select_next()."""

    selected: TodoItem | None
    """The item to work on next; None when nothing is eligible."""

    deferred: list[tuple[TodoItem, str]]
    """Items gated by not_before: (item, eligible_at_utc ISO-8601)."""

    blocked: list[tuple[TodoItem, str]]
    """Items blocked by an unfinished/deferred dependency: (item, reason)."""

    parked: list[tuple[TodoItem, str]]
    """Items with @paused= token: (item, reason).  Never selected."""

    next_eligible_at: str | None
    """Earliest future eligibility instant across all deferred items (ISO-8601 UTC)."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_utc(ts: str) -> datetime:
    """Parse an ISO-8601 string into a timezone-aware datetime."""
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


# Statuses that permanently exclude an item from selection.
_INELIGIBLE_STATUSES: frozenset[ItemStatus] = frozenset(
    {
        ItemStatus.done,
        ItemStatus.failed,
        ItemStatus.blocked,
        ItemStatus.skipped_by_human,
        ItemStatus.inconclusive,
        ItemStatus.deferred,
    }
)


# ---------------------------------------------------------------------------
# Cycle detection (iterative DFS)
# ---------------------------------------------------------------------------


def _detect_cycles(by_id: dict[str, TodoItem]) -> None:
    """Raise CycleError if any @depends cycle exists among items in by_id.

    Uses an iterative DFS with white/gray/black colouring to avoid Python's
    recursion limit on long dependency chains.
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {iid: WHITE for iid in by_id}

    for start in list(by_id):
        if color[start] != WHITE:
            continue

        # Stack entries: (node, path_leading_to_node, remaining_deps_to_visit)
        start_item = by_id[start]
        stack: list[tuple[str, list[str], list[str]]] = [(start, [], list(start_item.depends))]
        color[start] = GRAY

        while stack:
            node, path, remaining = stack[-1]
            cur_path = path + [node]

            if remaining:
                dep = remaining.pop(0)
                if dep not in color:
                    # External dependency not in our item list — cannot form a cycle.
                    continue
                if color[dep] == GRAY:
                    cycle_start = cur_path.index(dep)
                    raise CycleError(tuple(cur_path[cycle_start:]) + (dep,))
                if color[dep] == WHITE:
                    dep_item = by_id[dep]
                    color[dep] = GRAY
                    stack.append((dep, cur_path, list(dep_item.depends)))
            else:
                stack.pop()
                color[node] = BLACK


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def select_next(
    items: Sequence[TodoItem],
    now: datetime,
    done_ids: frozenset[str] = frozenset(),
) -> SelectResult:
    """Choose the single next item to work on.

    Parameters
    ----------
    items:
        All items parsed by ingest, in file order.  Order determines
        tiebreaks when priority and due are equal.
    now:
        The run's single eligibility instant — frozen at run_started and
        never re-read from the system clock.  Passing the same ``now`` on
        every call within a run ensures items that become eligible *during*
        a long run are not selected until the next run.
    done_ids:
        item_ids already completed or failed in the journal for this run.
        These are never re-selected even if their source status is pending.

    Returns
    -------
    SelectResult

    Raises
    ------
    CycleError
        When @depends forms a cycle.  Raised before any eligibility check
        so no item is dispatched if the dependency graph is corrupt.
    """
    by_id: dict[str, TodoItem] = {item.item_id: item for item in items}

    # Cycle check first — fail before any selection work.
    _detect_cycles(by_id)

    deferred: list[tuple[TodoItem, str]] = []
    blocked: list[tuple[TodoItem, str]] = []
    parked: list[tuple[TodoItem, str]] = []
    eligible: list[TodoItem] = []

    def _is_done(item_id: str) -> bool:
        if item_id in done_ids:
            return True
        dep = by_id.get(item_id)
        return dep is not None and dep.status is ItemStatus.done

    def _deferred_instant(item_id: str) -> str | None:
        """Return the not_before ISO string if the dep is currently deferred."""
        dep = by_id.get(item_id)
        if dep is None:
            return None
        if dep.schedule and dep.schedule.not_before:
            nb = _parse_utc(dep.schedule.not_before)
            if nb > now:
                return dep.schedule.not_before
        return None

    for item in items:
        # Done items (source or journal) are never re-selected.
        if item.status is ItemStatus.done or item.item_id in done_ids:
            continue

        # Parked items (@paused= token) are excluded before all other gates.
        paused_reason = item.meta.get("paused")
        if paused_reason:
            parked.append((item, str(paused_reason)))
            continue

        # Only pending / in_progress items are candidates.
        if item.status in _INELIGIBLE_STATUSES:
            continue

        # ── Time gate ──────────────────────────────────────────────────────
        if item.schedule and item.schedule.not_before:
            nb = _parse_utc(item.schedule.not_before)
            if nb > now:
                deferred.append((item, item.schedule.not_before))
                continue

        # ── Dependency gate ────────────────────────────────────────────────
        dep_blocker: str | None = None
        dep_eligible_at: str | None = None
        for dep_id in item.depends:
            if not _is_done(dep_id):
                dep_eligible_at = _deferred_instant(dep_id)
                dep_blocker = dep_id
                break

        if dep_blocker is not None:
            if dep_eligible_at:
                reason = f"depends on {dep_blocker} which is deferred until {dep_eligible_at}"
            else:
                reason = f"depends on unfinished {dep_blocker}"
            blocked.append((item, reason))
            continue

        eligible.append(item)

    # ── Ordering ───────────────────────────────────────────────────────────
    # Key: (overdue_group, overdue_rank, priority_rank, due_rank, file_order)
    #
    # overdue_group = 0 for overdue items (due < now), 1 for others — overdue first.
    # overdue_rank  = due timestamp ascending among overdue items (most overdue = smallest).
    # priority_rank = priority ascending; None → float('inf') (lowest priority).
    # due_rank      = due timestamp ascending; None → float('inf').
    # file_order    = line_no.

    def _sort_key(item: TodoItem) -> tuple[int, float, float, float, int]:
        due_ts: float | None = None
        is_overdue = False
        if item.schedule and item.schedule.due:
            due_dt = _parse_utc(item.schedule.due)
            due_ts = due_dt.timestamp()
            if due_dt < now:
                is_overdue = True

        overdue_group = 0 if is_overdue else 1
        overdue_rank = due_ts if (is_overdue and due_ts is not None) else 0.0
        pri_rank: float = float(item.priority) if item.priority is not None else float("inf")
        due_rank: float = due_ts if due_ts is not None else float("inf")

        return (overdue_group, overdue_rank, pri_rank, due_rank, item.line_no)

    eligible.sort(key=_sort_key)

    # ── Next eligibility instant ───────────────────────────────────────────
    next_eligible_at: str | None = None
    if deferred:
        earliest = min(_parse_utc(ts) for _, ts in deferred)
        next_eligible_at = earliest.isoformat()

    return SelectResult(
        selected=eligible[0] if eligible else None,
        deferred=deferred,
        blocked=blocked,
        parked=parked,
        next_eligible_at=next_eligible_at,
    )
