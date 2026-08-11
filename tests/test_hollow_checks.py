# SPDX-License-Identifier: Apache-2.0
"""Tests for the three defences against hollow verification.

These exist because of a real run. An item was decomposed into fifteen
subtasks; fourteen of the fifteen checks were ``bash -c 'grep …'``, one was
``test -f`` against a file the fetch had left at zero bytes, and every one of
them passed. The item reported success while two of its three source documents
had never been downloaded and the drafts were written from the model's memory.

Nothing in the pipeline was broken. The checks were.

Three rules follow, and each has a test here:

1. A ``command`` check may not be a shell wrapper — ``bash -c`` turns an argv
   into a shell string and bypasses both the no-shell rule and the argv[0]
   allow list.
2. A ``command`` check must be capable of failing. ``true``, ``echo``, and
   ``ls`` with no operands cannot.
3. A ``file`` check must fail on a zero-byte file. A failed fetch leaves the
   path present and empty, which ``test -f`` happily accepts.

Plus the allow list, which was defined, documented in four places, and called
from nowhere: a disallowed argv is ``inconclusive`` and spawns no process.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jumar.config import Config
from jumar.models import Check, CheckKind, Verdict
from jumar.verify import VerifyContext
from jumar.verify.command import verify_command
from jumar.verify.file import verify_file


def _ctx(tmp_path: Path, **kw: object) -> VerifyContext:
    return VerifyContext(
        cwd=tmp_path,
        run_dir=tmp_path / "run",
        evidence_head_bytes=4096,
        subtask_id="s1",
        attempt_no=0,
        **kw,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# 1. A check may not be a shell string wearing an argv
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ("bash", "-c", "grep -q OK out.txt"),
        ("sh", "-c", "test -f out.txt"),
        ("/bin/bash", "-c", "true"),
        ("zsh", "-c", "ls"),
        ("bash", "-lc", "make check"),
        ("dash", "--command", "true"),
    ],
)
def test_shell_wrapper_is_refused_at_construction(argv: tuple[str, ...]) -> None:
    """``bash -c`` defeats shell=False and the allow list in one argv element."""
    with pytest.raises(ValueError, match="shell wrapper"):
        Check(kind=CheckKind.command, statement="s", command=argv)


@pytest.mark.parametrize(
    "argv",
    [
        ("grep", "-q", "pattern", "file.txt"),
        ("make", "check"),
        ("python3", "-c", "import sys; sys.exit(0)"),
        ("bash", "script.sh"),  # a wrapper running a *file* is still an argv
        ("test", "-s", "out.txt"),
    ],
)
def test_a_real_argv_is_still_accepted(argv: tuple[str, ...]) -> None:
    """The rule targets inline shell strings, not every use of an interpreter."""
    check = Check(kind=CheckKind.command, statement="s", command=argv)
    assert check.command == argv


# ---------------------------------------------------------------------------
# 2. A check must be capable of failing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ("true",),
        ("/bin/true",),
        ("echo", "subtask complete"),
        ("printf", "ok"),
        ("date",),
        ("ls",),  # no operands: succeeds whatever the subtask did
        ("test",),
        ("ls", "-la"),  # flags are not operands
    ],
)
def test_a_check_that_cannot_fail_is_refused(argv: tuple[str, ...]) -> None:
    """An argv whose exit status is unrelated to the work proves nothing."""
    with pytest.raises(ValueError, match="cannot fail"):
        Check(kind=CheckKind.command, statement="s", command=argv)


@pytest.mark.parametrize(
    "argv",
    [
        ("ls", "out.txt"),
        ("test", "-s", "out.txt"),
        ("echo_results",),  # not `echo`; substring matching would be wrong here
    ],
)
def test_a_falsifiable_check_is_accepted(argv: tuple[str, ...]) -> None:
    assert Check(kind=CheckKind.command, statement="s", command=argv).command == argv


# ---------------------------------------------------------------------------
# 3. A failed fetch must fail the subtask
# ---------------------------------------------------------------------------


def test_zero_byte_file_fails_the_check(tmp_path: Path) -> None:
    """The exact shape of the real failure: HTTP 202, 0 bytes written, `test -f` passes."""
    (tmp_path / "ai-act.html").write_bytes(b"")
    check = Check(
        kind=CheckKind.file,
        statement="the AI Act text was fetched",
        path="ai-act.html",
    )

    result = verify_file(check, ctx=_ctx(tmp_path))

    assert result.verdict is Verdict.failed
    assert result.evidence["size_bytes"] == 0
    assert "empty" in result.summary.lower()


def test_non_empty_file_still_passes(tmp_path: Path) -> None:
    (tmp_path / "ai-act.html").write_text("Article 50 …\n")
    check = Check(kind=CheckKind.file, statement="fetched", path="ai-act.html")

    result = verify_file(check, ctx=_ctx(tmp_path))

    assert result.verdict is Verdict.passed
    assert result.evidence["size_bytes"] > 0


def test_missing_file_still_fails(tmp_path: Path) -> None:
    check = Check(kind=CheckKind.file, statement="fetched", path="nope.txt")
    assert verify_file(check, ctx=_ctx(tmp_path)).verdict is Verdict.failed


# ---------------------------------------------------------------------------
# 4. The allow list is consulted before the process is spawned
# ---------------------------------------------------------------------------


def test_a_denied_argv_is_inconclusive_and_spawns_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Denied is `inconclusive`, never `failed` and never `passed` (S1)."""
    import subprocess

    def explode(*_a: object, **_kw: object) -> object:
        raise AssertionError("a denied argv reached subprocess.run")

    monkeypatch.setattr(subprocess, "run", explode)

    check = Check(kind=CheckKind.command, statement="mail was sent", command=("sendmail", "-t"))
    result = verify_command(check, ctx=_ctx(tmp_path, config=Config()))

    assert result.verdict is Verdict.inconclusive
    assert result.evidence.get("error") == "capability_denied"
    assert "sendmail" in result.summary


