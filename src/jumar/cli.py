# SPDX-License-Identifier: Apache-2.0
"""``jumar`` command-line entry point.

Subcommands land with their pipeline stages in the phase order set out in
``specs/04-technical-plan.md``; see ``IMPLEMENTATION_PLAN.md``.

Implemented so far
------------------
``jumar plan --dry-run``  — ingest, select, print result; no agent calls.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import __version__
from .models import HarnessInfo

SUBCOMMANDS = ("plan", "run", "resume", "report", "schedule", "doctor", "status")


# ---------------------------------------------------------------------------
# Run-id resolution — prefix matching, "latest", and CWD-aware errors
# ---------------------------------------------------------------------------


class _RunResolveError(Exception):
    """Raised when a run-id cannot be resolved to a unique run directory."""


def _resolve_run_id(runs_dir: Path, run_id: str) -> tuple[str, Path]:
    """Resolve run_id (exact match, unambiguous prefix, or ``'latest'``) to
    ``(resolved_run_id, run_dir)``.

    ``runs_dir`` may be relative; error messages convert it to absolute and
    mention the current working directory so the user knows why a bare id
    resolves differently depending on which directory they are in.

    Raises :exc:`_RunResolveError` when:
    - the directory does not exist or contains no runs;
    - the prefix matches more than one run (ambiguous);
    - no run matches the given id or prefix.
    """
    import json as _json

    _cwd_hint = (
        f" (path resolved relative to {Path.cwd()}; "
        "use --runs-dir to specify the correct runs directory)"
    )

    if not runs_dir.exists():
        raise _RunResolveError(f"no runs found — {runs_dir.absolute()} does not exist{_cwd_hint}")

    # Collect subdirectories that have a journal file.
    all_run_dirs: list[Path] = sorted(
        d for d in runs_dir.iterdir() if d.is_dir() and (d / "journal.jsonl").exists()
    )

    if run_id == "latest":
        if not all_run_dirs:
            raise _RunResolveError(f"no runs found in {runs_dir.absolute()}{_cwd_hint}")

        # Fast path: try the run index before scanning every journal.
        from .index import resolve_latest_from_index as _resolve_idx

        _idx_id = _resolve_idx(runs_dir)
        if _idx_id is not None:
            _idx_dir = runs_dir / _idx_id
            if _idx_dir.is_dir() and (_idx_dir / "journal.jsonl").exists():
                return _idx_id, _idx_dir

        # Fall back to full journal scan (no index, corrupt index, or stale entry).
        best_dir: Path | None = None
        best_ts = ""
        for d in all_run_dirs:
            try:
                for line in (d / "journal.jsonl").read_text("utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = _json.loads(line)
                    except _json.JSONDecodeError:
                        continue
                    if entry.get("event") == "run_started":
                        ts: str = (entry.get("payload") or {}).get("now", "")
                        if ts > best_ts:
                            best_ts = ts
                            best_dir = d
                        break
            except OSError:
                continue
        if best_dir is None:
            raise _RunResolveError(
                f"no run with a run_started event found in {runs_dir.absolute()}{_cwd_hint}"
            )
        return best_dir.name, best_dir

    # Exact match takes priority over prefix search.
    exact = runs_dir / run_id
    if exact.is_dir() and (exact / "journal.jsonl").exists():
        return run_id, exact

    # Prefix match.
    matches = [d for d in all_run_dirs if d.name.startswith(run_id)]
    if len(matches) == 1:
        return matches[0].name, matches[0]
    if len(matches) > 1:
        names = ", ".join(d.name for d in sorted(matches, key=lambda p: p.name))
        raise _RunResolveError(f"ambiguous run-id prefix {run_id!r}: matches {names}")

    # Nothing found — surface the relative-path hint so the user knows why.
    raise _RunResolveError(
        f"no journal found for run-id {run_id!r} in {runs_dir.absolute()}{_cwd_hint}"
    )


def build_parser() -> argparse.ArgumentParser:
    """Return the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="jumar",
        description="Turn a todo list into verified work.",
    )
    parser.add_argument("--version", action="version", version=f"jumar {__version__}")

    subs = parser.add_subparsers(dest="command")

    # plan
    plan_p = subs.add_parser("plan", help="Plan the next eligible item.")
    plan_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the selected item and exit without any agent calls.",
    )
    plan_p.add_argument("--todo", default=None, metavar="PATH", help="Override the todo file path.")
    plan_p.add_argument(
        "--json",
        action="store_true",
        help="Emit the plan result as a JSON document on stdout (warnings still go to stderr).",
    )

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
    run_p.add_argument(
        "--verbose",
        action="store_true",
        help="Echo the agent's captured output after each attempt (progress to stderr).",
    )
    run_p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable output on stdout; suppresses progress on stderr.",
    )

    # resume
    resume_p = subs.add_parser("resume", help="Resume an interrupted run.")
    resume_p.add_argument("run_id", help="The run ID to resume (e.g. from runs/<run-id>/).")
    resume_p.add_argument(
        "--runs-dir", default=None, metavar="DIR", help="Override the runs directory."
    )
    resume_p.add_argument(
        "--retry-failed",
        action="store_true",
        help=(
            "Re-run an item the journal records as failed, continuing from the "
            "first subtask without a passing verdict. Passed subtasks are "
            "replayed, never re-executed."
        ),
    )

    # report
    report_p = subs.add_parser("report", help="Show the report for a completed run.")
    report_p.add_argument("run_id", help="The run ID to report on.")
    report_p.add_argument(
        "--runs-dir", default=None, metavar="DIR", help="Override the runs directory."
    )
    report_p.add_argument(
        "--json",
        action="store_true",
        help="Emit the report as a JSON document on stdout (warnings still go to stderr).",
    )

    # schedule
    schedule_p = subs.add_parser("schedule", help="Manage scheduled jumar runs.")
    schedule_subs = schedule_p.add_subparsers(dest="schedule_command")

    _schedule_add_p = schedule_subs.add_parser("add", help="Install a schedule.")
    _schedule_add_p.add_argument("cron_expr", help="5-field cron expression, e.g. '0 9 * * 1-5'.")
    _schedule_add_p.add_argument(
        "--todo", required=True, metavar="PATH", help="Absolute path to the todo file."
    )
    _schedule_add_p.add_argument(
        "--config", default=None, metavar="PATH", help="Absolute path to jumar.toml."
    )
    _schedule_add_p.add_argument(
        "--dry-run", action="store_true", help="Print the entry without installing."
    )
    _schedule_add_p.add_argument(
        "--backend", default=None, metavar="NAME", help="Force 'cron' or 'launchd'."
    )

    schedule_subs.add_parser("list", help="List jumar-owned schedule entries.")

    _schedule_remove_p = schedule_subs.add_parser("remove", help="Remove a schedule entry.")
    _schedule_remove_p.add_argument("schedule_id", help="The schedule ID to remove.")
    _schedule_remove_p.add_argument(
        "--backend", default=None, metavar="NAME", help="Force 'cron' or 'launchd'."
    )

    _schedule_show_p = schedule_subs.add_parser(
        "show", help="Preview what 'add' would install (never writes)."
    )
    _schedule_show_p.add_argument("cron_expr", help="5-field cron expression.")
    _schedule_show_p.add_argument(
        "--todo", required=True, metavar="PATH", help="Absolute path to the todo file."
    )
    _schedule_show_p.add_argument(
        "--config", default=None, metavar="PATH", help="Absolute path to jumar.toml."
    )

    # doctor
    doctor_p = subs.add_parser("doctor", help="Check config, harness, and todo file.")
    doctor_p.add_argument(
        "--todo", default=None, metavar="PATH", help="Override the todo file path."
    )

    # status
    status_p = subs.add_parser("status", help="Show item-centric status across all runs.")
    status_p.add_argument(
        "--todo", default=None, metavar="PATH", help="Override the todo file path."
    )
    status_p.add_argument(
        "--runs-dir", default=None, metavar="DIR", help="Override the runs directory."
    )
    status_p.add_argument(
        "--json",
        action="store_true",
        help="Emit the status as a JSON document on stdout (warnings still go to stderr).",
    )

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
    if args.command == "schedule":
        return _cmd_schedule(args)
    if args.command == "doctor":
        return _cmd_doctor(args)
    if args.command == "status":
        return _cmd_status(args)
    print(f"jumar {args.command}: not implemented yet — see IMPLEMENTATION_PLAN.md")
    return 2


