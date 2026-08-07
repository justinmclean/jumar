# SPDX-License-Identifier: Apache-2.0
"""Stage 7 — Repair: bounded retry on failed/inconclusive subtask verdicts.

Public API
----------
RepairExhausted  – raised when the repair budget is consumed without a pass.
repair()         – attempt up to config.max_repairs times; return passed result
                   or raise RepairExhausted.

AC7.1  A failing subtask is retried exactly max_repairs times, no more.
AC7.2  The repair prompt contains the failing check and its evidence.
AC7.3  The original check is always re-run; no repair may substitute another.
AC7.4  On exhaustion: item is failed, later subtasks skipped, next item
       selected (default) or run halted (--halt-on-fail).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import Config
from .execute import execute
from .journal import REPAIR_STARTED, Journal
from .models import Check, Subtask, TodoItem, Verdict, VerificationResult
from .verify import VerifyContext
from .verify import run_verify as _default_run_verify


class RepairExhausted(Exception):
    """All repair attempts consumed without passing.

    Callers check ``.halt`` to determine whether the run should stop
    (config.halt_on_fail=True) or continue with the next eligible item.
    ``.last_result`` carries the final non-passing verdict for reporting.
    """

    def __init__(self, last_result: VerificationResult, *, halt: bool) -> None:
        super().__init__(
            f"Repair budget exhausted for {last_result.subtask_id}: "
            f"last verdict={last_result.verdict.value}"
        )
        self.last_result = last_result
        self.halt = halt


# ---------------------------------------------------------------------------
# Repair prompt construction
# ---------------------------------------------------------------------------


def _format_evidence(evidence: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, val in evidence.items():
        if val is not None and val != "":
            parts.append(f"  {key}: {val}")
    return "\n".join(parts) if parts else "  (no evidence captured)"


def _build_repair_prompt(
    subtask: Subtask,
    item: TodoItem,
    prior_evidence: list[VerificationResult],
    last_result: VerificationResult,
) -> str:
    """Build the repair-attempt prompt.

    AC7.2: must include the failing check and its evidence so the agent
    knows exactly what did not pass and why.
    """
    check = subtask.check
    verdict = last_result.verdict.value
    evidence_text = _format_evidence(last_result.evidence)

    ctx_block = ("\nContext:\n" + "\n".join(item.context)) if item.context else ""

    prior_block = ""
    if prior_evidence:
        lines = ["\nVerified prior subtasks:"]
        for vr in prior_evidence:
            lines.append(f"  [{vr.subtask_id}] {vr.verdict.value} — {vr.summary}")
        prior_block = "\n".join(lines)

    check_lines = [f"Kind: {check.kind.value}", f"Statement: {check.statement}"]
    if check.command:
        check_lines.append(f"Command: {' '.join(check.command)}")
    if check.path:
        check_lines.append(f"Path: {check.path}")
    if check.pattern:
        check_lines.append(f"Pattern: {check.pattern}")
    if check.rationale:
        check_lines.append(f"Rationale: {check.rationale}")
    check_block = "\n".join(check_lines)

    return (
        "A previous attempt at this subtask produced a failing verdict.\n\n"
        f"Todo item: {item.text}{ctx_block}\n\n"
        f"Subtask {subtask.index + 1}: {subtask.description}\n\n"
        f"The check that must pass:\n{check_block}\n\n"
        f"Failing verdict: {verdict}\n"
        f"Evidence from the failing attempt:\n{evidence_text}"
        f"{prior_block}\n\n"
        "Please retry the subtask, directly addressing what caused the check "
        f"to produce verdict={verdict!r}. "
        "When done, summarise what you changed in one sentence."
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def repair(
    subtask: Subtask,
    *,
    first_result: VerificationResult,
    item: TodoItem,
    prior_evidence: list[VerificationResult],
    config: Config,
    journal: Journal,
    cwd: Path,
    run_dir: Path,
    _run_agent: Any | None = None,
    _run_verify: Any | None = None,
) -> VerificationResult:
    """Retry *subtask* up to ``config.max_repairs`` times after a non-pass.

    The same check (``subtask.check``) is always re-run after each repair —
    a repair may never substitute a different check (AC7.3).

    Returns the first passing :class:`VerificationResult`.
    Raises :class:`RepairExhausted` when the budget is consumed without a pass.

    Parameters
    ----------
    subtask:        The subtask to repair.
    first_result:   The non-passing result from the initial execution.
    item:           Parent todo item (for prompt context).
    prior_evidence: Passing results from earlier subtasks in the same plan.
    config:         Resolved run config (max_repairs, halt_on_fail, …).
    journal:        Append-only run journal.
    cwd:            Working directory for the agent subprocess.
    run_dir:        Directory for artefact files.
    _run_agent:     Injectable harness callable (tests only).
    _run_verify:    Injectable verifier callable (tests only; default: run_verify).
    """
    if first_result.verdict == Verdict.passed:
        return first_result

    run_verify_fn = _run_verify if _run_verify is not None else _default_run_verify
    original_check: Check = subtask.check  # Never replaced (AC7.3).

    last_result = first_result

    for repair_no in range(config.max_repairs):
        attempt_no = repair_no + 1  # attempt 0 = initial; 1..n = repairs (AC7.1)

        # Journal before re-executing so the record precedes the action.
        # AC7.2: payload carries the check and the failing evidence.
        journal.append(
            REPAIR_STARTED,
            subtask_id=subtask.subtask_id,
            item_id=item.item_id,
            payload={
                "repair_no": repair_no,
                "attempt_no": attempt_no,
                "previous_verdict": last_result.verdict.value,
                "check_kind": original_check.kind.value,
                "check_statement": original_check.statement,
                "evidence": last_result.evidence,
                "summary": last_result.summary,
            },
        )

        repair_prompt = _build_repair_prompt(subtask, item, prior_evidence, last_result)

        execute(
            subtask,
            item=item,
            prior_evidence=prior_evidence,
            config=config,
            journal=journal,
            cwd=cwd,
            run_dir=run_dir,
            attempt_no=attempt_no,
            _run_agent=_run_agent,
            prompt_override=repair_prompt,
        )

        # Always re-run the original check — never a modified substitute (AC7.3).
        ctx = VerifyContext(
            cwd=cwd,
            run_dir=run_dir,
            evidence_head_bytes=config.evidence_head_bytes,
            subtask_id=subtask.subtask_id,
            attempt_no=attempt_no,
        )
        last_result = run_verify_fn(original_check, journal=journal, item_id=item.item_id, ctx=ctx)

        if last_result.verdict == Verdict.passed:
            return last_result

    # Budget exhausted (AC7.4).
    raise RepairExhausted(last_result, halt=config.halt_on_fail)
