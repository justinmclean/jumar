# SPDX-License-Identifier: Apache-2.0
"""Tests for repair.py — Stage 7: Repair (bounded).

Acceptance criteria covered:
- AC7.1  A failing subtask is retried exactly max_repairs times, no more.
- AC7.2  The repair prompt contains the failing check and its evidence.
- AC7.3  A repair attempt that modifies the subtask's check is rejected and
         the original check is re-run.  Enforced by repair() always passing
         subtask.check to the verifier, never a substitute.
- AC7.4  On budget exhaustion the item is failed, later subtasks are not run,
         and the next eligible item is selected (default) or the run halts
         (--halt-on-fail).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jumar.clock import stamp
from jumar.config import Config
from jumar.journal import (
    ATTEMPT_STARTED,
    REPAIR_STARTED,
    Journal,
)
from jumar.models import (
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
from jumar.repair import RepairExhausted, repair
from jumar.verify import VerifyContext

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def journal(tmp_path: Path) -> Journal:
    return Journal(tmp_path / "journal.jsonl", "run-repair")


@pytest.fixture
def cfg() -> Config:
    return Config(max_repairs=2)


def _make_check(statement: str = "Command exits zero") -> Check:
    return Check(
        kind=CheckKind.command,
        statement=statement,
        command=("test", "-d", "."),
    )


def _make_subtask(
    index: int = 0,
    item_id: str = "item-01",
    check: Check | None = None,
) -> Subtask:
    return Subtask(
        subtask_id=f"{item_id}#{index}",
        index=index,
        description="Do something",
        check=check or _make_check(),
        capabilities=frozenset({Capability.run_commands}),
        depends_on=(),
        status=SubtaskStatus.pending,
        attempts=(),
    )


def _make_item(item_id: str = "item-01") -> TodoItem:
    return TodoItem(
        item_id=item_id,
        text="Fix the bug",
        raw_line="- [ ] Fix the bug",
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


def _failing_result(
    subtask_id: str,
    verdict: Verdict = Verdict.failed,
    attempt_no: int = 0,
) -> VerificationResult:
    return VerificationResult(
        subtask_id=subtask_id,
        attempt_no=attempt_no,
        verdict=verdict,
        kind=CheckKind.command,
        evidence={"exit_status": 1, "stdout_head": "", "stderr_head": "error message"},
        summary="Command exited 1, expected 0",
        evidence_path=None,
        checked_at=stamp(),
    )


def _passing_result(subtask_id: str, attempt_no: int = 1) -> VerificationResult:
    return VerificationResult(
        subtask_id=subtask_id,
        attempt_no=attempt_no,
        verdict=Verdict.passed,
        kind=CheckKind.command,
        evidence={"exit_status": 0, "stdout_head": "", "stderr_head": ""},
        summary="Command exited 0 as expected",
        evidence_path=None,
        checked_at=stamp(),
    )


def _make_fake_runner() -> Any:
    """Fake run_agent that records the prompt it received."""
    calls: list[str] = []

    class _R:
        exit_status = 0
        stdout = "ok"
        stderr = ""
        timed_out = False
        agent_claim: str | None = "done"

    def runner(
        prompt: str,
        *,
        cwd: Path,
        capabilities: Any,
        timeout_s: int,
        harness: HarnessInfo,
        session_id: str | None = None,
        session_is_new: bool = False,
    ) -> _R:
        calls.append(prompt)
        return _R()

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def _make_fake_verify(results: list[VerificationResult]) -> Any:
    """Fake run_verify that replays results in order and records check objects."""
    checks_seen: list[Check] = []
    idx = [0]

    def verifier(
        check: Check,
        *,
        journal: Journal,
        item_id: str,
        ctx: VerifyContext,
    ) -> VerificationResult:
        checks_seen.append(check)
        i = idx[0]
        idx[0] += 1
        if i < len(results):
            return results[i]
        return VerificationResult(
            subtask_id=ctx.subtask_id,
            attempt_no=ctx.attempt_no,
            verdict=Verdict.failed,
            kind=check.kind,
            evidence={},
            summary="still failing",
            evidence_path=None,
            checked_at=stamp(),
        )

    verifier.checks_seen = checks_seen  # type: ignore[attr-defined]
    return verifier


# ---------------------------------------------------------------------------
# AC7.1 — retried exactly max_repairs times, no more
# ---------------------------------------------------------------------------


def test_repair_retried_exactly_max_repairs_times(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    """Budget=2: a subtask that keeps failing is retried exactly 2 times."""
    subtask = _make_subtask()
    item = _make_item()
    first_result = _failing_result(subtask.subtask_id)

    runner = _make_fake_runner()
    verifier = _make_fake_verify(
        [
            _failing_result(subtask.subtask_id, attempt_no=1),
            _failing_result(subtask.subtask_id, attempt_no=2),
        ]
    )

    with pytest.raises(RepairExhausted):
        repair(
            subtask,
            first_result=first_result,
            item=item,
            prior_evidence=[],
            config=cfg,
            journal=journal,
            cwd=tmp_path,
            run_dir=tmp_path,
            _run_agent=runner,
            _run_verify=verifier,
        )

    assert len(runner.calls) == cfg.max_repairs  # type: ignore[attr-defined]
    assert len(verifier.checks_seen) == cfg.max_repairs  # type: ignore[attr-defined]


def test_repair_stops_on_first_pass(journal: Journal, tmp_path: Path) -> None:
    """If the first repair attempt passes, no further repairs are attempted."""
    cfg = Config(max_repairs=3)
    subtask = _make_subtask()
    item = _make_item()
    first_result = _failing_result(subtask.subtask_id)

    runner = _make_fake_runner()
    verifier = _make_fake_verify([_passing_result(subtask.subtask_id)])

    result = repair(
        subtask,
        first_result=first_result,
        item=item,
        prior_evidence=[],
        config=cfg,
        journal=journal,
        cwd=tmp_path,
        run_dir=tmp_path,
        _run_agent=runner,
        _run_verify=verifier,
    )

    assert result.verdict == Verdict.passed
    assert len(runner.calls) == 1  # type: ignore[attr-defined]


def test_no_repair_when_first_result_passed(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    """No repair attempt if first_result is already passed."""
    subtask = _make_subtask()
    item = _make_item()
    first_result = _passing_result(subtask.subtask_id, attempt_no=0)

    runner = _make_fake_runner()
    verifier = _make_fake_verify([])

    result = repair(
        subtask,
        first_result=first_result,
        item=item,
        prior_evidence=[],
        config=cfg,
        journal=journal,
        cwd=tmp_path,
        run_dir=tmp_path,
        _run_agent=runner,
        _run_verify=verifier,
    )

    assert result.verdict == Verdict.passed
    assert len(runner.calls) == 0  # type: ignore[attr-defined]
    assert len(verifier.checks_seen) == 0  # type: ignore[attr-defined]


def test_repair_stops_on_second_pass_not_third(journal: Journal, tmp_path: Path) -> None:
    """Second repair passes; third is never attempted even when budget allows it."""
    cfg = Config(max_repairs=3)
    subtask = _make_subtask()
    item = _make_item()
    first_result = _failing_result(subtask.subtask_id)

    runner = _make_fake_runner()
    verifier = _make_fake_verify(
        [
            _failing_result(subtask.subtask_id, attempt_no=1),
            _passing_result(subtask.subtask_id, attempt_no=2),
        ]
    )

    result = repair(
        subtask,
        first_result=first_result,
        item=item,
        prior_evidence=[],
        config=cfg,
        journal=journal,
        cwd=tmp_path,
        run_dir=tmp_path,
        _run_agent=runner,
        _run_verify=verifier,
    )

    assert result.verdict == Verdict.passed
    assert len(runner.calls) == 2  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# AC7.2 — repair prompt contains the failing check and its evidence
# ---------------------------------------------------------------------------


def test_repair_prompt_contains_check_statement(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    """AC7.2 — the repair prompt includes the check statement."""
    subtask = _make_subtask(check=_make_check("Must exit zero"))
    item = _make_item()
    first_result = _failing_result(subtask.subtask_id)

    runner = _make_fake_runner()
    verifier = _make_fake_verify([_passing_result(subtask.subtask_id)])

    repair(
        subtask,
        first_result=first_result,
        item=item,
        prior_evidence=[],
        config=cfg,
        journal=journal,
        cwd=tmp_path,
        run_dir=tmp_path,
        _run_agent=runner,
        _run_verify=verifier,
    )

    assert "Must exit zero" in runner.calls[0]  # type: ignore[attr-defined]


def test_repair_prompt_contains_failing_verdict(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    """AC7.2 — the repair prompt names the failing verdict."""
    subtask = _make_subtask()
    item = _make_item()
    first_result = _failing_result(subtask.subtask_id, Verdict.inconclusive)

    runner = _make_fake_runner()
    verifier = _make_fake_verify([_passing_result(subtask.subtask_id)])

    repair(
        subtask,
        first_result=first_result,
        item=item,
        prior_evidence=[],
        config=cfg,
        journal=journal,
        cwd=tmp_path,
        run_dir=tmp_path,
        _run_agent=runner,
        _run_verify=verifier,
    )

    assert "inconclusive" in runner.calls[0]  # type: ignore[attr-defined]


def test_repair_prompt_contains_failing_evidence(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    """AC7.2 — the repair prompt includes the failing evidence (e.g. stderr)."""
    subtask = _make_subtask()
    item = _make_item()
    first_result = _failing_result(subtask.subtask_id)
    # _failing_result sets stderr_head = "error message"

    runner = _make_fake_runner()
    verifier = _make_fake_verify([_passing_result(subtask.subtask_id)])

    repair(
        subtask,
        first_result=first_result,
        item=item,
        prior_evidence=[],
        config=cfg,
        journal=journal,
        cwd=tmp_path,
        run_dir=tmp_path,
        _run_agent=runner,
        _run_verify=verifier,
    )

    assert "error message" in runner.calls[0]  # type: ignore[attr-defined]


def test_repair_started_journalled_with_check_and_evidence(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    """AC7.2 — REPAIR_STARTED journal entry carries the check statement and evidence."""
    subtask = _make_subtask()
    item = _make_item()
    first_result = _failing_result(subtask.subtask_id)

    runner = _make_fake_runner()
    verifier = _make_fake_verify([_passing_result(subtask.subtask_id)])

    repair(
        subtask,
        first_result=first_result,
        item=item,
        prior_evidence=[],
        config=cfg,
        journal=journal,
        cwd=tmp_path,
        run_dir=tmp_path,
        _run_agent=runner,
        _run_verify=verifier,
    )

    state = journal.replay()
    repair_entries = [e for e in state.entries if e["event"] == REPAIR_STARTED]
    assert len(repair_entries) == 1
    payload = repair_entries[0]["payload"]
    assert payload["check_statement"] == subtask.check.statement
    assert "evidence" in payload
    assert payload["previous_verdict"] == Verdict.failed.value


# ---------------------------------------------------------------------------
# AC7.3 — original check always re-run; no substitution possible
# ---------------------------------------------------------------------------


def test_original_check_always_used_for_verification(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    """AC7.3 — repair() passes the original subtask.check to the verifier every time."""
    original_check = _make_check("This is the original check")
    subtask = _make_subtask(check=original_check)
    item = _make_item()
    first_result = _failing_result(subtask.subtask_id)

    runner = _make_fake_runner()
    verifier = _make_fake_verify(
        [
            _failing_result(subtask.subtask_id, attempt_no=1),
            _failing_result(subtask.subtask_id, attempt_no=2),
        ]
    )

    with pytest.raises(RepairExhausted):
        repair(
            subtask,
            first_result=first_result,
            item=item,
            prior_evidence=[],
            config=cfg,
            journal=journal,
            cwd=tmp_path,
            run_dir=tmp_path,
            _run_agent=runner,
            _run_verify=verifier,
        )

    # Every verify call must have used the original check, never a different one.
    assert verifier.checks_seen, "expected at least one verify call"  # type: ignore[attr-defined]
    for seen_check in verifier.checks_seen:  # type: ignore[attr-defined]
        assert seen_check is original_check, (
            "repair() must always pass subtask.check to the verifier — "
            "a repair may never substitute a different check (AC7.3)"
        )


# ---------------------------------------------------------------------------
# AC7.4 — budget exhaustion: RepairExhausted raised; halt-on-fail support
# ---------------------------------------------------------------------------


def test_repair_exhausted_raises(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    """AC7.4 — budget exhaustion raises RepairExhausted."""
    subtask = _make_subtask()
    item = _make_item()
    first_result = _failing_result(subtask.subtask_id)

    runner = _make_fake_runner()
    verifier = _make_fake_verify(
        [
            _failing_result(subtask.subtask_id, attempt_no=1),
            _failing_result(subtask.subtask_id, attempt_no=2),
        ]
    )

    with pytest.raises(RepairExhausted):
        repair(
            subtask,
            first_result=first_result,
            item=item,
            prior_evidence=[],
            config=cfg,
            journal=journal,
            cwd=tmp_path,
            run_dir=tmp_path,
            _run_agent=runner,
            _run_verify=verifier,
        )


def test_repair_exhausted_carries_last_result(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    """AC7.4 — RepairExhausted.last_result reflects the final failing verdict."""
    subtask = _make_subtask()
    item = _make_item()
    first_result = _failing_result(subtask.subtask_id, Verdict.failed)

    runner = _make_fake_runner()
    # First repair: inconclusive. Second: failed.
    verifier = _make_fake_verify(
        [
            VerificationResult(
                subtask_id=subtask.subtask_id,
                attempt_no=1,
                verdict=Verdict.inconclusive,
                kind=CheckKind.command,
                evidence={},
                summary="inconclusive",
                evidence_path=None,
                checked_at=stamp(),
            ),
            _failing_result(subtask.subtask_id, attempt_no=2),
        ]
    )

    with pytest.raises(RepairExhausted) as exc_info:
        repair(
            subtask,
            first_result=first_result,
            item=item,
            prior_evidence=[],
            config=cfg,
            journal=journal,
            cwd=tmp_path,
            run_dir=tmp_path,
            _run_agent=runner,
            _run_verify=verifier,
        )

    assert exc_info.value.last_result.verdict == Verdict.failed


def test_repair_exhausted_halt_false_by_default(journal: Journal, tmp_path: Path) -> None:
    """AC7.4 — default config has halt_on_fail=False; RepairExhausted.halt is False."""
    cfg = Config(max_repairs=1, halt_on_fail=False)
    subtask = _make_subtask()
    item = _make_item()

    runner = _make_fake_runner()
    verifier = _make_fake_verify([_failing_result(subtask.subtask_id, attempt_no=1)])

    with pytest.raises(RepairExhausted) as exc_info:
        repair(
            subtask,
            first_result=_failing_result(subtask.subtask_id),
            item=item,
            prior_evidence=[],
            config=cfg,
            journal=journal,
            cwd=tmp_path,
            run_dir=tmp_path,
            _run_agent=runner,
            _run_verify=verifier,
        )

    assert exc_info.value.halt is False


def test_repair_exhausted_halt_true_when_configured(journal: Journal, tmp_path: Path) -> None:
    """AC7.4 — --halt-on-fail: RepairExhausted.halt is True."""
    cfg = Config(max_repairs=1, halt_on_fail=True)
    subtask = _make_subtask()
    item = _make_item()

    runner = _make_fake_runner()
    verifier = _make_fake_verify([_failing_result(subtask.subtask_id, attempt_no=1)])

    with pytest.raises(RepairExhausted) as exc_info:
        repair(
            subtask,
            first_result=_failing_result(subtask.subtask_id),
            item=item,
            prior_evidence=[],
            config=cfg,
            journal=journal,
            cwd=tmp_path,
            run_dir=tmp_path,
            _run_agent=runner,
            _run_verify=verifier,
        )

    assert exc_info.value.halt is True


# ---------------------------------------------------------------------------
# Inconclusive first result also triggers repair
# ---------------------------------------------------------------------------


def test_inconclusive_triggers_repair(journal: Journal, tmp_path: Path) -> None:
    """An inconclusive verdict (not just failed) also triggers repair."""
    cfg = Config(max_repairs=1)
    subtask = _make_subtask()
    item = _make_item()
    first_result = VerificationResult(
        subtask_id=subtask.subtask_id,
        attempt_no=0,
        verdict=Verdict.inconclusive,
        kind=CheckKind.command,
        evidence={"error": "binary_not_found"},
        summary="binary not found",
        evidence_path=None,
        checked_at=stamp(),
    )

    runner = _make_fake_runner()
    verifier = _make_fake_verify([_passing_result(subtask.subtask_id)])

    result = repair(
        subtask,
        first_result=first_result,
        item=item,
        prior_evidence=[],
        config=cfg,
        journal=journal,
        cwd=tmp_path,
        run_dir=tmp_path,
        _run_agent=runner,
        _run_verify=verifier,
    )

    assert result.verdict == Verdict.passed
    assert len(runner.calls) == 1  # type: ignore[attr-defined]


def test_inconclusive_exhaustion_last_result_preserved(journal: Journal, tmp_path: Path) -> None:
    """AC7.4 — budget exhaustion from inconclusive: last_result carries inconclusive."""
    cfg = Config(max_repairs=1)
    subtask = _make_subtask()
    item = _make_item()
    first_result = _failing_result(subtask.subtask_id)

    runner = _make_fake_runner()
    verifier = _make_fake_verify(
        [
            VerificationResult(
                subtask_id=subtask.subtask_id,
                attempt_no=1,
                verdict=Verdict.inconclusive,
                kind=CheckKind.command,
                evidence={"error": "binary_not_found"},
                summary="binary not found",
                evidence_path=None,
                checked_at=stamp(),
            )
        ]
    )

    with pytest.raises(RepairExhausted) as exc_info:
        repair(
            subtask,
            first_result=first_result,
            item=item,
            prior_evidence=[],
            config=cfg,
            journal=journal,
            cwd=tmp_path,
            run_dir=tmp_path,
            _run_agent=runner,
            _run_verify=verifier,
        )

    assert exc_info.value.last_result.verdict == Verdict.inconclusive


# ---------------------------------------------------------------------------
# Journal ordering — REPAIR_STARTED precedes ATTEMPT_STARTED for each repair
# ---------------------------------------------------------------------------


def test_repair_started_precedes_attempt_started_in_journal(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    """REPAIR_STARTED is journalled before ATTEMPT_STARTED for every repair."""
    subtask = _make_subtask()
    item = _make_item()
    first_result = _failing_result(subtask.subtask_id)

    runner = _make_fake_runner()
    verifier = _make_fake_verify(
        [
            _failing_result(subtask.subtask_id, attempt_no=1),
            _failing_result(subtask.subtask_id, attempt_no=2),
        ]
    )

    with pytest.raises(RepairExhausted):
        repair(
            subtask,
            first_result=first_result,
            item=item,
            prior_evidence=[],
            config=cfg,
            journal=journal,
            cwd=tmp_path,
            run_dir=tmp_path,
            _run_agent=runner,
            _run_verify=verifier,
        )

    state = journal.replay()
    events = [(e["event"], e["seq"]) for e in state.entries]
    repair_seqs = [seq for ev, seq in events if ev == REPAIR_STARTED]
    attempt_started_seqs = [seq for ev, seq in events if ev == ATTEMPT_STARTED]

    assert len(repair_seqs) == cfg.max_repairs, "expected one REPAIR_STARTED per repair"
    assert len(attempt_started_seqs) == cfg.max_repairs, "expected one ATTEMPT_STARTED per repair"
    for r_seq, a_seq in zip(repair_seqs, attempt_started_seqs, strict=True):
        assert r_seq < a_seq, "REPAIR_STARTED must precede its ATTEMPT_STARTED"


def test_repair_attempt_numbers_increment(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    """Each repair uses a strictly increasing attempt_no in the REPAIR_STARTED payload."""
    subtask = _make_subtask()
    item = _make_item()
    first_result = _failing_result(subtask.subtask_id)

    runner = _make_fake_runner()
    verifier = _make_fake_verify(
        [
            _failing_result(subtask.subtask_id, attempt_no=1),
            _failing_result(subtask.subtask_id, attempt_no=2),
        ]
    )

    with pytest.raises(RepairExhausted):
        repair(
            subtask,
            first_result=first_result,
            item=item,
            prior_evidence=[],
            config=cfg,
            journal=journal,
            cwd=tmp_path,
            run_dir=tmp_path,
            _run_agent=runner,
            _run_verify=verifier,
        )

    state = journal.replay()
    repair_entries = [e for e in state.entries if e["event"] == REPAIR_STARTED]
    attempt_nos = [e["payload"]["attempt_no"] for e in repair_entries]
    assert attempt_nos == list(range(1, cfg.max_repairs + 1))


# ---------------------------------------------------------------------------
# Repair prompt content — prior evidence, path, pattern, rationale
# ---------------------------------------------------------------------------


def test_repair_prompt_includes_prior_evidence(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    """The repair prompt lists previously verified subtasks when prior_evidence is set."""
    subtask = _make_subtask(index=1)
    item = _make_item()
    first_result = _failing_result(subtask.subtask_id)

    prior = _passing_result("item-01#0", attempt_no=0)
    runner = _make_fake_runner()
    verifier = _make_fake_verify([_passing_result(subtask.subtask_id)])

    repair(
        subtask,
        first_result=first_result,
        item=item,
        prior_evidence=[prior],
        config=cfg,
        journal=journal,
        cwd=tmp_path,
        run_dir=tmp_path,
        _run_agent=runner,
        _run_verify=verifier,
    )

    assert len(runner.calls) == 1  # type: ignore[attr-defined]
    prompt = runner.calls[0]  # type: ignore[attr-defined]
    assert "Verified prior subtask" in prompt or "item-01#0" in prompt


def test_repair_prompt_includes_check_path(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    """The repair prompt includes the check's path when set."""
    check = Check(
        kind=CheckKind.file,
        statement="File must exist",
        path="output.txt",
    )
    subtask = _make_subtask(check=check)
    item = _make_item()
    first_result = _failing_result(subtask.subtask_id)

    runner = _make_fake_runner()
    verifier = _make_fake_verify([_passing_result(subtask.subtask_id)])

    repair(
        subtask,
        first_result=first_result,
        item=item,
        prior_evidence=[],
        config=cfg,
        journal=journal,
        cwd=tmp_path,
        run_dir=tmp_path,
        _run_agent=runner,
        _run_verify=verifier,
    )

    prompt = runner.calls[0]  # type: ignore[attr-defined]
    assert "output.txt" in prompt