# ---------------------------------------------------------------------------
# plan subcommand
# ---------------------------------------------------------------------------


def _cmd_plan(args: argparse.Namespace) -> int:
    """Implement ``jumar plan [--dry-run] [--json]``."""
    if not args.dry_run:
        print("jumar plan: full pipeline not yet implemented — use --dry-run to preview")
        return 2

    from .clock import capture_now, make_run_id
    from .config import load_config
    from .ingest import IngestError, ingest
    from .journal import RUN_STARTED, Journal
    from .report import (
        PlanBlockedItem,
        PlanDeferredItem,
        PlanResult,
        PlanSelectedItem,
        format_plan_json,
        format_plan_text,
    )
    from .select import CycleError, select_next

    cli_overrides: dict[str, object] = {}
    if args.todo:
        cli_overrides["todo_path"] = args.todo
    config = load_config(cli_overrides=cli_overrides or None)

    now = capture_now(config)
    run_id = make_run_id(now)

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
        ingest_result = ingest(todo_path, config)
    except IngestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for w in ingest_result.warnings:
        print(f"warning: {w.message}", file=sys.stderr)

    try:
        sel = select_next(ingest_result.items, now)
    except CycleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    pending_count = sum(1 for it in ingest_result.items if it.status.value == "pending")

    # Build the shared result object — both human and JSON renderers use this.
    selected: PlanSelectedItem | None = None
    if sel.selected:
        item = sel.selected
        selected = PlanSelectedItem(
            item_id=item.item_id,
            text=item.text,
            capabilities=sorted(str(c) for c in item.capabilities),
            due=item.schedule.due_literal if item.schedule else None,
            authored_subtasks=list(item.authored_subtasks),
        )

    plan_result = PlanResult(
        run_id=run_id,
        now=now.isoformat(),
        todo_path=str(todo_path),
        pending_count=pending_count,
        selected=selected,
        deferred=[
            PlanDeferredItem(item_id=it.item_id, text=it.text, eligible_at=ea)
            for it, ea in sel.deferred
        ],
        blocked=[
            PlanBlockedItem(item_id=it.item_id, text=it.text, reason=r) for it, r in sel.blocked
        ],
        next_eligible_at=sel.next_eligible_at,
    )

    use_json: bool = getattr(args, "json", False)
    if use_json:
        print(format_plan_json(plan_result))
    else:
        print(format_plan_text(plan_result))

    return 0