def test_an_allowed_argv_still_runs(tmp_path: Path) -> None:
    (tmp_path / "out.txt").write_text("OK\n")
    check = Check(
        kind=CheckKind.command,
        statement="out.txt contains OK",
        command=("grep", "-q", "OK", "out.txt"),
    )

    result = verify_command(check, ctx=_ctx(tmp_path, config=Config()))

    assert result.verdict is Verdict.passed


def test_no_config_on_the_context_does_not_silently_allow_everything(
    tmp_path: Path,
) -> None:
    """A context without config falls back to the default policy, not to `allow all`."""
    check = Check(kind=CheckKind.command, statement="mail was sent", command=("sendmail", "-t"))
    result = verify_command(check, ctx=_ctx(tmp_path))
    assert result.verdict is Verdict.inconclusive


# ---------------------------------------------------------------------------
# 5. A pattern miss must be diagnosable
# ---------------------------------------------------------------------------


def test_pattern_miss_evidence_shows_what_the_file_says(tmp_path: Path) -> None:
    """Reproduces the 2026-08-09 run: three repairs burned on four characters.

    The check demanded ``Art 2(12)``; the draft said ``Article 2(12)``. The
    evidence was ``matched_excerpt: None`` and nothing else, so each repair
    attempt rewrote the prose rather than reconciling the spelling.
    """
    (tmp_path / "SCOPE.md").write_text(
        "# Scope\n\nUnder Article 2(12) the open-source exemption applies.\n"
    )
    check = Check(
        kind=CheckKind.file,
        statement="SCOPE.md analyses the distributor exemption",
        path="SCOPE.md",
        pattern=r"Art 2\(12\)",
    )

    result = verify_file(check, ctx=_ctx(tmp_path))

    assert result.verdict is Verdict.failed
    assert "Article 2(12)" in str(result.evidence["content_head"])


def test_pattern_miss_evidence_head_is_bounded(tmp_path: Path) -> None:
    """A large file must not paste itself wholesale into the repair prompt."""
    (tmp_path / "big.md").write_text("x" * 50_000)
    check = Check(kind=CheckKind.file, statement="s", path="big.md", pattern="nope")

    ctx = VerifyContext(
        cwd=tmp_path,
        run_dir=tmp_path / "run",
        evidence_head_bytes=1000,
        subtask_id="s1",
        attempt_no=0,
    )
    head = str(verify_file(check, ctx=ctx).evidence["content_head"])

    assert len(head) < 1200
    assert "truncated" in head


def test_a_tolerant_pattern_matches_both_spellings(tmp_path: Path) -> None:
    """The shape the decompose prompt now asks for."""
    check = Check(
        kind=CheckKind.file,
        statement="s",
        path="SCOPE.md",
        pattern=r"Art(icle)?\s*2\(12\)",
    )
    for text in ("see Article 2(12) here", "see Art 2(12) here"):
        (tmp_path / "SCOPE.md").write_text(text)
        assert verify_file(check, ctx=_ctx(tmp_path)).verdict is Verdict.passed
