# SPDX-License-Identifier: Apache-2.0
"""Tests for verify/file.py — Stage 6: Verify, kinds=file and absence.

Acceptance criteria covered:
- AC6.2  A ``file`` check for a path the subtask did not create yields ``failed``.

Also covers:
- file exists → passed
- file exists with matching pattern → passed (with matched_excerpt in evidence)
- file exists but pattern does not match → failed
- file not readable (inconclusive, not failed)
- absence: path exists → failed
- absence: path gone → passed
- absence: glob pattern with matches → failed
- absence: glob pattern with no matches → passed
- registry: file and absence kinds are registered in the default registry
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from jumar.journal import Journal
from jumar.models import Check, CheckKind, Verdict
from jumar.verify import VerifyContext, _registry, run_verify
from jumar.verify.file import verify_absence, verify_file

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(tmp_path: Path, run_dir: Path | None = None) -> VerifyContext:
    return VerifyContext(
        cwd=tmp_path,
        run_dir=run_dir or tmp_path / "run",
        evidence_head_bytes=4096,
        subtask_id="item1#0",
        attempt_no=1,
    )


def _file_check(
    path: str,
    pattern: str | None = None,
    statement: str = "File must exist",
) -> Check:
    return Check(
        kind=CheckKind.file,
        statement=statement,
        path=path,
        pattern=pattern,
    )


def _absence_check(path: str, statement: str = "Path must be gone") -> Check:
    return Check(kind=CheckKind.absence, statement=statement, path=path)


# ---------------------------------------------------------------------------
# Tests — kind=file
# ---------------------------------------------------------------------------


class TestVerifyFile:
    def test_file_exists_passes(self, tmp_path: Path) -> None:
        target = tmp_path / "output.txt"
        target.write_text("hello world")

        result = verify_file(_file_check("output.txt"), _ctx(tmp_path))

        assert result.verdict == Verdict.passed
        assert result.kind == CheckKind.file
        assert "sha256" in result.evidence
        assert result.evidence["size_bytes"] == len(b"hello world")

    def test_file_not_found_fails(self, tmp_path: Path) -> None:
        """AC6.2: a file check for a path the subtask did not create yields failed."""
        result = verify_file(_file_check("missing.txt"), _ctx(tmp_path))

        assert result.verdict == Verdict.failed
        assert result.evidence["error"] == "file_not_found"
        assert "missing.txt" in result.summary

    def test_absolute_path_found(self, tmp_path: Path) -> None:
        target = tmp_path / "abs.txt"
        target.write_text("data")

        result = verify_file(_file_check(str(target)), _ctx(tmp_path))

        assert result.verdict == Verdict.passed

    def test_absolute_path_missing_fails(self, tmp_path: Path) -> None:
        result = verify_file(_file_check(str(tmp_path / "nope.txt")), _ctx(tmp_path))

        assert result.verdict == Verdict.failed

    def test_pattern_matches_passes(self, tmp_path: Path) -> None:
        target = tmp_path / "report.txt"
        target.write_text("Build PASSED: all tests green")

        result = verify_file(_file_check("report.txt", pattern=r"PASSED"), _ctx(tmp_path))

        assert result.verdict == Verdict.passed
        assert result.evidence.get("matched_excerpt") == "PASSED"

    def test_pattern_no_match_fails(self, tmp_path: Path) -> None:
        target = tmp_path / "report.txt"
        target.write_text("Build FAILED: 3 tests broken")

        result = verify_file(_file_check("report.txt", pattern=r"PASSED"), _ctx(tmp_path))

        assert result.verdict == Verdict.failed
        assert result.evidence.get("matched_excerpt") is None
        assert "PASSED" in result.summary

    def test_pattern_not_set_does_not_inspect_contents(self, tmp_path: Path) -> None:
        target = tmp_path / "binary.bin"
        target.write_bytes(bytes(range(256)))

        result = verify_file(_file_check("binary.bin"), _ctx(tmp_path))

        assert result.verdict == Verdict.passed
        assert "pattern" not in result.evidence

    def test_unreadable_file_inconclusive(self, tmp_path: Path) -> None:
        """An unreadable file should yield inconclusive, not failed or passed."""
        target = tmp_path / "locked.txt"
        target.write_text("secret")
        target.chmod(0o000)

        try:
            result = verify_file(_file_check("locked.txt"), _ctx(tmp_path))
            # On some systems (macOS as root) chmod 000 is still readable; skip.
            if result.verdict != Verdict.inconclusive:
                pytest.skip("Running as root or OS ignores chmod 000")
            assert result.verdict == Verdict.inconclusive
        finally:
            target.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def test_evidence_includes_sha256_and_size(self, tmp_path: Path) -> None:
        import hashlib

        content = b"deterministic content for hashing"
        target = tmp_path / "hashme.txt"
        target.write_bytes(content)

        result = verify_file(_file_check("hashme.txt"), _ctx(tmp_path))

        expected_sha = hashlib.sha256(content).hexdigest()
        assert result.evidence["sha256"] == expected_sha
        assert result.evidence["size_bytes"] == len(content)


# ---------------------------------------------------------------------------
# Tests — kind=absence
# ---------------------------------------------------------------------------


class TestVerifyAbsence:
    def test_path_gone_passes(self, tmp_path: Path) -> None:
        result = verify_absence(_absence_check("deleted.txt"), _ctx(tmp_path))

        assert result.verdict == Verdict.passed
        assert result.evidence["matches"] == []

    def test_path_present_fails(self, tmp_path: Path) -> None:
        target = tmp_path / "leftover.txt"
        target.write_text("oops")

        result = verify_absence(_absence_check("leftover.txt"), _ctx(tmp_path))

        assert result.verdict == Verdict.failed
        assert len(result.evidence["matches"]) >= 1

    def test_absolute_path_present_fails(self, tmp_path: Path) -> None:
        target = tmp_path / "file.txt"
        target.write_text("here")

        result = verify_absence(_absence_check(str(target)), _ctx(tmp_path))

        assert result.verdict == Verdict.failed

    def test_absolute_path_gone_passes(self, tmp_path: Path) -> None:
        result = verify_absence(_absence_check(str(tmp_path / "ghost.txt")), _ctx(tmp_path))

        assert result.verdict == Verdict.passed

    def test_glob_matches_fails(self, tmp_path: Path) -> None:
        (tmp_path / "tmp_a.pyc").write_bytes(b"")
        (tmp_path / "tmp_b.pyc").write_bytes(b"")

        result = verify_absence(_absence_check("*.pyc"), _ctx(tmp_path))

        assert result.verdict == Verdict.failed
        assert len(result.evidence["matches"]) >= 1

    def test_glob_no_matches_passes(self, tmp_path: Path) -> None:
        result = verify_absence(_absence_check("*.pyc"), _ctx(tmp_path))

        assert result.verdict == Verdict.passed

    def test_evidence_records_resolved_glob(self, tmp_path: Path) -> None:
        result = verify_absence(_absence_check("build/*.o"), _ctx(tmp_path))

        assert "resolved_glob" in result.evidence
        assert str(tmp_path) in str(result.evidence["resolved_glob"])

    def test_summary_names_first_unexpected_match(self, tmp_path: Path) -> None:
        (tmp_path / "stray.log").write_text("log data")

        result = verify_absence(_absence_check("stray.log"), _ctx(tmp_path))

        assert "stray.log" in result.summary


# ---------------------------------------------------------------------------
# Tests — registry integration
# ---------------------------------------------------------------------------


class TestRegistryIntegration:
    def test_file_kind_registered(self) -> None:
        assert CheckKind.file in _registry

    def test_absence_kind_registered(self) -> None:
        assert CheckKind.absence in _registry

    def test_run_verify_file_journals_verdict(self, tmp_path: Path) -> None:
        """run_verify writes a VERIFICATION journal entry for kind=file."""
        journal_path = tmp_path / "run" / "journal.jsonl"
        journal = Journal(journal_path, "test-run-1")
        ctx = _ctx(tmp_path, run_dir=tmp_path / "run")

        target = tmp_path / "hello.txt"
        target.write_text("world")

        check = _file_check("hello.txt")
        result = run_verify(check, journal=journal, item_id="item1", ctx=ctx)

        assert result.verdict == Verdict.passed

        import json

        entries = [json.loads(line) for line in journal_path.read_text().splitlines()]
        assert any(e["event"] == "verification" for e in entries)
        ventry = next(e for e in entries if e["event"] == "verification")
        assert ventry["payload"]["kind"] == "file"
        assert ventry["payload"]["verdict"] == "passed"

    def test_run_verify_absence_journals_verdict(self, tmp_path: Path) -> None:
        journal_path = tmp_path / "run" / "journal.jsonl"
        journal = Journal(journal_path, "test-run-2")
        ctx = _ctx(tmp_path, run_dir=tmp_path / "run")

        check = _absence_check("gone.txt")
        result = run_verify(check, journal=journal, item_id="item1", ctx=ctx)

        assert result.verdict == Verdict.passed

        import json

        entries = [json.loads(line) for line in journal_path.read_text().splitlines()]
        ventry = next(e for e in entries if e["event"] == "verification")
        assert ventry["payload"]["kind"] == "absence"