# ---------------------------------------------------------------------------
# Shared item runner — the single orchestration used by both `run` and `resume`
# ---------------------------------------------------------------------------


def _verifications_from_journal(
    state: Any,
    plan: Any,
) -> list[Any]:
    """Rebuild VerificationResults for subtasks that already passed.

    ``complete()`` requires a passing verdict for every subtask (AC8.1).  On a
    resumed run the earlier verdicts live only in the journal, so they are
    reconstructed here — otherwise a resume that finds every subtask already
    passed would hand ``complete()`` an empty list and never mark the item done.
    """
    from .models import CheckKind, Verdict, VerificationResult

    rebuilt: list[VerificationResult] = []
    for subtask in plan.subtasks:
        verdict = state.subtask_verdicts.get(subtask.subtask_id)
        if verdict != Verdict.passed.value:
            continue
        rebuilt.append(
            VerificationResult(
                subtask_id=subtask.subtask_id,
                attempt_no=0,
                verdict=Verdict.passed,
                kind=CheckKind(subtask.check.kind),
                evidence={"replayed_from_journal": True},
                summary="passed in an earlier run (replayed from journal)",
                evidence_path=None,
                checked_at=state.now or "",
            )
        )
    return rebuilt


def _item_max_subtasks(item: Any, config: Any) -> int:
    """The plan-length cap for this item: @max-subtasks= overrides config."""
    raw = getattr(item, "meta", {}).get("max-subtasks")
    if raw:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    return int(getattr(config, "max_subtasks", 12))


