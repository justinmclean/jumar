# SPDX-License-Identifier: Apache-2.0
"""Stage 6 verifier — kind=manual.

Prompts the human operator at the terminal to confirm whether the check
passes.  Under ``--non-interactive``, yields ``inconclusive`` immediately
rather than blocking a headless run (AC6.5).

Evidence records:
- ``who``            OS username of the confirming operator.
- ``when``           ISO-8601 UTC stamp of when the prompt was answered.
- ``response``       Raw text the operator entered (empty string on EOF).
- ``non_interactive``  True when the check was skipped due to headless mode.
"""

from __future__ import annotations

import getpass
import sys
from typing import TYPE_CHECKING

from ..clock import stamp
from ..models import Check, CheckKind, Verdict, VerificationResult

if TYPE_CHECKING:
    from . import VerifyContext


def verify_manual(check: Check, ctx: VerifyContext) -> VerificationResult:
    """Prompt the human to confirm the check; inconclusive if non-interactive.

    AC6.5: a ``manual`` check in a non-interactive run is ``inconclusive``,
    never auto-passed.
    """
    checked_at = stamp()

    if ctx.non_interactive:
        return VerificationResult(
            subtask_id=ctx.subtask_id,
            attempt_no=ctx.attempt_no,
            verdict=Verdict.inconclusive,
            kind=CheckKind.manual,
            evidence={"non_interactive": True, "reason": "non_interactive"},
            summary=f"Manual check skipped (non-interactive mode): {check.statement}",
            evidence_path=None,
            checked_at=checked_at,
        )

    try:
        who = getpass.getuser()
    except Exception:
        who = "unknown"

    sys.stdout.write(f"\nManual check: {check.statement}\n")
    sys.stdout.write("Did this succeed? [y/n] ")
    sys.stdout.flush()
    response = sys.stdin.readline().strip().lower()

    evidence: dict[str, object] = {
        "who": who,
        "when": checked_at,
        "response": response,
    }

    if response in ("y", "yes"):
        return VerificationResult(
            subtask_id=ctx.subtask_id,
            attempt_no=ctx.attempt_no,
            verdict=Verdict.passed,
            kind=CheckKind.manual,
            evidence=evidence,
            summary=f"Manual check passed (confirmed by {who}): {check.statement}",
            evidence_path=None,
            checked_at=checked_at,
        )

    if response in ("n", "no"):
        return VerificationResult(
            subtask_id=ctx.subtask_id,
            attempt_no=ctx.attempt_no,
            verdict=Verdict.failed,
            kind=CheckKind.manual,
            evidence=evidence,
            summary=f"Manual check failed (rejected by {who}): {check.statement}",
            evidence_path=None,
            checked_at=checked_at,
        )

    return VerificationResult(
        subtask_id=ctx.subtask_id,
        attempt_no=ctx.attempt_no,
        verdict=Verdict.inconclusive,
        kind=CheckKind.manual,
        evidence=evidence,
        summary=(
            f"Manual check inconclusive (response {response!r} not recognised): {check.statement}"
        ),
        evidence_path=None,
        checked_at=checked_at,
    )
