# SPDX-License-Identifier: Apache-2.0
"""Stage 9 — Report: read the run journal and produce runs/<run-id>/report.md.

Public API
----------
ReportSubtask  – one subtask with its verdict and evidence summary.
ReportItem     – one item with status, overdue flag, subtasks.
RunReport      – full run report built from the journal.
build_report() – parse journal.jsonl and build a RunReport.
format_summary() – one-paragraph summary for stdout.
format_markdown() – full Markdown report.
write_report() – write report.md to runs/<run-id>/.
report_exit_status() – 0 if clean run, 1 if any failure.

Journal event shapes consumed
------------------------------
run_started   payload: {now, tz, mode, todo_path}
item_selected payload: {text, due?, is_overdue?}
item_deferred payload: {text, eligible_at}
item_blocked  payload: {text, reason}
plan_created  payload: {subtasks: [{subtask_id, index, description, check:{kind, statement}}, ...]}
verification  subtask_id, payload: {verdict, summary}
item_completed item_id
item_failed   item_id, payload: {failure_code?, failed_subtask_index?}
run_finished  (presence only)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class ReportSubtask:
    """One subtask in a plan, with its verification outcome."""

    index: int
    subtask_id: str
    description: str
    check_kind: str
    check_statement: str
    verdict: str | None = None
    evidence_summary: str | None = None


@dataclass
class ReportItem:
    """One todo item's outcome in the report."""

    item_id: str
    text: str
    status: str  # done | failed | in_progress | deferred | blocked | skipped_by_human
    is_overdue: bool = False
    due: str | None = None
    subtasks: list[ReportSubtask] = field(default_factory=list)
    failure_code: str | None = None
    failed_subtask_index: int | None = None
    eligible_at: str | None = None  # non-None only for deferred
    blocked_reason: str | None = None  # non-None only for blocked


@dataclass
class RunReport:
    """Full report built from a run's journal.jsonl."""

    run_id: str
    now: str | None
    tz: str | None
    todo_path: str | None
    mode: str | None
    truncated: bool  # True if journal had a corrupt trailing line
    interrupted: bool  # True if no run_finished event was found
    items: list[ReportItem] = field(default_factory=list)
    next_eligible_at: str | None = None  # earliest future eligibility across deferred items


# ---------------------------------------------------------------------------
# Journal parsing
# ---------------------------------------------------------------------------