def run_item(
    item: Any,
    *,
    config: Any,
    journal: Any,
    todo_path: Path,
    run_dir: Path,
    now: Any,
    mode: str = "auto",
    plan: Any | None = None,
    already_passed: frozenset[str] = frozenset(),
    prior_verifications: list[Any] | None = None,
    resumed: bool = False,
    stdin: Any | None = None,
    stdout: Any | None = None,
    progress: Any | None = None,
    _run_agent: Any | None = None,
) -> int:
    """Decompose, gate, then execute→verify→repair each subtask, then complete.

    This is the whole outer loop for ONE item, shared verbatim by ``jumar run``
    and ``jumar resume`` so the two commands cannot drift apart.

    Returns the exit-status contribution: 0 when the item completed, was
    dry-run, or was skipped by the human; 1 when it failed or was inconclusive.
    """
    from .backoff import advance_failure_count, clear_failure_count
    from .complete import complete
    from .decompose import DecomposeError, decompose
    from .execute import execute
    from .gate import GateDecision, GateError, GateMode, gate
    from .journal import ITEM_COMPLETED, ITEM_FAILED, ITEM_SELECTED
    from .models import Verdict
    from .progress import Progress
    from .repair import RepairExhausted, repair
    from .verify import VerifyContext, run_verify

    prog = progress if progress is not None else Progress(enabled=False)

    cwd = todo_path.parent
    non_interactive = mode != GateMode.approve

    journal.append(
        ITEM_SELECTED,
        item_id=item.item_id,
        payload={
            "text": item.text,
            "due": (item.schedule.due if item.schedule else None),
            "is_overdue": bool(
                item.schedule
                and item.schedule.due is not None
                and item.schedule.due < now.isoformat()
            ),
            "resumed": resumed,
        },
    )

    # --- decompose (skipped when resuming with a plan already in the journal)
    if plan is None:
        prog.planning(item.text, _item_max_subtasks(item, config))
        try:
            plan = decompose(
                item,
                config=config,
                journal=journal,
                cwd=cwd,
                _run_agent=_run_agent,
                on_rejected=lambda reason, attempt, retrying: prog.plan_rejected(
                    reason, attempt, 2, retrying
                ),
            )
        except DecomposeError as exc:
            journal.append(
                ITEM_FAILED,
                item_id=item.item_id,
                payload={"failure_code": exc.failure_code.value},
            )
            advance_failure_count(todo_path, item, config, journal)
            print(f"error: {exc}", file=sys.stderr)
            return 1

    # --- gate (capability check runs in every mode, AC4.3)
    try:
        decision = gate(
            plan,
            item,
            mode=mode,
            journal=journal,
            stdin=stdin,
            stdout=stdout,
        )
    except GateError as exc:
        journal.append(
            ITEM_FAILED,
            item_id=item.item_id,
            payload={"failure_code": exc.failure_code.value},
        )
        advance_failure_count(todo_path, item, config, journal)
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if decision == GateDecision.dry_run:
        return 0
    if decision == GateDecision.skip:
        return 0

    # --- inner loop: one subtask at a time, verified before the next (AC5.1)
    verifications: list[Any] = list(prior_verifications or [])
    prior_evidence: list[Any] = list(verifications)
    total = len(plan.subtasks)
    prog.item_started(item.text, total)

    for subtask in plan.subtasks:
        if subtask.subtask_id in already_passed:
            prog.subtask_skipped(subtask.index, total, subtask.description)
            continue  # AC-S1: never redo verified work

        prog.subtask_started(subtask.index, total, subtask.description)
        # One execution session per item: the first subtask CREATES it
        # (--session-id <session_id>) and every later subtask RESUMES it
        # (--resume <session_id>), so the agent keeps the context of prior
        # work.  Repairs always resume — by then the session exists, and a
        # second --session-id with the same UUID is an error.
        exec_session_id = plan.session_id
        attempt = execute(
            subtask,
            item=item,
            prior_evidence=prior_evidence,
            config=config,
            journal=journal,
            cwd=cwd,
            run_dir=run_dir,
            attempt_no=0,
            session_id=exec_session_id,
            session_is_new=subtask.index == 0,
            _run_agent=_run_agent,
        )

        prog.agent_transcript(getattr(attempt, "transcript_path", None))
        prog.verifying(subtask.check.kind)

        # The judge verifier needs a harness or it returns
        # `no_harness_configured` — inconclusive — for every judge check, which
        # then burns the repair budget and fails the item. Build it from config
        # and pass it, here and in repair.py. Resolved per-stage: an operator
        # who sets [harness.judge] (e.g. to keep judge on a frontier model
        # while execute uses a cheaper or local one) must actually get it here,
        # the one live call site that dispatches a judge.
        judge_resolved = config.harness.for_stage("judge")
        judge_harness = HarnessInfo(
            agent=judge_resolved.agent,
            model=judge_resolved.model,
            harness=judge_resolved.agent,
            invoked_as=judge_resolved.agent,
            base_url=judge_resolved.base_url,
            api_key_env=judge_resolved.api_key_env,
            commands_allow=config.commands.allow,
            commands_deny=config.commands.deny,
        )
        ctx = VerifyContext(
            cwd=cwd,
            run_dir=run_dir,
            evidence_head_bytes=config.evidence_head_bytes,
            subtask_id=subtask.subtask_id,
            attempt_no=0,
            non_interactive=non_interactive,
            config=config,
            harness=judge_harness,
            judge_timeout_s=getattr(config, "subtask_timeout_s", 300),
            # Honour the run's injected runner. Without this the judge always
            # calls the default harness — which in a test means spawning the
            # real agent binary, and in production means the judge silently
            # ignores whatever runner the caller configured.
            judge_run_agent=_run_agent,
        )
        result = run_verify(subtask.check, journal=journal, item_id=item.item_id, ctx=ctx)

        # --- bounded repair on a non-pass (AC7.1–AC7.4)
        repaired = result.verdict != Verdict.passed
        if result.verdict != Verdict.passed:
            # Say that it failed, and why, BEFORE the repair loop opens — not
            # after the budget is spent.
            prog.check_failed(result.verdict, getattr(result, "summary", None))
            prog.repairing(getattr(config, "max_repairs", 0))
            try:
                result = repair(
                    subtask,
                    first_result=result,
                    item=item,
                    prior_evidence=prior_evidence,
                    config=config,
                    journal=journal,
                    cwd=cwd,
                    run_dir=run_dir,
                    session_id=exec_session_id,
                    _run_agent=_run_agent,
                    judge_harness=judge_harness,
                    judge_run_agent=_run_agent,
                    on_attempt=prog.repair_attempt,
                    on_result=lambda r: prog.repair_result(r.verdict, getattr(r, "summary", None)),
                )
            except RepairExhausted as exc:
                prog.verdict(
                    "repairs exhausted",
                    getattr(exc.last_result, "summary", None),
                )
                journal.append(
                    ITEM_FAILED,
                    item_id=item.item_id,
                    payload={
                        "failure_code": "repairs_exhausted",
                        "failed_subtask_index": subtask.index,
                    },
                )
                advance_failure_count(todo_path, item, config, journal)
                return 1

        if result.verdict != Verdict.passed:
            prog.verdict(result.verdict, getattr(result, "summary", None))
            journal.append(
                ITEM_FAILED,
                item_id=item.item_id,
                payload={
                    "failure_code": "check_failed",
                    "failed_subtask_index": subtask.index,
                },
            )
            advance_failure_count(todo_path, item, config, journal)
            return 1

        # A repaired subtask already printed its per-attempt verdict; don't
        # print "passed" twice.
        if not repaired:
            prog.verdict(result.verdict, getattr(result, "summary", None))
        verifications.append(result)
        prior_evidence.append(result)

    # --- every subtask passed: flip the checkbox / advance recurrence (AC8.x)
    completed = complete(
        item,
        plan,
        todo_path=todo_path,
        verifications=tuple(verifications),
        config=config,
        journal=journal,
        cwd=cwd,
        now=now,
    )
    if not completed:
        journal.append(
            ITEM_FAILED,
            item_id=item.item_id,
            payload={"failure_code": "check_failed"},
        )
        advance_failure_count(todo_path, item, config, journal)
        return 1

    clear_failure_count(todo_path, item, journal)
    journal.append(ITEM_COMPLETED, item_id=item.item_id, payload={})
    return 0


