# SPDX-License-Identifier: Apache-2.0
"""``gsd`` command-line entry point.

Subcommands land with their pipeline stages in the phase order set out in
``specs/04-technical-plan.md``; see ``IMPLEMENTATION_PLAN.md``.

Implemented so far
------------------
``gsd plan --dry-run``  — ingest, select, print result; no agent calls.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import __version__

SUBCOMMANDS = ("plan", "run", "resume", "report", "schedule", "doctor")


def build_parser() -> argparse.ArgumentParser:
    """Return the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="gsd",
        description="Turn a todo list into verified work.",
    )
    parser.add_argument("--version", action="version", version=f"gsd {__version__}")

    subs = parser.add_subparsers(dest="command")

    # plan
    plan_p = subs.add_parser("plan", help="Plan the next eligible item.")
    plan_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the selected item and exit without any agent calls.",
    )
    plan_p.add_argument("--todo", default=None, metavar="PATH", help="Override the todo file path.")

    # run — gate flags introduced in Phase 2 (gate-modes work item)
    run_p = subs.add_parser("run", help="Run the next eligible todo item.")
    run_p.add_argument("--todo", default=None, metavar="PATH", help="Override the todo file path.")
    _mode_group = run_p.add_mutually_exclusive_group()
    _mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan and exit without executing anything.",
    )
    _mode_group.add_argument(
        "--approve",
        action="store_true",
        help="Print the plan and wait for human approval before executing.",
    )
    run_p.add_argument(
        "--non-interactive",
        action="store_true",
        help="Disable all interactive prompts (required for scheduled / headless runs).",
    )

    # resume
    resume_p = subs.add_parser("resume", help="Resume an interrupted run.")
    resume_p.add_argument("run_id", help="The run ID to resume (e.g. from runs/<run-id>/).")
    resume_p.add_argument(
        "--runs-dir", default=None, metavar="DIR", help="Override the runs directory."
    )

    # report
    report_p = subs.add_parser("report", help="Show the report for a completed run.")
    report_p.add_argument("run_id", help="The run ID to report on.")
    report_p.add_argument(
        "--runs-dir", default=None, metavar="DIR", help="Override the runs directory."
    )

    # stubs — accepting the subcommand name is enough for smoke tests
    for cmd in ("schedule", "doctor"):
        subs.add_parser(cmd)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI. Returns the process exit status."""
    args = build_parser().parse_args(argv)
    if args.command is None:
        build_parser().print_help()
        return 0
    if args.command == "plan":
        return _cmd_plan(args)
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "report":
        return _cmd_report(args)
    if args.command == "resume":
        return _cmd_resume(args)
    print(f"gsd {args.command}: not implemented yet — see IMPLEMENTATION_PLAN.md")
    return 2


# ---------------------------------------------------------------------------
# plan subcommand
# ---------------------------------------------------------------------------


def _cmd_plan(args: argparse.Namespace) -> int:
    """Implement ``gsd plan [--dry-run]``."""
    if not args.dry_run:
        print("gsd plan: full pipeline not yet implemented — use --dry-run to preview")
        return 2

    from .clock import capture_now
    from .config import load_config
    from .ingest import IngestError, ingest
    from .journal import RUN_STARTED, Journal
    from .select import CycleError, select_next

    cli_overrides: dict[str, object] = {}
    if args.todo:
        cli_overrides["todo_path"] = args.todo
    config = load_config(cli_overrides=cli_overrides or None)

    now = capture_now(config)
    run_id = str(uuid.uuid4())

    journal_path = Path("runs") / run_id / "journal.jsonl"
    journal = Journal(journal_path, run_id)
    journal.append(
        RUN_STARTED,
        payload={
            "now": now.isoformat(),
            "tz": config.timezone,
            "mode": "dry-run",
            "todo_path": config.todo_path,
        },
    )

    todo_path = Path(config.todo_path)
    try:
        result = ingest(todo_path, config)
    except IngestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for w in result.warnings:
        print(f"warning: {w.message}", file=sys.stderr)

    try:
        sel = select_next(result.items, now)
    except CycleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    pending_count = sum(1 for it in result.items if it.status.value == "pending")
    print(f"Run:   {run_id}")
    print(f"Todo:  {todo_path}  ({pending_count} pending)")
    print(f"Now:   {now.isoformat()}")
    print()

    if sel.selected:
        item = sel.selected
        caps = ", ".join(sorted(str(c) for c in item.capabilities)) or "(none)"
        print(f"Selected:  {item.text}")
        print(f"  id:           {item.item_id}")
        print(f"  capabilities: {caps}")
        if item.schedule and item.schedule.due_literal:
            print(f"  due:          {item.schedule.due_literal}")
        if item.authored_subtasks:
            print(f"  subtasks ({len(item.authored_subtasks)}):")
            for i, st in enumerate(item.authored_subtasks, 1):
                print(f"    {i}. {st}")
    else:
        print("Nothing eligible to work on.")
        if sel.next_eligible_at:
            print(f"Next eligible: {sel.next_eligible_at}")

    if sel.deferred:
        print()
        print(f"Deferred ({len(sel.deferred)}):")
        for item, eligible_at in sel.deferred:
            print(f"  - {item.text!r}  [eligible at {eligible_at}]")

    if sel.blocked:
        print()
        print(f"Blocked ({len(sel.blocked)}):")
        for item, reason in sel.blocked:
            print(f"  - {item.text!r}  [{reason}]")

    return 0


# ---------------------------------------------------------------------------
# run subcommand
# ---------------------------------------------------------------------------


def _cmd_run(args: argparse.Namespace) -> int:
    """Implement ``gsd run [--dry-run | --approve] [--non-interactive]``."""
    from .gate import GateMode, GateStartupError, check_startup_flags

    if getattr(args, "dry_run", False):
        mode = GateMode.dry_run
    elif getattr(args, "approve", False):
        mode = GateMode.approve
    else:
        mode = GateMode.auto

    non_interactive: bool = getattr(args, "non_interactive", False)

    try:
        check_startup_flags(mode, non_interactive=non_interactive)
    except GateStartupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print("gsd run: full pipeline not yet implemented — see IMPLEMENTATION_PLAN.md")
    return 2


# ---------------------------------------------------------------------------
# report subcommand
# ---------------------------------------------------------------------------


def _cmd_report(args: argparse.Namespace) -> int:
    """Implement ``gsd report <run-id>``."""
    from .report import build_report, format_summary, report_exit_status, write_report

    runs_dir = Path(args.runs_dir) if getattr(args, "runs_dir", None) else Path("runs")
    run_dir = runs_dir / args.run_id

    if not (run_dir / "journal.jsonl").exists():
        print(f"error: no journal found at {run_dir / 'journal.jsonl'}", file=sys.stderr)
        return 1

    report = build_report(run_dir)
    report_path = write_report(report, run_dir)
    print(format_summary(report))
    print(f"\nReport written to: {report_path}")
    return report_exit_status(report)


# ---------------------------------------------------------------------------
# resume subcommand
# ---------------------------------------------------------------------------


def _rebuild_plan(
    entry: dict[str, Any],
    item_id: str,
) -> Any:
    """Reconstruct a Plan from a plan_created journal entry payload.

    Returns None if the entry is missing required fields.
    """
    from .models import (
        Capability,
        Check,
        CheckKind,
        HarnessInfo,
        Plan,
        Subtask,
        SubtaskStatus,
    )

    payload = entry.get("payload") or {}
    raw_subtasks = payload.get("subtasks")
    if not isinstance(raw_subtasks, list):
        return None

    harness_raw = payload.get("harness") or {}
    harness = HarnessInfo(
        agent=str(harness_raw.get("agent", "claude")),
        model=str(harness_raw.get("model", "sonnet")),
        harness=str(harness_raw.get("agent", "claude")),
        invoked_as=str(harness_raw.get("agent", "claude")),
    )

    subtasks: list[Subtask] = []
    for raw in raw_subtasks:
        if not isinstance(raw, dict):
            return None
        idx = int(raw.get("index", 0))
        description = str(raw.get("description", ""))
        check_raw = raw.get("check") or {}
        kind_str = str(check_raw.get("kind", ""))
        stmt = str(check_raw.get("statement", ""))

        try:
            kind = CheckKind(kind_str)
        except ValueError:
            return None

        command: tuple[str, ...] | None = None
        if kind is CheckKind.command:
            cmd_raw = check_raw.get("command")
            if isinstance(cmd_raw, list):
                command = tuple(str(a) for a in cmd_raw)

        path: str | None = check_raw.get("path") or None
        pattern: str | None = check_raw.get("pattern") or None
        rationale: str | None = check_raw.get("rationale") or None
        expect_status = int(check_raw.get("expect_status", 0))
        timeout_s = int(check_raw.get("timeout_s", 300))

        try:
            check = Check(
                kind=kind,
                statement=stmt,
                command=command,
                expect_status=expect_status,
                path=path,
                pattern=pattern,
                rationale=rationale,
                timeout_s=timeout_s,
            )
        except ValueError:
            return None

        import contextlib

        raw_caps = raw.get("capabilities") or []
        caps: frozenset[Capability] = frozenset()
        if isinstance(raw_caps, list):
            valid: set[Capability] = set()
            for c in raw_caps:
                with contextlib.suppress(ValueError):
                    valid.add(Capability(str(c)))
            caps = frozenset(valid)

        raw_deps = raw.get("depends_on") or []
        depends_on: tuple[int, ...] = tuple(int(d) for d in raw_deps if isinstance(d, (int, float)))

        subtasks.append(
            Subtask(
                subtask_id=str(raw.get("subtask_id", f"{item_id}#{idx}")),
                index=idx,
                description=description,
                check=check,
                capabilities=caps,
                depends_on=depends_on,
                status=SubtaskStatus.pending,
                attempts=(),
            )
        )

    if not subtasks:
        return None

    from .clock import stamp

    return Plan(
        item_id=item_id,
        subtasks=tuple(subtasks),
        source=str(payload.get("source", "model")),
        created_at=str(payload.get("created_at", stamp())),
        harness=harness,
    )


def _cmd_resume(
    args: argparse.Namespace,
    *,
    _run_agent: object | None = None,
) -> int:
    """Implement ``gsd resume <run-id>``.

    Replays the journal, restores run state (AC-S4 — reuses original ``now``),
    and continues from the first subtask without a ``passed`` verdict (AC-S1).
    Writes ``runs/<run-id>/report.md`` on completion.
    """
    from datetime import UTC, datetime

    from .config import load_config
    from .execute import execute
    from .ingest import IngestError, ingest
    from .journal import (
        ITEM_COMPLETED,
        ITEM_FAILED,
        ITEM_SELECTED,
        RUN_FINISHED,
        Journal,
    )
    from .models import Plan, Verdict, VerificationResult
    from .report import build_report, format_summary, report_exit_status, write_report
    from .select import CycleError, select_next
    from .verify import VerifyContext, run_verify

    runs_dir = Path(args.runs_dir) if getattr(args, "runs_dir", None) else Path("runs")
    run_dir = runs_dir / args.run_id
    journal_path = run_dir / "journal.jsonl"

    if not journal_path.exists():
        print(f"error: no journal found at {journal_path}", file=sys.stderr)
        return 1

    journal = Journal(journal_path, args.run_id)
    state = journal.replay()

    if state.now is None:
        print(f"error: journal {journal_path} has no run_started event", file=sys.stderr)
        return 1

    # AC-S4: use original now from journal, not current wall clock.
    original_now = datetime.fromisoformat(state.now)
    if original_now.tzinfo is None:
        original_now = original_now.replace(tzinfo=UTC)

    # Determine the todo path from the journal.
    todo_path_str: str | None = None
    for entry in state.entries:
        if entry.get("event") == "run_started":
            todo_path_str = (entry.get("payload") or {}).get("todo_path")
            break

    config = load_config()
    if todo_path_str:
        config = load_config(cli_overrides={"todo_path": todo_path_str})

    todo_path = Path(config.todo_path)

    # If the journal already has a completed/failed item (nothing in progress),
    # just build the report and exit.
    done_and_failed = state.items_done | state.items_failed

    # Re-ingest to get fresh item objects.
    try:
        ingest_result = ingest(todo_path, config)
    except IngestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # AC-S4: eligibility evaluated against original now.
    try:
        sel = select_next(ingest_result.items, original_now, done_ids=done_and_failed)
    except CycleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # No item selected — either everything done or only deferred.
    if sel.selected is None:
        report = build_report(run_dir)
        write_report(report, run_dir)
        print(format_summary(report))
        return report_exit_status(report)

    item = sel.selected

    # Journal that this item is being resumed (adds context to partial journals).
    journal.append(
        ITEM_SELECTED,
        item_id=item.item_id,
        payload={
            "text": item.text,
            "due": (item.schedule.due if item.schedule else None),
            "is_overdue": (
                (item.schedule.due is not None and item.schedule.due < state.now)
                if item.schedule and state.now
                else False
            ),
            "resumed": True,
        },
    )

    # Find existing plan in the journal.
    plan: Plan | None = None
    for entry in state.entries:
        if entry.get("event") == "plan_created" and entry.get("item_id") == item.item_id:
            plan = _rebuild_plan(entry, item.item_id)
            break

    if plan is None:
        # No plan in journal — need to decompose first.
        from .decompose import DecomposeError, decompose

        try:
            plan = decompose(
                item,
                config=config,
                journal=journal,
                cwd=todo_path.parent,
                _run_agent=_run_agent,
            )
        except DecomposeError as exc:
            journal.append(
                ITEM_FAILED,
                item_id=item.item_id,
                payload={"failure_code": exc.failure_code.value},
            )
            journal.append(RUN_FINISHED, payload={"exit_status": 1})
            report = build_report(run_dir)
            write_report(report, run_dir)
            print(format_summary(report))
            return 1

    # Execute and verify remaining subtasks — skip those already passed (AC-S1).
    prior_evidence: list[VerificationResult] = []
    cwd = todo_path.parent
    exit_status = 0

    for subtask in plan.subtasks:
        # AC-S1: skip subtasks that already have a passed verdict.
        existing_verdict = state.subtask_verdicts.get(subtask.subtask_id)
        if existing_verdict == Verdict.passed.value:
            continue

        execute(
            subtask,
            item=item,
            prior_evidence=prior_evidence,
            config=config,
            journal=journal,
            cwd=cwd,
            run_dir=run_dir,
            attempt_no=0,
            _run_agent=_run_agent,
        )

        ctx = VerifyContext(
            cwd=cwd,
            run_dir=run_dir,
            evidence_head_bytes=config.evidence_head_bytes,
            subtask_id=subtask.subtask_id,
            attempt_no=0,
        )
        result = run_verify(subtask.check, journal=journal, item_id=item.item_id, ctx=ctx)

        if result.verdict == Verdict.passed:
            prior_evidence.append(result)
        else:
            journal.append(
                ITEM_FAILED,
                item_id=item.item_id,
                payload={
                    "failure_code": "check_failed",
                    "failed_subtask_index": subtask.index,
                },
            )
            exit_status = 1
            break
    else:
        # All subtasks passed.
        journal.append(ITEM_COMPLETED, item_id=item.item_id, payload={})

    journal.append(RUN_FINISHED, payload={"exit_status": exit_status})
    report = build_report(run_dir)
    write_report(report, run_dir)
    print(format_summary(report))
    return exit_status


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