def _parse_journal(path: Path) -> tuple[list[dict[str, Any]], bool]:
    """Return (entries, truncated) from a journal.jsonl file."""
    entries: list[dict[str, Any]] = []
    truncated = False
    if not path.exists():
        return entries, truncated
    with path.open(encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.rstrip("\n")
            if not raw:
                continue
            try:
                entries.append(json.loads(raw))
            except json.JSONDecodeError:
                truncated = True
                break
    return entries, truncated


def build_report(run_dir: Path) -> RunReport:
    """Parse ``runs/<run-id>/journal.jsonl`` and construct a :class:`RunReport`.

    Tolerates truncated journals (AC9.3): truncated means the last line was
    corrupt, but all prior entries are used.  ``RunReport.truncated`` and
    ``RunReport.interrupted`` are both set when the journal ends before a
    ``run_finished`` event.
    """
    journal_path = run_dir / "journal.jsonl"
    run_id = run_dir.name

    entries, truncated = _parse_journal(journal_path)

    # Run-level metadata.
    now: str | None = None
    tz: str | None = None
    todo_path: str | None = None
    mode: str | None = None
    run_finished = False

    # Per-item tracking.
    # Items that were selected for execution (item_selected event).
    selected_text: dict[str, str] = {}
    selected_overdue: dict[str, bool] = {}
    selected_due: dict[str, str | None] = {}
    item_status: dict[str, str] = {}

    # Plan subtasks per item (from plan_created).
    item_subtasks: dict[str, dict[int, ReportSubtask]] = {}

    # Subtask verdicts (from verification).
    subtask_verdicts: dict[str, tuple[str, str | None]] = {}  # id -> (verdict, summary)

    # Failure metadata.
    item_failure_code: dict[str, str] = {}
    item_failed_subtask_idx: dict[str, int] = {}

    # Deferred / blocked / parked items.
    deferred_items: dict[str, tuple[str, str]] = {}  # item_id -> (text, eligible_at)
    blocked_items: dict[str, tuple[str, str]] = {}  # item_id -> (text, reason)
    parked_tracking: dict[str, tuple[str, str]] = {}  # item_id -> (text, reason)

    for entry in entries:
        event: str = entry.get("event", "")
        payload: dict[str, Any] = entry.get("payload") or {}
        item_id: str | None = entry.get("item_id") or payload.get("item_id") or None

        if event == "run_started":
            now = payload.get("now")
            tz = payload.get("tz")
            todo_path = payload.get("todo_path")
            mode = payload.get("mode")

        elif event == "run_finished":
            run_finished = True

        elif event == "item_selected" and item_id:
            selected_text[item_id] = str(payload.get("text", item_id))
            selected_overdue[item_id] = bool(payload.get("is_overdue", False))
            selected_due[item_id] = payload.get("due") or None
            item_status.setdefault(item_id, "in_progress")

        elif event == "item_deferred" and item_id:
            text = str(payload.get("text", item_id))
            eligible_at = str(payload.get("eligible_at", ""))
            deferred_items[item_id] = (text, eligible_at)
            item_status[item_id] = "deferred"

        elif event == "item_blocked" and item_id:
            text = str(payload.get("text", item_id))
            reason = str(payload.get("reason", ""))
            blocked_items[item_id] = (text, reason)
            item_status[item_id] = "blocked"

        elif event == "item_parked" and item_id:
            text = str(payload.get("text", item_id))
            reason = str(payload.get("reason", ""))
            parked_tracking[item_id] = (text, reason)
            item_status[item_id] = "parked"

        elif event == "gate_decision" and item_id:
            if payload.get("skipped_by_human") or payload.get("decision") == "skip":
                item_status[item_id] = "skipped_by_human"
                if item_id not in selected_text:
                    selected_text[item_id] = str(payload.get("text", item_id))

        elif event == "plan_created" and item_id:
            raw_subtasks: list[Any] = payload.get("subtasks") or []
            subtask_map: dict[int, ReportSubtask] = {}
            for raw_st in raw_subtasks:
                if not isinstance(raw_st, dict):
                    continue
                idx = int(raw_st.get("index", 0))
                check: dict[str, Any] = raw_st.get("check") or {}
                subtask_map[idx] = ReportSubtask(
                    index=idx,
                    subtask_id=str(raw_st.get("subtask_id", f"{item_id}#{idx}")),
                    description=str(raw_st.get("description", "")),
                    check_kind=str(check.get("kind", "")),
                    check_statement=str(check.get("statement", "")),
                )
            item_subtasks[item_id] = subtask_map

        elif event == "verification":
            subtask_id: str | None = entry.get("subtask_id") or payload.get("subtask_id") or None
            if subtask_id:
                verdict = str(payload.get("verdict", ""))
                summary: str | None = payload.get("summary") or None
                subtask_verdicts[subtask_id] = (verdict, summary)

        elif event == "item_completed" and item_id:
            item_status[item_id] = "done"

        elif event == "item_failed" and item_id:
            item_status[item_id] = "failed"
            if "failure_code" in payload:
                item_failure_code[item_id] = str(payload["failure_code"])
            fsi = payload.get("failed_subtask_index")
            if fsi is not None:
                item_failed_subtask_idx[item_id] = int(fsi)

    # Apply verdicts to subtask objects.
    for _iid, subtask_map in item_subtasks.items():
        for st in subtask_map.values():
            if st.subtask_id in subtask_verdicts:
                v, s = subtask_verdicts[st.subtask_id]
                st.verdict = v
                st.evidence_summary = s

    # Build ReportItem list.
    items: list[ReportItem] = []

    # Selected items (worked on, done, failed, skipped, or in_progress).
    for iid, text in selected_text.items():
        status = item_status.get(iid, "in_progress")
        subtask_map = item_subtasks.get(iid, {})
        subtasks_sorted = sorted(subtask_map.values(), key=lambda s: s.index)
        items.append(
            ReportItem(
                item_id=iid,
                text=text,
                status=status,
                is_overdue=selected_overdue.get(iid, False),
                due=selected_due.get(iid),
                subtasks=subtasks_sorted,
                failure_code=item_failure_code.get(iid),
                failed_subtask_index=item_failed_subtask_idx.get(iid),
            )
        )

    # Deferred items not already captured as selected.
    for iid, (text, eligible_at) in deferred_items.items():
        if iid not in selected_text:
            items.append(
                ReportItem(
                    item_id=iid,
                    text=text,
                    status="deferred",
                    eligible_at=eligible_at,
                )
            )

    # Blocked items not already captured.
    for iid, (text, reason) in blocked_items.items():
        if iid not in selected_text:
            items.append(
                ReportItem(
                    item_id=iid,
                    text=text,
                    status="blocked",
                    blocked_reason=reason,
                )
            )

    # Parked items (@paused= token) — listed but never affect exit status.
    for iid, (text, reason) in parked_tracking.items():
        if iid not in selected_text:
            items.append(
                ReportItem(
                    item_id=iid,
                    text=text,
                    status="parked",
                    blocked_reason=reason,
                )
            )

    # Earliest future eligibility across all deferred items (AC9.4).
    eligible_ats = [eligible_at for _, eligible_at in deferred_items.values() if eligible_at]
    next_eligible_at = min(eligible_ats) if eligible_ats else None

    interrupted = not run_finished and bool(entries)

    return RunReport(
        run_id=run_id,
        now=now,
        tz=tz,
        todo_path=todo_path,
        mode=mode,
        truncated=truncated,
        interrupted=interrupted,
        items=items,
        next_eligible_at=next_eligible_at,
    )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def format_summary(report: RunReport) -> str:
    """Return a short summary paragraph for stdout.

    Exit-status semantics are separate (see :func:`report_exit_status`).
    """
    done = [i for i in report.items if i.status == "done"]
    failed = [i for i in report.items if i.status == "failed"]
    deferred = [i for i in report.items if i.status == "deferred"]
    blocked = [i for i in report.items if i.status == "blocked"]
    parked = [i for i in report.items if i.status == "parked"]
    in_progress = [i for i in report.items if i.status == "in_progress"]
    overdue_done = [i for i in done if i.is_overdue]

    lines: list[str] = []

    if report.truncated or report.interrupted:
        lines.append("WARNING: Partial report — run was interrupted before completion.")

    parts: list[str] = []
    if done:
        parts.append(f"{len(done)} done")
    if failed:
        parts.append(f"{len(failed)} failed")
    if deferred:
        parts.append(f"{len(deferred)} deferred")
    if blocked:
        parts.append(f"{len(blocked)} blocked")
    if parked:
        parts.append(f"{len(parked)} parked")
    if in_progress:
        parts.append(f"{len(in_progress)} in progress (interrupted)")

    lines.append("Run: " + (", ".join(parts) if parts else "nothing to report"))

    if report.next_eligible_at:
        lines.append(f"Next eligible: {report.next_eligible_at}")

    # AC9.5 — overdue items listed even when completed.
    for item in overdue_done:
        lines.append(f"Overdue (completed): {item.text}")

    # Parked items — listed with reason, never affect exit status.
    for item in parked:
        reason = item.blocked_reason or "auto-failures"
        lines.append(f"Parked: {item.text}  [paused: {reason}]")

    # AC9.1 — failing subtask detail.
    for item in failed:
        lines.append(f"\nFailed: {item.text}")
        if item.failure_code:
            lines.append(f"  Reason: {item.failure_code}")
        fsi = item.failed_subtask_index
        if fsi is not None and item.subtasks:
            matching = [s for s in item.subtasks if s.index == fsi]
            if matching:
                st = matching[0]
                lines.append(f"  Subtask {fsi + 1}: {st.description}")
                lines.append(f"  Check ({st.check_kind}): {st.check_statement}")
                if st.evidence_summary:
                    lines.append(f"  Evidence: {st.evidence_summary}")

    return "\n".join(lines)


def format_markdown(report: RunReport) -> str:
    """Return the full Markdown report text."""
    lines: list[str] = []
    lines.append(f"# Run Report: {report.run_id}")
    lines.append("")

    # Partial-run banner — AC9.3.
    if report.truncated or report.interrupted:
        lines.append("> **WARNING: Partial report.** The run was interrupted before completion.")
        lines.append("")

    # Metadata block.
    if report.now:
        lines.append(f"- **Started:** {report.now}")
    if report.tz:
        lines.append(f"- **Timezone:** {report.tz}")
    if report.todo_path:
        lines.append(f"- **Todo file:** {report.todo_path}")
    if report.mode:
        lines.append(f"- **Mode:** {report.mode}")
    lines.append("")

    # Summary counts.
    done = [i for i in report.items if i.status == "done"]
    failed = [i for i in report.items if i.status == "failed"]
    in_progress = [i for i in report.items if i.status == "in_progress"]
    deferred = [i for i in report.items if i.status == "deferred"]
    blocked = [i for i in report.items if i.status == "blocked"]
    parked = [i for i in report.items if i.status == "parked"]
    skipped = [i for i in report.items if i.status == "skipped_by_human"]
    overdue_done = [i for i in done if i.is_overdue]

    lines.append("## Summary")
    lines.append("")
    lines.append("| Status | Count |")
    lines.append("|--------|-------|")
    lines.append(f"| Done | {len(done)} |")
    lines.append(f"| Failed | {len(failed)} |")
    if in_progress:
        lines.append(f"| In progress (interrupted) | {len(in_progress)} |")
    if deferred:
        lines.append(f"| Deferred | {len(deferred)} |")
    if blocked:
        lines.append(f"| Blocked | {len(blocked)} |")
    if parked:
        lines.append(f"| Parked | {len(parked)} |")
    if skipped:
        lines.append(f"| Skipped by human | {len(skipped)} |")
    if overdue_done:
        lines.append(f"| Overdue (completed) | {len(overdue_done)} |")
    lines.append("")

    # Deferred items + next eligibility — AC9.4.
    if deferred:
        lines.append("## Deferred Items")
        lines.append("")
        for item in deferred:
            lines.append(f"- **{item.text}**")
            if item.eligible_at:
                lines.append(f"  - Eligible at: `{item.eligible_at}`")
        lines.append("")
        if report.next_eligible_at:
            lines.append(f"**Next eligibility instant:** `{report.next_eligible_at}`")
            lines.append("")

    # Blocked items.
    if blocked:
        lines.append("## Blocked Items")
        lines.append("")
        for item in blocked:
            reason = item.blocked_reason or "unknown reason"
            lines.append(f"- **{item.text}**: {reason}")
        lines.append("")

    # Parked items — listed with reason, never affect exit status.
    if parked:
        lines.append("## Parked Items")
        lines.append("")
        for item in parked:
            reason = item.blocked_reason or "auto-failures"
            lines.append(f"- **{item.text}**: {reason}")
        lines.append("")

    # Failed items — AC9.1.
    if failed:
        lines.append("## Failed Items")
        lines.append("")
        for item in failed:
            lines.append(f"### {item.text}")
            lines.append(f"- Item ID: `{item.item_id}`")
            if item.failure_code:
                lines.append(f"- Failure: `{item.failure_code}`")
            fsi = item.failed_subtask_index
            if fsi is not None and item.subtasks:
                matching = [s for s in item.subtasks if s.index == fsi]
                if matching:
                    st = matching[0]
                    lines.append(f"- Failed at subtask {fsi + 1}: {st.description}")
                    lines.append(f"  - Check (`{st.check_kind}`): {st.check_statement}")
                    if st.evidence_summary:
                        lines.append(f"  - Evidence: {st.evidence_summary}")
            lines.append("")

    # Completed items — AC9.5: overdue marker even when done.
    if done:
        lines.append("## Completed Items")
        lines.append("")
        for item in done:
            overdue_marker = " *(overdue)*" if item.is_overdue else ""
            lines.append(f"- {item.text}{overdue_marker}")
        lines.append("")

    # In-progress (interrupted) items.
    if in_progress:
        lines.append("## Interrupted Items")
        lines.append("")
        for item in in_progress:
            lines.append(f"- **{item.text}** (in progress at interruption)")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def write_report(report: RunReport, run_dir: Path) -> Path:
    """Write ``report.md`` to *run_dir* and return its path."""
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / "report.md"
    report_path.write_text(format_markdown(report), encoding="utf-8")
    return report_path


def report_exit_status(report: RunReport) -> int:
    """Return 0 for a clean or deferred-only run, 1 if any item failed (AC9.2)."""
    for item in report.items:
        if item.status == "failed":
            return 1
    return 0