# ---------------------------------------------------------------------------
# run subcommand
# ---------------------------------------------------------------------------


def _cmd_run(
    args: argparse.Namespace,
    *,
    _run_agent: object | None = None,
    _progress_force: bool | None = None,
) -> int:
    """Implement ``jumar run [--dry-run | --approve] [--non-interactive]``.

    Acquires the single-flight lock, ingests, selects one eligible item, and
    hands it to :func:`run_item` — the same orchestration ``jumar resume`` uses.
    """
    from .gate import GateMode, GateStartupError, check_harness_policy, check_startup_flags

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

    from .config import load_config
    from .lock import AlreadyRunning, Lock

    cli_overrides: dict[str, object] = {}
    if getattr(args, "todo", None):
        cli_overrides["todo_path"] = args.todo
    config = load_config(cli_overrides=cli_overrides or None)

    try:
        check_harness_policy(
            config.harness.agent,
            allow_unrestricted_harness=config.allow_unrestricted_harness,
        )
    except GateStartupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    todo_path = Path(config.todo_path)

    # Capture now before the lock so make_run_id has a real timestamp.
    from .clock import capture_now, make_run_id

    now = capture_now(config)

    # Acquire single-flight lock before ingest (AC10.5, AC10.6).
    run_id = make_run_id(now)
    lock = Lock(todo_path)
    try:
        lock.acquire(run_id=run_id)
    except AlreadyRunning as exc:
        print(f"already_running (pid={exc.pid})", file=sys.stderr)
        return 0

    try:
        from .ingest import IngestError, ingest
        from .journal import ITEM_PARKED, LOCK_RECLAIMED, RUN_FINISHED, RUN_STARTED, Journal
        from .report import build_report, format_summary, write_report
        from .select import CycleError, select_next

        run_dir = Path("runs") / run_id
        journal = Journal(run_dir / "journal.jsonl", run_id)

        journal.append(
            RUN_STARTED,
            payload={
                "now": now.isoformat(),
                "tz": config.timezone,
                "mode": str(mode),
                "todo_path": str(todo_path),
                "interactive": not non_interactive,
                "trigger": "human",
            },
        )
        from .index import (
            STATUS_COMPLETED,
            STATUS_FAILED,
            append_run_started,
            update_run_finished,
        )

        append_run_started(run_dir.parent, run_id, now.isoformat())
        if lock.reclaimed_info is not None:
            journal.append(LOCK_RECLAIMED, payload=lock.reclaimed_info)

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

        # Journal parked items so the report can list them.
        for p_item, p_reason in sel.parked:
            journal.append(
                ITEM_PARKED,
                item_id=p_item.item_id,
                payload={"text": p_item.text, "reason": p_reason},
            )

        if sel.selected is None:
            # AC2.10 / AC9.4: nothing eligible is a success, not a failure.
            print("Nothing eligible to work on.")
            if sel.next_eligible_at:
                print(f"Next eligible: {sel.next_eligible_at}")
            if sel.parked:
                print(f"Parked ({len(sel.parked)}):")
                for p_item, p_reason in sel.parked:
                    print(f"  - {p_item.text!r}  [paused: {p_reason}]")
            journal.append(RUN_FINISHED, payload={"exit_status": 0})
            update_run_finished(run_dir.parent, run_id, "", STATUS_COMPLETED)
            return 0

        from .progress import Progress

        prog = Progress.for_run(
            json_output=bool(getattr(args, "json", False)),
            verbose=bool(getattr(args, "verbose", False)),
            force=_progress_force,
        )

        status = run_item(
            sel.selected,
            config=config,
            journal=journal,
            todo_path=todo_path,
            run_dir=run_dir,
            now=now,
            mode=mode,
            progress=prog,
            _run_agent=_run_agent,
        )

        prog.item_finished("done" if status == 0 else "failed")
        journal.append(RUN_FINISHED, payload={"exit_status": status})
        update_run_finished(
            run_dir.parent,
            run_id,
            sel.selected.item_id,
            STATUS_COMPLETED if status == 0 else STATUS_FAILED,
        )
        report = build_report(run_dir)
        write_report(report, run_dir)
        print(format_summary(report))
        return status
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# report subcommand
# ---------------------------------------------------------------------------


