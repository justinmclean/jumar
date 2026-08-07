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

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from ..journal import VERIFICATION, Journal
from ..models import Check, CheckKind, VerificationResult

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

    Raises :class:`VerifyError` if no verifier is registered for
    ``check.kind``.
    """
    verifier = _registry.get(check.kind)
    if verifier is None:
        raise VerifyError(
            f"No verifier registered for check kind {check.kind.value!r}. "
            "Call register() before run_verify()."
        )

    result = verifier(check, ctx)

    journal.append(
        VERIFICATION,
        subtask_id=ctx.subtask_id,
        item_id=item_id,
        payload={
            "attempt_no": ctx.attempt_no,
            "verdict": result.verdict.value,
            "kind": result.kind.value,
            "summary": result.summary,
            "evidence": result.evidence,
            "evidence_path": result.evidence_path,
            "checked_at": result.checked_at,
        },
    )

    return result


# ---------------------------------------------------------------------------
# Register built-in verifiers — must be last to avoid circular imports.
# ---------------------------------------------------------------------------

from .command import verify_command as _verify_command  # noqa: E402
from .file import verify_absence as _verify_absence  # noqa: E402
from .file import verify_file as _verify_file  # noqa: E402

register(CheckKind.command, _verify_command)
register(CheckKind.file, _verify_file)
register(CheckKind.absence, _verify_absence)
