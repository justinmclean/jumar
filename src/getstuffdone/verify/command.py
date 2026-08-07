# SPDX-License-Identifier: Apache-2.0
"""Stage 6 verifier — kind=command.

Runs the ``Check.command`` argv list with ``shell=False``, captures
stdout/stderr, and compares the exit status to ``check.expect_status``.

Rules:
- A binary not found on PATH yields ``inconclusive``, not ``failed`` (AC6.3).
- An exit status different from ``expect_status`` yields ``failed`` (AC6.1).
- An expected exit status match (plus optional ``expect_stdout`` regex) yields
  ``passed``.
- A command that times out yields ``failed`` (the binary was present and ran).
"""

from __future__ import annotations

import re
import subprocess
import time
from typing import TYPE_CHECKING

from ..clock import stamp
from ..models import Check, CheckKind, Verdict, VerificationResult

if TYPE_CHECKING:
    from . import VerifyContext


def verify_command(check: Check, ctx: VerifyContext) -> VerificationResult:
    """Run ``check.command``, capture output, and return a VerificationResult.

    Never raises — all failure modes are encoded in the returned result.
    """
    assert check.command is not None, "kind=command invariant: command must be set"

    argv: list[str] = list(check.command)
    checked_at = stamp()
    t0 = time.monotonic()

    stdout_bytes: bytes
    stderr_bytes: bytes
    exit_status: int
    timed_out: bool

    try:
        proc = subprocess.run(  # noqa: S603
            argv,
            cwd=ctx.cwd,
            capture_output=True,
            timeout=check.timeout_s,
            shell=False,
            check=False,
        )
        duration_s = time.monotonic() - t0
        stdout_bytes = proc.stdout
        stderr_bytes = proc.stderr
        exit_status = proc.returncode
        timed_out = False

    except FileNotFoundError:
        # Binary not on PATH — inconclusive (AC6.3).
        return VerificationResult(
            subtask_id=ctx.subtask_id,
            attempt_no=ctx.attempt_no,
            verdict=Verdict.inconclusive,
            kind=CheckKind.command,
            evidence={
                "argv": argv,
                "error": "binary_not_found",
                "command": argv[0],
            },
            summary=f"Verifier command not found: {argv[0]!r}",
            evidence_path=None,
            checked_at=checked_at,
        )

    except subprocess.TimeoutExpired as exc:
        duration_s = time.monotonic() - t0
        stdout_bytes = exc.stdout if isinstance(exc.stdout, bytes) else b""
        stderr_bytes = exc.stderr if isinstance(exc.stderr, bytes) else b""
        exit_status = -1
        timed_out = True

    stdout_text = stdout_bytes.decode(errors="replace")
    stderr_text = stderr_bytes.decode(errors="replace")
    head = ctx.evidence_head_bytes
    stdout_head = stdout_text[:head]
    stderr_head = stderr_text[:head]

    evidence: dict[str, object] = {
        "argv": argv,
        "exit_status": exit_status,
        "stdout_head": stdout_head,
        "stderr_head": stderr_head,
        "duration_s": round(duration_s, 3),
    }

    # Write full output as artefact file.
    artefacts_dir = ctx.run_dir / "artifacts"
    artefacts_dir.mkdir(parents=True, exist_ok=True)
    safe_id = ctx.subtask_id.replace("#", "-")
    artefact_name = f"{safe_id}-check-{ctx.attempt_no}.txt"
    artefact_file = artefacts_dir / artefact_name
    artefact_file.write_text(
        f"command: {' '.join(argv)}\n"
        f"exit_status: {exit_status}\n"
        f"=== stdout ===\n{stdout_text}\n"
        f"=== stderr ===\n{stderr_text}\n",
        encoding="utf-8",
    )
    evidence_path: str | None = str(artefact_file)

    if timed_out:
        verdict = Verdict.failed
        summary = f"Command timed out after {check.timeout_s}s: {' '.join(argv)}"
    elif exit_status != check.expect_status:
        # AC6.1: non-zero (or unexpected) exit → failed.
        verdict = Verdict.failed
        summary = f"Command exited {exit_status} (expected {check.expect_status}): {' '.join(argv)}"
    elif check.expect_stdout is not None and not re.search(check.expect_stdout, stdout_text):
        verdict = Verdict.failed
        summary = (
            f"Command exited {exit_status} but stdout did not match pattern {check.expect_stdout!r}"
        )
    else:
        verdict = Verdict.passed
        summary = f"Command exited {exit_status} as expected: {' '.join(argv)}"

    return VerificationResult(
        subtask_id=ctx.subtask_id,
        attempt_no=ctx.attempt_no,
        verdict=verdict,
        kind=CheckKind.command,
        evidence=evidence,
        summary=summary,
        evidence_path=evidence_path,
        checked_at=checked_at,
    )