def _cmd_report(args: argparse.Namespace) -> int:
    """Implement ``jumar report <run-id> [--json]``."""
    from .report import (
        build_report,
        format_report_json,
        format_summary,
        report_exit_status,
        write_report,
    )

    runs_dir = Path(args.runs_dir) if getattr(args, "runs_dir", None) else Path("runs")
    try:
        _, run_dir = _resolve_run_id(runs_dir, args.run_id)
    except _RunResolveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    report = build_report(run_dir)
    exit_code = report_exit_status(report)

    use_json: bool = getattr(args, "json", False)
    if use_json:
        print(format_report_json(report))
    else:
        report_path = write_report(report, run_dir)
        print(format_summary(report))
        print(f"\nReport written to: {report_path}")

    return exit_code


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

    raw_session_id = payload.get("session_id")
    session_id: str | None = str(raw_session_id) if isinstance(raw_session_id, str) else None

    return Plan(
        item_id=item_id,
        subtasks=tuple(subtasks),
        source=str(payload.get("source", "model")),
        created_at=str(payload.get("created_at", stamp())),
        harness=harness,
        session_id=session_id,
    )


def _cmd_resume(
    args: argparse.Namespace,
    *,
    _run_agent: object | None = None,
) -> int:
    """Implement ``jumar resume <run-id>``.

    Replays the journal, restores run state (AC-S4 — reuses original ``now``),
    and continues from the first subtask without a ``passed`` verdict (AC-S1).
    Writes ``runs/<run-id>/report.md`` on completion.
    """
    from datetime import UTC, datetime

    from .config import load_config
    from .ingest import IngestError, ingest
    from .journal import RUN_FINISHED, Journal
    from .models import Plan, Verdict
    from .report import build_report, format_summary, report_exit_status, write_report
    from .select import CycleError, select_next

    runs_dir = Path(args.runs_dir) if getattr(args, "runs_dir", None) else Path("runs")
    try:
        resolved_run_id, run_dir = _resolve_run_id(runs_dir, args.run_id)
    except _RunResolveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    journal_path = run_dir / "journal.jsonl"

    journal = Journal(journal_path, resolved_run_id)
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

    # Resume continues THIS run's item — it does not start a new one.  The item
    # is whichever the journal recorded as selected; only when the run died
    # before journalling a selection does resume fall back to choosing one, and
    # then against the original now (AC-S4).
    resumed_item_id: str | None = None
    for entry in state.entries:
        if entry.get("event") == "item_selected":
            resumed_item_id = entry.get("item_id")
            break

    # `--retry-failed` reopens a terminally FAILED item. A *completed* item is
    # never reopened: it is done, and re-running it would undo the one guarantee
    # the journal provides. The default stays report-and-exit so that resuming
    # twice is idempotent.
    retry_failed = bool(getattr(args, "retry_failed", False))
    # Exclude items that subsequently completed: once in items_done, always done.
    reopenable = (state.items_failed - state.items_done) if retry_failed else frozenset()

    item = None
    if resumed_item_id is not None:
        if resumed_item_id in done_and_failed and resumed_item_id not in reopenable:
            # That item already reached a terminal state — report, do not
            # pick up unrelated work under the guise of resuming.
            report = build_report(run_dir)
            write_report(report, run_dir)
            print(format_summary(report))
            return report_exit_status(report)
        item = next(
            (i for i in ingest_result.items if i.item_id == resumed_item_id),
            None,
        )

    if item is None:
        # AC-S4: eligibility evaluated against the original now.
        try:
            sel = select_next(ingest_result.items, original_now, done_ids=done_and_failed)
        except CycleError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        item = sel.selected

    # Nothing to resume — everything done, deferred, or the item is gone.
    if item is None:
        report = build_report(run_dir)
        write_report(report, run_dir)
        print(format_summary(report))
        return report_exit_status(report)

    # Find an existing plan in the journal; run_item decomposes if there is none.
    plan: Plan | None = None
    for entry in state.entries:
        if entry.get("event") == "plan_created" and entry.get("item_id") == item.item_id:
            plan = _rebuild_plan(entry, item.item_id)
            break

    # AC-S1: subtasks with a passed verdict are never re-executed; their
    # verdicts are replayed so complete() sees a full passing set (AC8.1).
    already_passed = frozenset(
        sid for sid, v in state.subtask_verdicts.items() if v == Verdict.passed.value
    )
    prior = _verifications_from_journal(state, plan) if plan is not None else []

    if retry_failed and resumed_item_id in state.items_failed:
        journal.append(
            "retry_started",
            item_id=item.item_id,
            payload={
                "reason": "operator retried a failed item",
                "resuming_after": sorted(already_passed),
            },
        )

    exit_status = run_item(
        item,
        config=config,
        journal=journal,
        todo_path=todo_path,
        run_dir=run_dir,
        now=original_now,
        plan=plan,
        already_passed=already_passed,
        prior_verifications=prior,
        resumed=True,
        _run_agent=_run_agent,
    )

    journal.append(RUN_FINISHED, payload={"exit_status": exit_status})
    from .index import STATUS_COMPLETED, STATUS_FAILED
    from .index import update_run_finished as _update_idx

    _update_idx(
        run_dir.parent,
        resolved_run_id,
        item.item_id,
        STATUS_COMPLETED if exit_status == 0 else STATUS_FAILED,
    )
    report = build_report(run_dir)
    write_report(report, run_dir)
    print(format_summary(report))
    return exit_status


