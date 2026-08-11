# SPDX-License-Identifier: Apache-2.0
"""Item-centric status view aggregated from the todo file and all run journals.

Public API
----------
ItemStatusLine  – one row in the status output.
StatusReport    – full status view across all items.
build_status()  – scan the todo file and runs dir; return StatusReport.
format_status() – human-readable one-line-per-item summary.

States
------
done            – checkbox is checked in the todo file.
parked          – item carries a @paused= token; ineligible until removed by hand.
deferred        – not_before is in the future; next eligibility included.
blocked         – unmet @depends= dependency.
eligible        – pending, no gate.
never-attempted – eligible but no journal entry has ever touched this item.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class ItemStatusLine:
    """One item's current state as seen by ``jumar status``."""

    item_id: str
    text: str
    state: str  # done | parked | deferred | blocked | eligible | never-attempted
    paused_reason: str | None = None  # non-None when state=parked
    failure_count: int = 0  # @failed=N count from the todo line
    last_run_id: str | None = None
    last_run_at: str | None = None  # ISO-8601 UTC of run_started
    last_outcome: str | None = None  # done | failed | in_progress | deferred
    failed_subtask_desc: str | None = None  # description of failing subtask
    failed_check_kind: str | None = None  # check kind of failing subtask
    next_eligible_at: str | None = None  # for deferred items


@dataclass
class StatusReport:
    """Aggregated status across all todo items."""

    todo_path: str
    items: list[ItemStatusLine] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Journal scanning helpers
# ---------------------------------------------------------------------------


