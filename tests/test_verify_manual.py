# SPDX-License-Identifier: Apache-2.0
"""Tests for verify/manual.py — Stage 6: Verify, kind=manual.

Acceptance criteria covered:
- AC6.5  A ``manual`` check under ``--non-interactive`` yields ``inconclusive``
         and never auto-passes, regardless of the check statement.

Also covers:
- Interactive "y" / "yes" → passed with evidence (who, when, response).
- Interactive "n" / "no"  → failed with evidence.
- Interactive unrecognised input → inconclusive.
- EOF / empty stdin → inconclusive.
- ``manual`` kind is registered in the default verifier registry.
- ``run_verify`` journals a VERIFICATION entry for kind=manual.
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

from jumar.journal import Journal
from jumar.models import Check, CheckKind, Verdict
from jumar.verify import VerifyContext, _registry, run_verify
from jumar.verify.manual import verify_manual

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _manual_check(statement: str = "Confirm the output looks correct") -> Check:
    return Check(kind=CheckKind.manual, statement=statement)


def _ctx(
    tmp_path: Path,
    *,
    non_interactive: bool = False,
    subtask_id: str = "item#0",
    attempt_no: int = 0,
) -> VerifyContext:
    return VerifyContext(
        cwd=tmp_path,
        run_dir=tmp_path,
        evidence_head_bytes=4000,
        subtask_id=subtask_id,
        attempt_no=attempt_no,
        non_interactive=non_interactive,
    )


def _run_interactive(response: str, tmp_path: Path, statement: str = "Check statement") -> object:
    """Call verify_manual with a fake stdin returning *response*."""
    check = _manual_check(statement)
    ctx = _ctx(tmp_path, non_interactive=False)
    fake_stdin = io.StringIO(response + "\n")
    fake_stdout = io.StringIO()
    with patch("sys.stdin", fake_stdin), patch("sys.stdout", fake_stdout):
        return verify_manual(check, ctx)


# ---------------------------------------------------------------------------
# AC6.5 — non-interactive always yields inconclusive, never passed
# ---------------------------------------------------------------------------


class TestNonInteractive:
    def test_non_interactive_yields_inconclusive(self, tmp_path: Path) -> None:
        """AC6.5: manual check under --non-interactive is inconclusive."""
        result = verify_manual(_manual_check(), _ctx(tmp_path, non_interactive=True))
        assert result.verdict == Verdict.inconclusive

    def test_non_interactive_never_auto_passes(self, tmp_path: Path) -> None:
        """AC6.5: inconclusive, not passed — even if the statement says it should."""
        result = verify_manual(
            _manual_check("Always passing check"),
            _ctx(tmp_path, non_interactive=True),
        )
        assert result.verdict != Verdict.passed

    def test_non_interactive_evidence_flags_reason(self, tmp_path: Path) -> None:
        result = verify_manual(_manual_check(), _ctx(tmp_path, non_interactive=True))
        assert result.evidence.get("non_interactive") is True
        assert result.evidence.get("reason") == "non_interactive"

    def test_non_interactive_summary_mentions_mode(self, tmp_path: Path) -> None:
        result = verify_manual(_manual_check(), _ctx(tmp_path, non_interactive=True))
        assert "non-interactive" in result.summary.lower()

    def test_non_interactive_does_not_read_stdin(self, tmp_path: Path) -> None:
        """Non-interactive path must not block on stdin."""

        # A stdin that raises on read proves the path was never taken.
        class _RaisingStdin(io.RawIOBase):
            def readline(self, size: int = -1) -> bytes:
                raise AssertionError("stdin must not be read in non-interactive mode")

        with patch("sys.stdin", _RaisingStdin()):
            result = verify_manual(_manual_check(), _ctx(tmp_path, non_interactive=True))
        assert result.verdict == Verdict.inconclusive

    def test_non_interactive_kind_is_manual(self, tmp_path: Path) -> None:
        result = verify_manual(_manual_check(), _ctx(tmp_path, non_interactive=True))
        assert result.kind == CheckKind.manual

    def test_non_interactive_subtask_id_preserved(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, non_interactive=True, subtask_id="task#3", attempt_no=1)
        result = verify_manual(_manual_check(), ctx)
        assert result.subtask_id == "task#3"
        assert result.attempt_no == 1


# ---------------------------------------------------------------------------
# Interactive paths — yes / no / unrecognised
# ---------------------------------------------------------------------------


class TestInteractiveYes:
    def test_y_yields_passed(self, tmp_path: Path) -> None:
        result = _run_interactive("y", tmp_path)
        assert result.verdict == Verdict.passed  # type: ignore[union-attr]

    def test_yes_yields_passed(self, tmp_path: Path) -> None:
        result = _run_interactive("yes", tmp_path)
        assert result.verdict == Verdict.passed  # type: ignore[union-attr]

    def test_y_evidence_has_who_when_response(self, tmp_path: Path) -> None:
        result = _run_interactive("y", tmp_path)
        ev = result.evidence  # type: ignore[union-attr]
        assert "who" in ev
        assert "when" in ev
        assert ev["response"] == "y"

    def test_y_summary_mentions_passed(self, tmp_path: Path) -> None:
        result = _run_interactive("y", tmp_path)
        assert "passed" in result.summary.lower()  # type: ignore[union-attr]


class TestInteractiveNo:
    def test_n_yields_failed(self, tmp_path: Path) -> None:
        result = _run_interactive("n", tmp_path)
        assert result.verdict == Verdict.failed  # type: ignore[union-attr]

    def test_no_yields_failed(self, tmp_path: Path) -> None:
        result = _run_interactive("no", tmp_path)
        assert result.verdict == Verdict.failed  # type: ignore[union-attr]

    def test_n_evidence_response_is_n(self, tmp_path: Path) -> None:
        result = _run_interactive("n", tmp_path)
        assert result.evidence["response"] == "n"  # type: ignore[union-attr]

    def test_n_summary_mentions_failed(self, tmp_path: Path) -> None:
        result = _run_interactive("n", tmp_path)
        assert "failed" in result.summary.lower()  # type: ignore[union-attr]


class TestInteractiveUnrecognised:
    def test_empty_response_inconclusive(self, tmp_path: Path) -> None:
        result = _run_interactive("", tmp_path)
        assert result.verdict == Verdict.inconclusive  # type: ignore[union-attr]

    def test_unrecognised_text_inconclusive(self, tmp_path: Path) -> None:
        result = _run_interactive("maybe", tmp_path)
        assert result.verdict == Verdict.inconclusive  # type: ignore[union-attr]

    def test_unrecognised_response_in_evidence(self, tmp_path: Path) -> None:
        result = _run_interactive("maybe", tmp_path)
        assert result.evidence["response"] == "maybe"  # type: ignore[union-attr]

    def test_statement_in_check_is_displayed(self, tmp_path: Path) -> None:
        fake_stdout = io.StringIO()
        check = _manual_check("Confirm the widget is green")
        ctx = _ctx(tmp_path, non_interactive=False)
        with patch("sys.stdin", io.StringIO("y\n")), patch("sys.stdout", fake_stdout):
            verify_manual(check, ctx)
        output = fake_stdout.getvalue()
        assert "Confirm the widget is green" in output


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestRegistryIntegration:
    def test_manual_kind_registered(self) -> None:
        assert CheckKind.manual in _registry

    def test_run_verify_manual_non_interactive_journals_verdict(self, tmp_path: Path) -> None:
        """run_verify writes a VERIFICATION journal entry for kind=manual."""
        journal_path = tmp_path / "journal.jsonl"
        journal = Journal(journal_path, "run-manual-test")
        ctx = _ctx(tmp_path, non_interactive=True)
        check = _manual_check()

        result = run_verify(check, journal=journal, item_id="item1", ctx=ctx)

        assert result.verdict == Verdict.inconclusive

        import json

        entries = [json.loads(line) for line in journal_path.read_text().splitlines()]
        ventry = next((e for e in entries if e["event"] == "verification"), None)
        assert ventry is not None, "VERIFICATION entry missing from journal"
        assert ventry["payload"]["kind"] == "manual"
        assert ventry["payload"]["verdict"] == "inconclusive"

    def test_run_verify_manual_interactive_passed_journals(self, tmp_path: Path) -> None:
        journal_path = tmp_path / "journal.jsonl"
        journal = Journal(journal_path, "run-manual-test-2")
        ctx = _ctx(tmp_path, non_interactive=False)
        check = _manual_check()

        with patch("sys.stdin", io.StringIO("y\n")), patch("sys.stdout", io.StringIO()):
            result = run_verify(check, journal=journal, item_id="item1", ctx=ctx)

        assert result.verdict == Verdict.passed

        import json

        entries = [json.loads(line) for line in journal_path.read_text().splitlines()]
        ventry = next((e for e in entries if e["event"] == "verification"), None)
        assert ventry is not None
        assert ventry["payload"]["verdict"] == "passed"