# ---------------------------------------------------------------------------
# schedule subcommand
# ---------------------------------------------------------------------------


def _cmd_schedule(args: argparse.Namespace) -> int:
    """Implement ``jumar schedule <add|list|remove|show>``."""
    from .config import load_config
    from .schedule import (
        CronExprError,
        add_schedule,
        default_backend,
        list_schedules,
        remove_schedule,
    )

    sub = getattr(args, "schedule_command", None)
    if sub is None:
        build_parser().parse_args(["schedule", "--help"])
        return 0

    config = load_config()
    backend_override: str | None = getattr(args, "backend", None) or config.schedule_backend

    if sub == "add":
        try:
            add_schedule(
                args.cron_expr,
                todo_path=args.todo,
                config_path=getattr(args, "config", None),
                timezone=config.timezone,
                backend=default_backend(backend_override),
                dry_run=getattr(args, "dry_run", False),
            )
        except CronExprError as exc:
            print(f"error: invalid cron expression ({exc.field}): {exc}", file=sys.stderr)
            return 1
        return 0

    if sub == "show":
        try:
            add_schedule(
                args.cron_expr,
                todo_path=args.todo,
                config_path=getattr(args, "config", None),
                timezone=config.timezone,
                backend=default_backend(backend_override),
                dry_run=True,
            )
        except CronExprError as exc:
            print(f"error: invalid cron expression ({exc.field}): {exc}", file=sys.stderr)
            return 1
        return 0

    if sub == "list":
        entries = list_schedules(default_backend(backend_override))
        if not entries:
            print("No jumar-owned schedule entries found.")
            return 0
        for e in entries:
            print(
                f"  id={e.schedule_id}  cron={e.cron_expr!r}  tz={e.timezone}  todo={e.todo_path}"
            )
        return 0

    if sub == "remove":
        removed = remove_schedule(args.schedule_id, default_backend(backend_override))
        if not removed:
            print(f"error: schedule {args.schedule_id!r} not found", file=sys.stderr)
            return 1
        print(f"Removed schedule {args.schedule_id!r}.")
        return 0

    print(f"jumar schedule {sub}: unknown sub-command", file=sys.stderr)
    return 2


