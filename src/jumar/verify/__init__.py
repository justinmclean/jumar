# SPDX-License-Identifier: Apache-2.0
"""Stage 6 — Verify: check-kind registry and dispatch.

Public API
----------
VerifyContext  – immutable context passed to every verifier function.
Verifier       – callable type ``(Check, VerifyContext) -> VerificationResult``.
VerifyError    – raised when no verifier is registered for a kind.
register()     – add (or replace) a verifier for a :class:`CheckKind`.
run_verify()   – look up the registered verifier, call it, journal the result.

Built-in verifiers are registered at module load time (bottom of this file).
Adding a new check kind requires only:
  1. Writing a verifier function with signature ``(Check, VerifyContext) -> VerificationResult``.
  2. Calling ``register(CheckKind.<new_kind>, the_function)`` once at startup.
No other module needs to be touched (AC6.7).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeAlias

if TYPE_CHECKING:
    from ..config import Config

from ..clock import stamp
from ..journal import VERIFICATION, Journal
from ..models import Check, CheckKind, HarnessInfo, Termination, Verdict, VerificationResult

# ---------------------------------------------------------------------------
# Context passed to every verifier
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifyContext:
    """Immutable execution context for one verification call."""

    cwd: Path
    run_dir: Path
    evidence_head_bytes: int
    subtask_id: str
    attempt_no: int
    # Optional judge harness fields — only required by kind=judge.
    harness: HarnessInfo | None = None
    judge_timeout_s: int = 300
    # Injectable agent runner for tests (default: harness.run_agent).
    judge_run_agent: Any | None = field(default=None, compare=False, hash=False)
    non_interactive: bool = False
    # Resolved run config. Optional so existing call sites keep working; when
    # absent the command verifier cannot consult the argv allow list and says
    # so rather than silently running whatever it was handed.
    config: Config | None = None
    # How the attempt being checked ended. run_verify refuses to run the check
    # for anything but a clean ending (see unclean_result).
    termination: Termination | None = None


# ---------------------------------------------------------------------------
# Callable type alias
# ---------------------------------------------------------------------------

Verifier: TypeAlias = Callable[[Check, VerifyContext], VerificationResult]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_registry: dict[CheckKind, Verifier] = {}


class VerifyError(Exception):
    """Raised when no verifier is registered for the requested check kind."""


def register(kind: CheckKind, verifier: Verifier) -> None:
    """Register *verifier* for *kind*, replacing any prior registration."""
    _registry[kind] = verifier


def run_verify(
    check: Check,
    *,
    journal: Journal,
    item_id: str,
    ctx: VerifyContext,
) -> VerificationResult:
    """Dispatch *check* to its registered verifier and journal the result.

    The ``VERIFICATION`` journal entry is written **after** the verifier
    returns but **before** this function returns, so it lands in the journal
    before the caller starts the next subtask (AC6.6).

    When ``ctx.termination`` says the attempt ended any way but cleanly, the
    check is **not run**: a step-capped, timed-out or truncated agent may have
    left a partial artefact, or one from an earlier attempt, that satisfies
    it. The result is ``failed`` with ``unclean_termination`` evidence instead
    (see :func:`unclean_result`), journalled the same way.

    Raises :class:`VerifyError` if no verifier is registered for
    ``check.kind``.
    """
    verifier = _registry.get(check.kind)
    if verifier is None:
        raise VerifyError(
            f"No verifier registered for check kind {check.kind.value!r}. "
            "Call register() before run_verify()."
        )

    if ctx.termination is not None and ctx.termination is not Termination.clean:
        result = unclean_result(check, ctx, ctx.termination)
    else:
        result = verifier(check, ctx)
        if ctx.termination is not None:
            result = dataclasses.replace(result, termination=ctx.termination)

    payload: dict[str, Any] = {
        "attempt_no": ctx.attempt_no,
        "verdict": result.verdict.value,
        "kind": result.kind.value,
        "summary": result.summary,
        "evidence": result.evidence,
        "evidence_path": result.evidence_path,
        "checked_at": result.checked_at,
    }
    if result.termination is not None:
        payload["termination"] = result.termination.value
    journal.append(VERIFICATION, subtask_id=ctx.subtask_id, item_id=item_id, payload=payload)

    return result


def unclean_result(
    check: Check, ctx: VerifyContext, termination: Termination
) -> VerificationResult:
    """The verdict for an attempt that did not end cleanly: ``failed``, unchecked.

    The termination class rides on the verdict and in its evidence, so the
    repair prompt, the journal and the report all say why the work was not
    trusted, rather than showing a check that happened to pass.
    """
    return VerificationResult(
        subtask_id=ctx.subtask_id,
        attempt_no=ctx.attempt_no,
        verdict=Verdict.failed,
        kind=check.kind,
        evidence={
            "error": "unclean_termination",
            "termination": termination.value,
            "check_run": False,
        },
        summary=(
            f"the agent's run ended non-clean ({termination.value}), so what it left "
            "behind was not checked; finish the subtask so the run ends normally"
        ),
        evidence_path=None,
        checked_at=stamp(),
        termination=termination,
    )


# ---------------------------------------------------------------------------
# Register built-in verifiers — must be last to avoid circular imports.
# ---------------------------------------------------------------------------

from .command import verify_command as _verify_command  # noqa: E402
from .file import verify_absence as _verify_absence  # noqa: E402
from .file import verify_file as _verify_file  # noqa: E402
from .judge import verify_judge as _verify_judge  # noqa: E402
from .manual import verify_manual as _verify_manual  # noqa: E402

register(CheckKind.command, _verify_command)
register(CheckKind.file, _verify_file)
register(CheckKind.absence, _verify_absence)
register(CheckKind.judge, _verify_judge)
register(CheckKind.manual, _verify_manual)
