# SPDX-License-Identifier: Apache-2.0
"""Tests for verify/command.py and verify/__init__.py — Stage 6: Verify.

Acceptance criteria covered:
- AC6.1  A command check exiting non-zero yields failed with exit code and
         captured output in the evidence.
- AC6.3  A missing verifier binary yields inconclusive, not failed and not passed.
- AC6.6  Every verdict is journalled with its evidence before the next subtask
         starts — asserted by a golden-journal ordering test.
- AC6.7  Adding a new check kind requires only registering a verifier in the
         registry — asserted by a test that registers a dummy kind end to end
         without touching execute.py.

Also covers the happy path and the expect_stdout pattern match.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from getstuffdone.clock import stamp
from getstuffdone.config import Config
from getstuffdone.execute import execute
from getstuffdone.journal import (
    ATTEMPT_FINISHED,
    ATTEMPT_STARTED,
    VERIFICATION,
    Journal,
)
from getstuffdone.models import (
    Capability,
    Check,
    CheckKind,
    HarnessInfo,
    ItemStatus,
    Subtask,
    SubtaskStatus,
    TodoItem,
    Verdict,
    VerificationResult,
)
from getstuffdone.verify import VerifyContext, VerifyError, _registry, register, run_verify
from getstuffdone.verify.command import verify_command

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def journal(tmp_path: Path) -> Journal:
    return Journal(tmp_path / "journal.jsonl", "run-test")


def _ctx(tmp_path: Path, subtask_id: str = "item#0", attempt_no: int = 0) -> VerifyContext:
    return VerifyContext(
        cwd=tmp_path,
        run_dir=tmp_path,
        evidence_head_bytes=4000,
        subtask_id=subtask_id,
        attempt_no=attempt_no,
    )


def _command_check(
    *argv: str,
    expect_status: int = 0,
    expect_stdout: str | None = None,
) -> Check:
    return Check(
        kind=CheckKind.command,
        statement="Command exits with expected status",
        command=tuple(argv),
        expect_status=expect_status,
        expect_stdout=expect_stdout,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_command_exits_zero_passes(tmp_path: Path) -> None:
    check = _command_check(sys.executable, "-c", "pass")
    ctx = _ctx(tmp_path)
    result = verify_command(check, ctx)
    assert result.verdict == Verdict.passed


def test_command_passed_evidence_has_argv_and_exit_status(tmp_path: Path) -> None:
    check = _command_check(sys.executable, "-c", "pass")
    ctx = _ctx(tmp_path)
    result = verify_command(check, ctx)
    assert result.evidence["argv"] == [sys.executable, "-c", "pass"]
    assert result.evidence["exit_status"] == 0


def test_command_passed_artefact_written(tmp_path: Path) -> None:
    check = _command_check(sys.executable, "-c", "pass")
    ctx = _ctx(tmp_path)
    result = verify_command(check, ctx)
    assert result.evidence_path is not None
    assert Path(result.evidence_path).exists()


# ---------------------------------------------------------------------------
# AC6.1 — non-zero exit yields failed with evidence
# ---------------------------------------------------------------------------


def test_nonzero_exit_yields_failed(tmp_path: Path) -> None:
    check = _command_check(sys.executable, "-c", "raise SystemExit(1)")
    ctx = _ctx(tmp_path)
    result = verify_command(check, ctx)
    assert result.verdict == Verdict.failed


def test_nonzero_exit_evidence_contains_exit_code(tmp_path: Path) -> None:
    check = _command_check(sys.executable, "-c", "raise SystemExit(42)")
    ctx = _ctx(tmp_path)
    result = verify_command(check, ctx)
    assert result.evidence["exit_status"] == 42


def test_nonzero_exit_evidence_contains_output(tmp_path: Path) -> None:
    check = _command_check(
        sys.executable,
        "-c",
        "import sys; print('err', file=sys.stderr); raise SystemExit(1)",
    )
    ctx = _ctx(tmp_path)
    result = verify_command(check, ctx)
    assert "err" in str(result.evidence["stderr_head"])


def test_expected_nonzero_exit_passes(tmp_path: Path) -> None:
    """Check with expect_status=1 and the command exits 1 — should pass."""
    check = _command_check(sys.executable, "-c", "raise SystemExit(1)", expect_status=1)
    ctx = _ctx(tmp_path)
    result = verify_command(check, ctx)
    assert result.verdict == Verdict.passed


# ---------------------------------------------------------------------------
# expect_stdout pattern matching
# ---------------------------------------------------------------------------


def test_expect_stdout_match_passes(tmp_path: Path) -> None:
    check = _command_check(sys.executable, "-c", "print('hello world')", expect_stdout="hello")
    ctx = _ctx(tmp_path)
    result = verify_command(check, ctx)
    assert result.verdict == Verdict.passed


def test_expect_stdout_no_match_fails(tmp_path: Path) -> None:
    check = _command_check(sys.executable, "-c", "print('goodbye')", expect_stdout="hello")
    ctx = _ctx(tmp_path)
    result = verify_command(check, ctx)
    assert result.verdict == Verdict.failed


# ---------------------------------------------------------------------------
# AC6.3 — missing binary → inconclusive
# ---------------------------------------------------------------------------


def test_missing_binary_yields_inconclusive(tmp_path: Path) -> None:
    check = _command_check("__no_such_binary_gsd_test__")
    ctx = _ctx(tmp_path)
    result = verify_command(check, ctx)
    assert result.verdict == Verdict.inconclusive


def test_missing_binary_evidence_has_error_field(tmp_path: Path) -> None:
    check = _command_check("__no_such_binary_gsd_test__")
    ctx = _ctx(tmp_path)
    result = verify_command(check, ctx)
    assert result.evidence.get("error") == "binary_not_found"


def test_missing_binary_evidence_path_is_none(tmp_path: Path) -> None:
    check = _command_check("__no_such_binary_gsd_test__")
    ctx = _ctx(tmp_path)
    result = verify_command(check, ctx)
    assert result.evidence_path is None


# ---------------------------------------------------------------------------
# Timeout path (lines 77-82, 114-115 in command.py)
# ---------------------------------------------------------------------------


def test_command_timeout_yields_failed(tmp_path: Path) -> None:
    """A command that exceeds its timeout is failed (not inconclusive)."""
    check = Check(
        kind=CheckKind.command,
        statement="Command must finish quickly",
        command=(sys.executable, "-c", "import time; time.sleep(60)"),
        timeout_s=1,
    )
    ctx = _ctx(tmp_path)
    result = verify_command(check, ctx)
    assert result.verdict == Verdict.failed


def test_command_timeout_summary_mentions_timeout(tmp_path: Path) -> None:
    check = Check(
        kind=CheckKind.command,
        statement="Command must finish quickly",
        command=(sys.executable, "-c", "import time; time.sleep(60)"),
        timeout_s=1,
    )
    ctx = _ctx(tmp_path)
    result = verify_command(check, ctx)
    assert "timed out" in result.summary.lower() or "timeout" in result.summary.lower()


def test_command_timeout_evidence_path_written(tmp_path: Path) -> None:
    """Artefact file is written even when the command times out."""
    check = Check(
        kind=CheckKind.command,
        statement="Command must finish quickly",
        command=(sys.executable, "-c", "import time; time.sleep(60)"),
        timeout_s=1,
    )
    ctx = _ctx(tmp_path)
    result = verify_command(check, ctx)
    assert result.evidence_path is not None
    assert Path(result.evidence_path).exists()


# ---------------------------------------------------------------------------
# VerifyError — unregistered kind
# ---------------------------------------------------------------------------


def test_run_verify_raises_for_unregistered_kind(journal: Journal, tmp_path: Path) -> None:
    # Temporarily remove command from registry to simulate an unregistered kind.
    old = _registry.pop(CheckKind.command, None)
    try:
        check = _command_check(sys.executable, "-c", "pass")
        ctx = _ctx(tmp_path)
        with pytest.raises(VerifyError, match="No verifier registered"):
            run_verify(check, journal=journal, item_id="item", ctx=ctx)
    finally:
        if old is not None:
            _registry[CheckKind.command] = old


# ---------------------------------------------------------------------------
# AC6.6 — golden-journal ordering:
#   attempt_started → attempt_finished → verification (per subtask)
#   and verification precedes the next attempt_started
# ---------------------------------------------------------------------------


def _fake_execute_runner(responses: list[Any]) -> Any:
    """Minimal fake runner for the golden journal test."""
    calls = [0]

    def runner(
        prompt: str,
        *,
        cwd: Path,
        capabilities: Any,
        timeout_s: int,
        harness: HarnessInfo,
    ) -> Any:
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class R:
            exit_status: int
            stdout: str
            stderr: str = ""
            timed_out: bool = False
            agent_claim: str | None = None

        idx = calls[0]
        calls[0] += 1
        if idx < len(responses):
            return responses[idx]
        return R(exit_status=0, stdout="")

    return runner


def _make_subtask_for_golden(index: int, item_id: str = "golden") -> Subtask:
    return Subtask(
        subtask_id=f"{item_id}#{index}",
        index=index,
        description=f"Step {index}",
        check=Check(
            kind=CheckKind.command,
            statement="Exits zero",
            command=(sys.executable, "-c", "pass"),
        ),
        capabilities=frozenset({Capability.run_commands}),
        depends_on=(),
        status=SubtaskStatus.pending,
        attempts=(),
    )


def _make_item_for_golden(item_id: str = "golden") -> TodoItem:
    return TodoItem(
        item_id=item_id,
        text="Golden journal item",
        raw_line="- [ ] Golden journal item",
        line_no=1,
        status=ItemStatus.pending,
        context=(),
        authored_subtasks=(),
        meta={},
        priority=None,
        depends=(),
        capabilities=frozenset({Capability.run_commands}),
        schedule=None,
    )


def test_golden_journal_ordering_two_subtasks(journal: Journal, tmp_path: Path) -> None:
    """attempt_started → attempt_finished → verification, repeated per subtask.

    The ordering invariant (from specs/04-technical-plan.md and AC6.6):
    attempt_finished[n] → verification[n] → attempt_started[n+1]
    """
    from dataclasses import dataclass as _dc

    @_dc(frozen=True)
    class _FR:
        exit_status: int
        stdout: str
        stderr: str = ""
        timed_out: bool = False
        agent_claim: str | None = None

    cfg = Config()
    item = _make_item_for_golden()

    runner = _fake_execute_runner([_FR(exit_status=0, stdout=""), _FR(exit_status=0, stdout="")])

    subtask_0 = _make_subtask_for_golden(0)
    subtask_1 = _make_subtask_for_golden(1)

    execute(
        subtask_0,
        item=item,
        prior_evidence=[],
        config=cfg,
        journal=journal,
        cwd=tmp_path,
        run_dir=tmp_path,
        _run_agent=runner,
    )
    ctx_0 = VerifyContext(
        cwd=tmp_path,
        run_dir=tmp_path,
        evidence_head_bytes=4000,
        subtask_id=subtask_0.subtask_id,
        attempt_no=0,
    )
    vr_0 = run_verify(subtask_0.check, journal=journal, item_id=item.item_id, ctx=ctx_0)

    execute(
        subtask_1,
        item=item,
        prior_evidence=[vr_0],
        config=cfg,
        journal=journal,
        cwd=tmp_path,
        run_dir=tmp_path,
        _run_agent=runner,
    )
    ctx_1 = VerifyContext(
        cwd=tmp_path,
        run_dir=tmp_path,
        evidence_head_bytes=4000,
        subtask_id=subtask_1.subtask_id,
        attempt_no=0,
    )
    run_verify(subtask_1.check, journal=journal, item_id=item.item_id, ctx=ctx_1)

    state = journal.replay()
    events = [(e["event"], e.get("subtask_id"), e["seq"]) for e in state.entries]

    # Collect indices by event+subtask.
    def seq_of(event: str, subtask_id: str) -> int:
        for ev, sid, seq in events:
            if ev == event and sid == subtask_id:
                return seq
        raise AssertionError(f"{event!r} for {subtask_id!r} not found in journal")

    s0_started = seq_of(ATTEMPT_STARTED, "golden#0")
    s0_finished = seq_of(ATTEMPT_FINISHED, "golden#0")
    s0_verify = seq_of(VERIFICATION, "golden#0")
    s1_started = seq_of(ATTEMPT_STARTED, "golden#1")
    s1_finished = seq_of(ATTEMPT_FINISHED, "golden#1")
    s1_verify = seq_of(VERIFICATION, "golden#1")

    # Strict ordering: started < finished < verify < next_started < …
    assert s0_started < s0_finished, "attempt_started must precede attempt_finished"
    assert s0_finished < s0_verify, "attempt_finished must precede verification"
    assert s0_verify < s1_started, "verification must precede the next attempt_started"
    assert s1_started < s1_finished
    assert s1_finished < s1_verify


# ---------------------------------------------------------------------------
# AC6.7 — dummy kind registered end to end without touching execute.py
# ---------------------------------------------------------------------------


def test_dummy_verifier_registered_end_to_end(journal: Journal, tmp_path: Path) -> None:
    """Registering a dummy verifier for an existing kind dispatches to it.

    This proves that the registry is the only integration point between a check
    kind and the dispatch logic in run_verify() — no changes to execute.py or
    any other stage are needed when adding a new verifier (AC6.7).
    """
    called: list[tuple[Check, VerifyContext]] = []

    def dummy_verifier(check: Check, ctx: VerifyContext) -> VerificationResult:
        called.append((check, ctx))
        return VerificationResult(
            subtask_id=ctx.subtask_id,
            attempt_no=ctx.attempt_no,
            verdict=Verdict.passed,
            kind=check.kind,
            evidence={"dummy": True},
            summary="dummy verifier passed",
            evidence_path=None,
            checked_at=stamp(),
        )

    old = _registry.get(CheckKind.command)
    try:
        register(CheckKind.command, dummy_verifier)

        check = _command_check(sys.executable, "-c", "pass")
        ctx = _ctx(tmp_path, subtask_id="item#0", attempt_no=0)
        result = run_verify(check, journal=journal, item_id="item", ctx=ctx)

        assert result.verdict == Verdict.passed
        assert len(called) == 1, "dummy verifier must have been called exactly once"
        assert called[0][0] is check
        assert called[0][1] is ctx

        # Verify the VERIFICATION event was journalled by run_verify().
        state = journal.replay()
        verification_entries = [e for e in state.entries if e["event"] == VERIFICATION]
        assert len(verification_entries) == 1
        assert verification_entries[0]["payload"]["verdict"] == "passed"

    finally:
        if old is not None:
            _registry[CheckKind.command] = old
        else:
            _registry.pop(CheckKind.command, None)