# ---------------------------------------------------------------------------
# doctor subcommand
# ---------------------------------------------------------------------------


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Implement ``jumar doctor``."""
    from .config import load_config
    from .doctor import format_report, run_doctor

    cli_overrides: dict[str, object] = {}
    if getattr(args, "todo", None):
        cli_overrides["todo_path"] = args.todo
    config = load_config(cli_overrides=cli_overrides or None)

    report = run_doctor(config)
    print(format_report(report))
    return report.exit_status


# ---------------------------------------------------------------------------
# status subcommand
# ---------------------------------------------------------------------------


def _cmd_status(args: argparse.Namespace) -> int:
    """Implement ``jumar status [--json]``.

    Read-only: never creates a run directory.  Exit 0 always.
    """
    from .clock import capture_now
    from .config import load_config
    from .status import build_status, format_status, format_status_json

    cli_overrides: dict[str, object] = {}
    if getattr(args, "todo", None):
        cli_overrides["todo_path"] = args.todo
    config = load_config(cli_overrides=cli_overrides or None)

    now = capture_now(config)
    todo_path = Path(config.todo_path)
    runs_dir = Path(args.runs_dir) if getattr(args, "runs_dir", None) else Path("runs")

    report = build_status(todo_path, runs_dir, config, now)
    use_json: bool = getattr(args, "json", False)
    if use_json:
        print(format_status_json(report))
    else:
        print(format_status(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