def _parse_journal_lines(path: Path) -> list[dict[str, Any]]:
    """Parse journal.jsonl with corrupt-line tolerance (stop at first bad line)."""
    entries: list[dict[str, Any]] = []
    if not path.exists():
        return entries
    try:
        with path.open(encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.rstrip("\n")
                if not raw:
                    continue
                try:
                    entries.append(json.loads(raw))
                except json.JSONDecodeError:
                    break  # tolerate corrupt trailing line; stop here
    except OSError:
        pass
    return entries


@dataclass
class _RunAttempt:
    """Everything we know about one run's handling of one item."""

    run_id: str
    run_started_at: str  # ISO-8601 of run_started event
    outcome: str  # done | failed | in_progress | deferred
    failed_subtask_desc: str | None
    failed_check_kind: str | None


def _scan_runs(runs_dir: Path) -> dict[str, _RunAttempt]:
    """Return the *latest* attempt info per item_id across all run journals.

    Journals that do not parse or are missing are silently skipped.
    Only the run with the latest ``run_started`` timestamp wins per item.
    """
    latest: dict[str, _RunAttempt] = {}

    if not runs_dir.exists():
        return latest

    for run_dir in sorted(runs_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        journal_path = run_dir / "journal.jsonl"
        if not journal_path.exists():
            continue

        entries = _parse_journal_lines(journal_path)
        if not entries:
            continue

        # Extract run-level metadata.
        run_id = run_dir.name
        run_started_at: str | None = None
        for entry in entries:
            if entry.get("event") == "run_started":
                start_payload = entry.get("payload") or {}
                run_started_at = start_payload.get("now")
                break

        if run_started_at is None:
            continue

        # Per-item state within this run.
        item_outcomes: dict[str, str] = {}
        item_failure_codes: dict[str, str] = {}
        item_failed_subtask_idx: dict[str, int] = {}
        plan_subtasks: dict[str, dict[int, dict[str, str]]] = {}  # item_id -> {idx -> {desc, kind}}

        for entry in entries:
            event = entry.get("event", "")
            payload: dict[str, Any] = entry.get("payload") or {}
            item_id: str | None = entry.get("item_id") or payload.get("item_id") or None

            if event == "item_selected" and item_id:
                item_outcomes.setdefault(item_id, "in_progress")

            elif event == "item_completed" and item_id:
                item_outcomes[item_id] = "done"

            elif event == "item_failed" and item_id:
                item_outcomes[item_id] = "failed"
                fc = payload.get("failure_code")
                if fc:
                    item_failure_codes[item_id] = str(fc)
                fsi = payload.get("failed_subtask_index")
                if fsi is not None:
                    item_failed_subtask_idx[item_id] = int(fsi)

            elif event == "item_deferred" and item_id:
                item_outcomes.setdefault(item_id, "deferred")

            elif event == "plan_created" and item_id:
                raw_subtasks: list[Any] = payload.get("subtasks") or []
                subtask_map: dict[int, dict[str, str]] = {}
                for raw_st in raw_subtasks:
                    if not isinstance(raw_st, dict):
                        continue
                    idx = int(raw_st.get("index", 0))
                    check: dict[str, Any] = raw_st.get("check") or {}
                    subtask_map[idx] = {
                        "desc": str(raw_st.get("description", "")),
                        "kind": str(check.get("kind", "")),
                    }
                plan_subtasks[item_id] = subtask_map

        # For each item in this run, compare to existing latest.
        for item_id, outcome in item_outcomes.items():
            existing = latest.get(item_id)
            if existing is not None:
                try:
                    existing_ts = datetime.fromisoformat(existing.run_started_at)
                    this_ts = datetime.fromisoformat(run_started_at)
                    if existing_ts >= this_ts:
                        continue  # existing is newer or equal — keep it
                except ValueError:
                    pass  # unparseable timestamp; overwrite with this one

            # Resolve failing subtask details.
            failed_desc: str | None = None
            failed_kind: str | None = None
            if outcome == "failed":
                fsi = item_failed_subtask_idx.get(item_id)
                if fsi is not None and item_id in plan_subtasks:
                    st_info = plan_subtasks[item_id].get(fsi)
                    if st_info:
                        failed_desc = st_info["desc"] or None
                        failed_kind = st_info["kind"] or None

            latest[item_id] = _RunAttempt(
                run_id=run_id,
                run_started_at=run_started_at,
                outcome=outcome,
                failed_subtask_desc=failed_desc,
                failed_check_kind=failed_kind,
            )

    return latest


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_status(
    todo_path: Path,
    runs_dir: Path,
    config: Any,
    now: datetime,
) -> StatusReport:
    """Build a :class:`StatusReport` by aggregating the todo file and all journals.

    Parameters
    ----------
    todo_path:
        Path to the todo Markdown file.
    runs_dir:
        Directory containing ``<run-id>/journal.jsonl`` subdirectories.
    config:
        Loaded :class:`~jumar.config.Config`.
    now:
        Current eligibility instant (timezone-aware UTC).

    Returns
    -------
    StatusReport
        Never raises; corrupt journals degrade the affected item's row to what
        is known.  Exit 0 always — status reports state, it does not judge it.
    """
    from .ingest import IngestError, ingest
    from .select import CycleError, select_next

    # Ingest the todo file.  If it is missing, return an empty report rather
    # than crashing — the caller can check items == [].
    try:
        ingest_result = ingest(todo_path, config)
    except IngestError:
        return StatusReport(todo_path=str(todo_path))

    items = ingest_result.items

    # Determine blocked / deferred state via select_next (same logic as the run).
    # CycleError is caught and treated as "unknown" — we cannot gate accurately,
    # but we still show everything we know.
    try:
        sel = select_next(items, now)
    except CycleError:
        sel = None

    deferred_ids: dict[str, str] = {}
    blocked_ids: dict[str, str] = {}
    if sel is not None:
        for item, eligible_at in sel.deferred:
            deferred_ids[item.item_id] = eligible_at
        for item, reason in sel.blocked:
            blocked_ids[item.item_id] = reason

    # Scan journals once for all items.
    attempts = _scan_runs(runs_dir)

    lines: list[ItemStatusLine] = []

    for item in items:
        # --- derive state ---
        paused_reason: str | None = item.meta.get("paused") or None

        if paused_reason is not None:
            state = "parked"
        elif item.status.value == "done":
            state = "done"
        elif item.item_id in deferred_ids:
            state = "deferred"
        elif item.item_id in blocked_ids:
            state = "blocked"
        else:
            attempt = attempts.get(item.item_id)
            state = "never-attempted" if attempt is None else "eligible"

        # --- failure count from @failed= token ---
        failure_count = 0
        raw_failed = item.meta.get("failed")
        if raw_failed is not None:
            with contextlib.suppress(ValueError):
                failure_count = int(raw_failed)

        # --- last attempt info ---
        attempt = attempts.get(item.item_id)
        line = ItemStatusLine(
            item_id=item.item_id,
            text=item.text,
            state=state,
            paused_reason=paused_reason,
            failure_count=failure_count,
            last_run_id=attempt.run_id if attempt else None,
            last_run_at=attempt.run_started_at if attempt else None,
            last_outcome=attempt.outcome if attempt else None,
            failed_subtask_desc=(attempt.failed_subtask_desc if attempt else None),
            failed_check_kind=(attempt.failed_check_kind if attempt else None),
            next_eligible_at=deferred_ids.get(item.item_id),
        )
        lines.append(line)

    return StatusReport(todo_path=str(todo_path), items=lines)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def format_status_json(report: StatusReport) -> str:
    """Return a JSON document representing the status report (AC11.5, AC11.6)."""
    return json.dumps(dataclasses.asdict(report), indent=2)


def format_status(report: StatusReport) -> str:
    """Return a human-readable one-line-per-item status table."""
    if not report.items:
        return f"No items found in {report.todo_path}"

    output_lines: list[str] = [f"Status for: {report.todo_path}", ""]

    for item in report.items:
        state_label = item.state.upper()
        line = f"  [{state_label:<16}] {item.text}"

        if item.state == "parked" and item.paused_reason:
            line += f"  (paused: {item.paused_reason})"
        if item.failure_count:
            line += f"  [failures: {item.failure_count}]"
        if item.state == "deferred" and item.next_eligible_at:
            line += f"  [eligible: {item.next_eligible_at}]"
        if item.state == "blocked" and item.last_outcome is None:
            # blocked reason is not stored in ItemStatusLine; caller knows from context
            pass

        output_lines.append(line)

        if item.last_run_id:
            attempt_line = f"    last: run={item.last_run_id}"
            if item.last_run_at:
                attempt_line += f"  at={item.last_run_at}"
            if item.last_outcome:
                attempt_line += f"  outcome={item.last_outcome}"
            output_lines.append(attempt_line)

        if item.state == "failed" or (item.last_outcome == "failed"):
            if item.failed_subtask_desc:
                output_lines.append(f"    failing subtask: {item.failed_subtask_desc}")
            if item.failed_check_kind:
                output_lines.append(f"    check kind: {item.failed_check_kind}")

    return "\n".join(output_lines)
