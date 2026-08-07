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

    # stubs — accepting the subcommand name is enough for smoke tests
    for cmd in ("run", "resume", "report", "schedule", "doctor"):
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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
