# SPDX-License-Identifier: Apache-2.0
"""Stage 6 verifiers — kind=file and kind=absence.

``verify_file``: passes when the named path exists and, if ``check.pattern``
is set, when the file contents match that regex.  Evidence always includes the
file's size and SHA-256 hash; on a pattern match it also includes the matched
excerpt (truncated to 200 chars).

``verify_absence``: passes when the named path or glob resolves to zero
matches.  Evidence records the raw path, the resolved glob pattern, and the
list of any unexpected matches (up to 20 entries).

Rules:
- A missing file yields ``failed`` for kind=file (AC6.2).
- A **zero-byte** file yields ``failed``. Existence is not evidence: a failed
  download, a truncated write and a successful one all leave a path behind, and
  only one of them is the work. Observed in a real run: two 0-byte fetches of a
  regulation, and a check asserting "5 files in sources/" that passed anyway.
- An unreadable file (permissions, broken symlink) yields ``inconclusive``.
- A present path yields ``failed`` for kind=absence.
- Relative paths are resolved against ``ctx.cwd``.
"""

from __future__ import annotations

import glob as _glob
import hashlib
import re
from pathlib import Path
from typing import TYPE_CHECKING

from ..clock import stamp
from ..models import Check, CheckKind, Verdict, VerificationResult

if TYPE_CHECKING:
    from . import VerifyContext


def _head(text: str, limit: int) -> str:
    """First *limit* characters of *text*, marked when truncated."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [truncated, {len(text)} characters total]"


def verify_file(check: Check, ctx: VerifyContext) -> VerificationResult:
    """Verify that a path exists, is readable, and optionally matches a regex."""
    assert check.path is not None, "kind=file invariant: path must be set"

    checked_at = stamp()
    raw_path = check.path
    file_path = Path(raw_path) if Path(raw_path).is_absolute() else ctx.cwd / raw_path

    if not file_path.exists():
        return VerificationResult(
            subtask_id=ctx.subtask_id,
            attempt_no=ctx.attempt_no,
            verdict=Verdict.failed,
            kind=CheckKind.file,
            evidence={"path": str(file_path), "error": "file_not_found"},
            summary=f"File not found: {file_path}",
            evidence_path=None,
            checked_at=checked_at,
        )

    if file_path.is_file() and file_path.stat().st_size == 0:
        return VerificationResult(
            subtask_id=ctx.subtask_id,
            attempt_no=ctx.attempt_no,
            verdict=Verdict.failed,
            kind=CheckKind.file,
            evidence={"path": str(file_path), "size_bytes": 0, "error": "file_is_empty"},
            summary=f"File exists but is empty (0 bytes): {raw_path}",
            evidence_path=None,
            checked_at=checked_at,
        )

    try:
        content = file_path.read_bytes()
    except OSError as exc:
        return VerificationResult(
            subtask_id=ctx.subtask_id,
            attempt_no=ctx.attempt_no,
            verdict=Verdict.inconclusive,
            kind=CheckKind.file,
            evidence={"path": str(file_path), "error": str(exc)},
            summary=f"Could not read file: {file_path}: {exc}",
            evidence_path=None,
            checked_at=checked_at,
        )

    sha256 = hashlib.sha256(content).hexdigest()
    size = len(content)
    text = content.decode(errors="replace")

    evidence: dict[str, object] = {
        "path": str(file_path),
        "size_bytes": size,
        "sha256": sha256,
    }

    if check.pattern is not None:
        match = re.search(check.pattern, text)
        if match is None:
            return VerificationResult(
                subtask_id=ctx.subtask_id,
                attempt_no=ctx.attempt_no,
                verdict=Verdict.failed,
                kind=CheckKind.file,
                evidence={
                    **evidence,
                    "pattern": check.pattern,
                    "matched_excerpt": None,
                    # WHAT THE FILE ACTUALLY SAYS. Without this the repair loop
                    # is told only "your pattern did not match" and has nothing
                    # to reconcile against; observed in a real run, where a
                    # check demanded "Art 2(12)" of a draft that said
                    # "Article 2(12)" and three repair attempts rewrote the
                    # prose instead of the four characters that mattered.
                    "content_head": _head(text, ctx.evidence_head_bytes),
                },
                summary=f"File exists but does not match pattern {check.pattern!r}: {file_path}",
                evidence_path=None,
                checked_at=checked_at,
            )
        evidence["pattern"] = check.pattern
        evidence["matched_excerpt"] = match.group(0)[:200]

    return VerificationResult(
        subtask_id=ctx.subtask_id,
        attempt_no=ctx.attempt_no,
        verdict=Verdict.passed,
        kind=CheckKind.file,
        evidence=evidence,
        summary=f"File exists ({size} bytes, sha256={sha256[:8]}…): {file_path}",
        evidence_path=None,
        checked_at=checked_at,
    )


def verify_absence(check: Check, ctx: VerifyContext) -> VerificationResult:
    """Verify that a path or glob pattern resolves to zero existing paths."""
    assert check.path is not None, "kind=absence invariant: path must be set"

    checked_at = stamp()
    raw_path = check.path

    # Anchor relative paths to cwd before glob expansion.
    glob_pattern = raw_path if Path(raw_path).is_absolute() else str(ctx.cwd / raw_path)

    matches = _glob.glob(glob_pattern, recursive=True)

    evidence: dict[str, object] = {
        "path": raw_path,
        "resolved_glob": glob_pattern,
        "matches": matches[:20],
    }

    if matches:
        return VerificationResult(
            subtask_id=ctx.subtask_id,
            attempt_no=ctx.attempt_no,
            verdict=Verdict.failed,
            kind=CheckKind.absence,
            evidence=evidence,
            summary=f"Path should be absent but {len(matches)} match(es) found: {matches[0]}",
            evidence_path=None,
            checked_at=checked_at,
        )

    return VerificationResult(
        subtask_id=ctx.subtask_id,
        attempt_no=ctx.attempt_no,
        verdict=Verdict.passed,
        kind=CheckKind.absence,
        evidence=evidence,
        summary=f"Path is absent as expected: {raw_path}",
        evidence_path=None,
        checked_at=checked_at,
    )
