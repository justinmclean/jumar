# SPDX-License-Identifier: Apache-2.0
"""Stage 4 — Gate: mode dispatch, capability refusal, dry-run / approve printing.

Public API
----------
GateMode          – the three run modes ("auto", "dry-run", "approve").
GateDecision      – outcome returned by gate().
GateStartupError  – raised before ingest when flags conflict (AC4.4).
GateError         – raised on per-item failures (e.g. capability_denied).
check_startup_flags() – validate before ingest (AC4.4).
gate()            – apply the gate to a Plan; return the decision (AC4.1–AC4.3).

Behaviour
---------
- ``--dry-run``: print the plan, journal the decision, execute nothing.
- ``--approve``: print the plan, wait for y/n on stdin; ``n`` records
  ``skipped_by_human``; ``y`` proceeds to execution.
- ``--auto`` (default): proceed straight to execution.

Capability check runs first, independently of mode.  A subtask that claims
a capability not granted to its parent item is refused before any mode
dispatch, naming the subtask and the denied capability (AC4.3).
"""

from __future__ import annotations

import enum
import sys
from typing import IO

from .journal import GATE_DECISION, Journal
from .models import Capability, FailureCode, Plan, TodoItem


class GateMode(enum.StrEnum):
    auto = "auto"
    dry_run = "dry-run"
    approve = "approve"


class GateDecision(enum.StrEnum):
    proceed = "proceed"
    skip = "skip"
    dry_run = "dry_run"


class GateStartupError(Exception):
    """Configuration error detected before ingest: --approve + --non-interactive."""


class GateError(Exception):
    """Per-item gate failure (capability_denied)."""

    def __init__(self, failure_code: FailureCode, message: str) -> None:
        super().__init__(message)
        self.failure_code = failure_code


def check_harness_policy(harness_name: str, *, allow_unrestricted_harness: bool) -> None:
    """Raise GateStartupError if an unrestricted harness is used without explicit opt-in.

    Harnesses in UNRESTRICTED_HARNESSES cannot express tool-level denials for
    git push, gh, or sendmail. Using one without allow_unrestricted_harness = true
    is a configuration error, not a runtime failure: raising here (before ingest)
    makes it visible immediately rather than silently running with weaker controls.
    """
    from .harness import UNRESTRICTED_HARNESSES

    if harness_name in UNRESTRICTED_HARNESSES and not allow_unrestricted_harness:
        raise GateStartupError(
            f"Harness {harness_name!r} cannot enforce tool-level denials (git push, gh, "
            "sendmail, etc.) — only 'claude' can express those via --disallowedTools. "
            "Set allow_unrestricted_harness = true in gsd.toml to acknowledge this and "
            "proceed (noting that the container is the actual control — see config.py)."
        )


def check_startup_flags(mode: str, *, non_interactive: bool) -> None:
    """Raise GateStartupError before ingest if --approve and --non-interactive conflict.

    --approve requires a human at the prompt; --non-interactive forbids blocking
    reads.  Together they have no safe resolution: auto-approving defeats the gate,
    auto-declining silently drops every item.  Caught here, before ingest, so no
    item is ever started (AC4.4).
    """
    if mode == GateMode.approve and non_interactive:
        raise GateStartupError(
            "--approve and --non-interactive are mutually exclusive: "
            "--approve waits for a human response, but --non-interactive "
            "forbids blocking reads. Remove one of these flags."
        )


def _print_plan(plan: Plan, item: TodoItem, out: IO[str]) -> None:
    print(f"Plan for: {item.text}", file=out)
    for st in plan.subtasks:
        caps = ", ".join(sorted(c.value for c in st.capabilities)) or "(none)"
        print(f"  [{st.index + 1}] {st.description}", file=out)
        print(f"      check ({st.check.kind.value}): {st.check.statement}", file=out)
        if st.check.command:
            print(f"      argv: {' '.join(st.check.command)}", file=out)
        if st.check.path:
            print(f"      path: {st.check.path}", file=out)
        if st.check.rationale:
            print(f"      rationale: {st.check.rationale}", file=out)
        print(f"      capabilities: {caps}", file=out)


def gate(
    plan: Plan,
    item: TodoItem,
    *,
    mode: str,
    journal: Journal,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
) -> GateDecision:
    """Apply the gate to *plan* and return the decision.

    The capability check runs unconditionally before mode dispatch.  Raises
    GateError(capability_denied) if any subtask claims a capability the item
    was not granted (AC4.3).
    """
    _in = stdin if stdin is not None else sys.stdin
    _out = stdout if stdout is not None else sys.stdout

    # AC4.3 — capability check is independent of mode.
    for subtask in plan.subtasks:
        denied: frozenset[Capability] = subtask.capabilities - item.capabilities
        if denied:
            cap = next(iter(sorted(denied)))
            raise GateError(
                FailureCode.capability_denied,
                f"Subtask {subtask.index + 1} ({subtask.description!r}) "
                f"requests capability {cap!r} not granted to item {item.item_id!r}",
            )

    if mode == GateMode.dry_run:
        _print_plan(plan, item, _out)
        journal.append(
            GATE_DECISION,
            item_id=item.item_id,
            payload={"mode": "dry-run", "decision": "dry_run"},
        )
        return GateDecision.dry_run

    if mode == GateMode.approve:
        _print_plan(plan, item, _out)
        print("Approve this plan? [y/n] ", end="", flush=True, file=_out)
        answer = _in.readline().strip().lower()
        if answer != "y":
            journal.append(
                GATE_DECISION,
                item_id=item.item_id,
                payload={"mode": "approve", "decision": "skip", "skipped_by_human": True},
            )
            return GateDecision.skip
        journal.append(
            GATE_DECISION,
            item_id=item.item_id,
            payload={"mode": "approve", "decision": "proceed"},
        )
        return GateDecision.proceed

    # auto
    journal.append(
        GATE_DECISION,
        item_id=item.item_id,
        payload={"mode": "auto", "decision": "proceed"},
    )
    return GateDecision.proceed