def test_repair_prompt_includes_check_pattern(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    """The repair prompt includes the check's pattern when set."""
    check = Check(
        kind=CheckKind.file,
        statement="File must contain pattern",
        path="output.txt",
        pattern="SUCCESS",
    )
    subtask = _make_subtask(check=check)
    item = _make_item()
    first_result = _failing_result(subtask.subtask_id)

    runner = _make_fake_runner()
    verifier = _make_fake_verify([_passing_result(subtask.subtask_id)])

    repair(
        subtask,
        first_result=first_result,
        item=item,
        prior_evidence=[],
        config=cfg,
        journal=journal,
        cwd=tmp_path,
        run_dir=tmp_path,
        _run_agent=runner,
        _run_verify=verifier,
    )

    prompt = runner.calls[0]  # type: ignore[attr-defined]
    assert "SUCCESS" in prompt


def test_repair_prompt_includes_check_rationale(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    """The repair prompt includes the check's rationale when set."""
    check = Check(
        kind=CheckKind.judge,
        statement="Output quality is acceptable",
        rationale="Human-readable check — mechanical check insufficient.",
    )
    subtask = _make_subtask(check=check)
    item = _make_item()
    first_result = _failing_result(subtask.subtask_id)

    runner = _make_fake_runner()
    verifier = _make_fake_verify([_passing_result(subtask.subtask_id)])

    repair(
        subtask,
        first_result=first_result,
        item=item,
        prior_evidence=[],
        config=cfg,
        journal=journal,
        cwd=tmp_path,
        run_dir=tmp_path,
        _run_agent=runner,
        _run_verify=verifier,
    )

    prompt = runner.calls[0]  # type: ignore[attr-defined]
    assert "Human-readable check" in prompt
