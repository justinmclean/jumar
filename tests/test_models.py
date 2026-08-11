# SPDX-License-Identifier: Apache-2.0
"""Tests for models.py — shapes, enumerations, and Check invariants."""

from __future__ import annotations

import pytest

from jumar.models import (
    Attempt,
    Capability,
    Check,
    CheckKind,
    FailureCode,
    HarnessInfo,
    ItemResult,
    ItemStatus,
    Plan,
    Recurrence,
    RecurUnit,
    Run,
    Schedule,
    ScheduleBackend,
    Subtask,
    SubtaskStatus,
    TodoItem,
    Verdict,
    VerificationResult,
)

# ---------------------------------------------------------------------------
# Enumeration membership
# ---------------------------------------------------------------------------


def test_item_status_members() -> None:
    members = {e.value for e in ItemStatus}
    assert members == {
        "pending",
        "in_progress",
        "done",
        "failed",
        "blocked",
        "skipped_by_human",
        "inconclusive",
        "deferred",
    }


def test_subtask_status_members() -> None:
    members = {e.value for e in SubtaskStatus}
    assert members == {"pending", "running", "passed", "failed", "inconclusive", "skipped"}


def test_check_kind_members() -> None:
    members = {e.value for e in CheckKind}
    assert members == {"command", "file", "absence", "judge", "manual"}


def test_verdict_members() -> None:
    members = {e.value for e in Verdict}
    assert members == {"passed", "failed", "inconclusive"}


def test_capability_members() -> None:
    members = {e.value for e in Capability}
    assert members == {"read_fs", "write_fs", "run_commands", "network", "git_commit"}


def test_failure_code_members() -> None:
    members = {e.value for e in FailureCode}
    assert members == {
        "unverifiable_plan",
        "plan_too_long",
        "invalid_plan",
        "capability_denied",
        "timed_out",
        "check_failed",
        "repairs_exhausted",
        "harness_error",
        "dependency_blocked",
        "bad_schedule",
        "already_running",
    }


# ---------------------------------------------------------------------------
# Check invariants — negative cases (one per invariant)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    ["", "n/a", "none", "TODO", "verify manually"],
)
def test_check_rejects_placeholder_statement(statement: str) -> None:
    with pytest.raises(ValueError, match="placeholder"):
        Check(kind=CheckKind.manual, statement=statement)


def test_check_command_requires_non_none_command() -> None:
    with pytest.raises(ValueError, match="kind=command"):
        Check(kind=CheckKind.command, statement="The command exits zero")


def test_check_command_rejects_empty_tuple() -> None:
    with pytest.raises(ValueError, match="kind=command"):
        Check(kind=CheckKind.command, statement="The command exits zero", command=())


def test_check_file_requires_non_none_path() -> None:
    with pytest.raises(ValueError, match="kind=file"):
        Check(kind=CheckKind.file, statement="The output file exists")


def test_check_file_rejects_empty_path() -> None:
    with pytest.raises(ValueError, match="kind=file"):
        Check(kind=CheckKind.file, statement="The output file exists", path="")


def test_check_absence_requires_non_none_path() -> None:
    with pytest.raises(ValueError, match="kind=absence"):
        Check(kind=CheckKind.absence, statement="The temp file is gone")


def test_check_absence_rejects_empty_path() -> None:
    with pytest.raises(ValueError, match="kind=absence"):
        Check(kind=CheckKind.absence, statement="The temp file is gone", path="")


def test_check_judge_requires_non_none_rationale() -> None:
    with pytest.raises(ValueError, match="kind=judge"):
        Check(kind=CheckKind.judge, statement="An agent confirms correctness")


def test_check_judge_rejects_empty_rationale() -> None:
    with pytest.raises(ValueError, match="kind=judge"):
        Check(
            kind=CheckKind.judge,
            statement="An agent confirms correctness",
            rationale="",
        )


# ---------------------------------------------------------------------------
# Check invariants — positive cases
# ---------------------------------------------------------------------------


def test_check_command_valid() -> None:
    c = Check(
        kind=CheckKind.command,
        statement="The test suite passes",
        command=("pytest", "-q"),
    )
    assert c.command == ("pytest", "-q")
    assert c.expect_status == 0
    assert c.timeout_s == 300


def test_check_file_valid() -> None:
    c = Check(
        kind=CheckKind.file,
        statement="The output file exists and contains data",
        path="output/result.json",
    )
    assert c.path == "output/result.json"


def test_check_absence_valid() -> None:
    c = Check(
        kind=CheckKind.absence,
        statement="The temporary file has been removed",
        path="tmp/work.tmp",
    )
    assert c.path == "tmp/work.tmp"


def test_check_judge_valid() -> None:
    c = Check(
        kind=CheckKind.judge,
        statement="The generated prose is coherent and on-topic",
        rationale="No deterministic executable check can verify subjective prose quality",
    )
    assert c.rationale


def test_check_manual_valid() -> None:
    c = Check(
        kind=CheckKind.manual,
        statement="The UI renders correctly in the browser",
    )
    assert c.kind is CheckKind.manual


def test_check_is_frozen() -> None:
    c = Check(
        kind=CheckKind.command,
        statement="Tests pass",
        command=("pytest",),
    )
    with pytest.raises((AttributeError, TypeError)):
        c.statement = "mutated"  # type: ignore[misc]


def test_check_command_custom_expect_status() -> None:
    c = Check(
        kind=CheckKind.command,
        statement="The linter reports no issues",
        command=("ruff", "check", "src"),
        expect_status=0,
        timeout_s=60,
    )
    assert c.timeout_s == 60


# ---------------------------------------------------------------------------
# HarnessInfo
# ---------------------------------------------------------------------------


def test_harness_info_construction() -> None:
    h = HarnessInfo(agent="claude", model="sonnet", harness="v1", invoked_as="claude")
    assert h.agent == "claude"
    assert h.model == "sonnet"


def test_harness_info_is_frozen() -> None:
    h = HarnessInfo(agent="claude", model="sonnet", harness="v1", invoked_as="claude")
    with pytest.raises((AttributeError, TypeError)):
        h.agent = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TodoItem
# ---------------------------------------------------------------------------


def _make_todo_item(**overrides: object) -> TodoItem:
    defaults: dict[str, object] = {
        "item_id": "write-tests-a1b2c3d4",
        "text": "Write tests",
        "raw_line": "- [ ] Write tests\n",
        "line_no": 3,
        "status": ItemStatus.pending,
        "context": ("## Testing",),
        "authored_subtasks": (),
        "meta": {},
        "priority": None,
        "depends": (),
        "capabilities": frozenset({Capability.read_fs}),
    }
    defaults.update(overrides)
    return TodoItem(**defaults)  # type: ignore[arg-type]


def test_todo_item_construction() -> None:
    item = _make_todo_item()
    assert item.item_id == "write-tests-a1b2c3d4"
    assert item.status is ItemStatus.pending
    assert item.priority is None
    assert item.depends == ()


def test_todo_item_done_status() -> None:
    item = _make_todo_item(status=ItemStatus.done, raw_line="- [x] Write tests\n")
    assert item.status is ItemStatus.done


def test_todo_item_with_meta() -> None:
    item = _make_todo_item(
        meta={"priority": "1", "depends": "other-item"},
        priority=1,
        depends=("other-item",),
    )
    assert item.meta["priority"] == "1"
    assert item.depends == ("other-item",)


# ---------------------------------------------------------------------------
# Attempt
# ---------------------------------------------------------------------------


def _make_harness() -> HarnessInfo:
    return HarnessInfo(agent="claude", model="sonnet", harness="v1", invoked_as="claude")


def _make_attempt(**overrides: object) -> Attempt:
    defaults: dict[str, object] = {
        "attempt_no": 0,
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:01:00Z",
        "harness": _make_harness(),
        "agent_claim": "I finished the task.",
        "transcript_path": "runs/abc/artifacts/subtask-0-attempt-0.txt",
        "exit_status": 0,
        "files_touched": ("src/foo.py",),
        "error": None,
    }
    defaults.update(overrides)
    return Attempt(**defaults)  # type: ignore[arg-type]


def test_attempt_construction() -> None:
    a = _make_attempt()
    assert a.attempt_no == 0
    assert a.exit_status == 0
    assert a.error is None


def test_attempt_with_error() -> None:
    a = _make_attempt(agent_claim=None, exit_status=None, error="harness crashed")
    assert a.error == "harness crashed"
    assert a.agent_claim is None


# ---------------------------------------------------------------------------
# VerificationResult
# ---------------------------------------------------------------------------


def test_verification_result_construction() -> None:
    vr = VerificationResult(
        subtask_id="item-1#0",
        attempt_no=0,
        verdict=Verdict.passed,
        kind=CheckKind.command,
        evidence={"exit_status": 0, "stdout_head": "ok"},
        summary="pytest exited 0",
        evidence_path=None,
        checked_at="2026-01-01T00:01:05Z",
    )
    assert vr.verdict is Verdict.passed
    assert vr.evidence["exit_status"] == 0


def test_verification_result_failed_verdict() -> None:
    vr = VerificationResult(
        subtask_id="item-1#0",
        attempt_no=0,
        verdict=Verdict.failed,
        kind=CheckKind.command,
        evidence={"exit_status": 1, "stderr_head": "assertion error"},
        summary="pytest exited 1",
        evidence_path="runs/abc/artifacts/verify-0.txt",
        checked_at="2026-01-01T00:01:05Z",
    )
    assert vr.verdict is Verdict.failed


# ---------------------------------------------------------------------------
# Subtask (requires a Check — structural guarantee)
# ---------------------------------------------------------------------------


def _make_check() -> Check:
    return Check(
        kind=CheckKind.command,
        statement="The test suite passes",
        command=("pytest", "-q"),
    )


def test_subtask_construction() -> None:
    st = Subtask(
        subtask_id="item-1#0",
        index=0,
        description="Install dependencies",
        check=_make_check(),
        capabilities=frozenset({Capability.run_commands}),
        depends_on=(),
        status=SubtaskStatus.pending,
        attempts=(),
    )
    assert st.index == 0
    assert isinstance(st.check, Check)


def test_subtask_with_attempt() -> None:
    st = Subtask(
        subtask_id="item-1#0",
        index=0,
        description="Run tests",
        check=_make_check(),
        capabilities=frozenset(),
        depends_on=(),
        status=SubtaskStatus.passed,
        attempts=(_make_attempt(),),
    )
    assert len(st.attempts) == 1
    assert st.status is SubtaskStatus.passed


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


def test_plan_construction() -> None:
    check = _make_check()
    subtask = Subtask(
        subtask_id="item-1#0",
        index=0,
        description="Run tests",
        check=check,
        capabilities=frozenset(),
        depends_on=(),
        status=SubtaskStatus.pending,
        attempts=(),
    )
    plan = Plan(
        item_id="item-1",
        subtasks=(subtask,),
        source="model",
        created_at="2026-01-01T00:00:00Z",
        harness=_make_harness(),
    )
    assert len(plan.subtasks) == 1
    assert plan.source == "model"


# ---------------------------------------------------------------------------
# ItemResult and Run
# ---------------------------------------------------------------------------


def test_item_result_construction() -> None:
    ir = ItemResult(
        item_id="item-1",
        status=ItemStatus.done,
        plan=None,
        failure_code=None,
        failed_subtask_index=None,
        verifications=(),
    )
    assert ir.status is ItemStatus.done
    assert ir.failure_code is None


def test_item_result_failure() -> None:
    ir = ItemResult(
        item_id="item-2",
        status=ItemStatus.failed,
        plan=None,
        failure_code=FailureCode.unverifiable_plan,
        failed_subtask_index=0,
        verifications=(),
    )
    assert ir.failure_code is FailureCode.unverifiable_plan


def test_run_construction() -> None:
    run = Run(
        run_id="20260101T000000Z-abc123",
        todo_path="todo.md",
        mode="auto",
        config={"max_subtasks": 12},
        started_at="2026-01-01T00:00:00Z",
        finished_at=None,
        items=(),
        warnings=(),
    )
    assert run.mode == "auto"
    assert run.finished_at is None
    assert run.warnings == ()


# ---------------------------------------------------------------------------
# RecurUnit, ScheduleBackend
# ---------------------------------------------------------------------------


def test_recur_unit_members() -> None:
    assert {e.value for e in RecurUnit} == {"day", "week", "month", "weekday", "dow"}


def test_schedule_backend_members() -> None:
    assert {e.value for e in ScheduleBackend} == {"cron", "launchd", "systemd"}


# ---------------------------------------------------------------------------
# Recurrence invariants
# ---------------------------------------------------------------------------


def test_recurrence_valid_daily() -> None:
    r = Recurrence(unit=RecurUnit.day, interval=1, days=())
    assert r.interval == 1
    assert r.days == ()


def test_recurrence_valid_weekly() -> None:
    r = Recurrence(unit=RecurUnit.week, interval=2, days=())
    assert r.unit is RecurUnit.week
    assert r.interval == 2


def test_recurrence_valid_weekday() -> None:
    r = Recurrence(unit=RecurUnit.weekday, interval=1, days=())
    assert r.unit is RecurUnit.weekday


def test_recurrence_valid_dow() -> None:
    r = Recurrence(unit=RecurUnit.dow, interval=1, days=("mon", "thu"))
    assert r.days == ("mon", "thu")


def test_recurrence_rejects_interval_zero() -> None:
    with pytest.raises(ValueError, match="interval"):
        Recurrence(unit=RecurUnit.day, interval=0, days=())


def test_recurrence_rejects_interval_negative() -> None:
    with pytest.raises(ValueError, match="interval"):
        Recurrence(unit=RecurUnit.day, interval=-1, days=())


def test_recurrence_dow_requires_non_empty_days() -> None:
    with pytest.raises(ValueError, match="days"):
        Recurrence(unit=RecurUnit.dow, interval=1, days=())


def test_recurrence_is_frozen() -> None:
    r = Recurrence(unit=RecurUnit.day, interval=1, days=())
    with pytest.raises((AttributeError, TypeError)):
        r.interval = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Schedule invariants
# ---------------------------------------------------------------------------


def test_schedule_valid_not_before() -> None:
    s = Schedule(
        not_before="2026-08-11T00:00:00+00:00",
        not_before_literal="2026-08-11",
        due=None,
        due_literal=None,
        recur=None,
        tz="UTC",
    )
    assert s.not_before == "2026-08-11T00:00:00+00:00"
    assert s.not_before_literal == "2026-08-11"


def test_schedule_valid_due() -> None:
    s = Schedule(
        not_before=None,
        not_before_literal=None,
        due="2026-08-14T00:00:00+00:00",
        due_literal="2026-08-14",
        recur=None,
        tz="UTC",
    )
    assert s.due == "2026-08-14T00:00:00+00:00"


def test_schedule_valid_with_recurrence() -> None:
    recur = Recurrence(unit=RecurUnit.weekday, interval=1, days=())
    s = Schedule(
        not_before=None,
        not_before_literal=None,
        due=None,
        due_literal=None,
        recur=recur,
        tz="Australia/Sydney",
    )
    assert s.recur is not None
    assert s.recur.unit is RecurUnit.weekday


def test_schedule_rejects_naive_not_before() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Schedule(
            not_before="2026-08-11T00:00:00",
            not_before_literal="2026-08-11",
            due=None,
            due_literal=None,
            recur=None,
            tz="UTC",
        )


def test_schedule_rejects_naive_due() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Schedule(
            not_before=None,
            not_before_literal=None,
            due="2026-08-14T00:00:00",
            due_literal="2026-08-14",
            recur=None,
            tz="UTC",
        )


def test_schedule_rejects_invalid_not_before_string() -> None:
    with pytest.raises(ValueError):
        Schedule(
            not_before="not-a-date",
            not_before_literal="not-a-date",
            due=None,
            due_literal=None,
            recur=None,
            tz="UTC",
        )


def test_schedule_is_frozen() -> None:
    s = Schedule(
        not_before=None,
        not_before_literal=None,
        due=None,
        due_literal=None,
        recur=None,
        tz="UTC",
    )
    with pytest.raises((AttributeError, TypeError)):
        s.tz = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TodoItem with schedule field
# ---------------------------------------------------------------------------


def test_todo_item_default_schedule_is_none() -> None:
    item = _make_todo_item()
    assert item.schedule is None


def test_todo_item_with_schedule() -> None:
    schedule = Schedule(
        not_before="2026-08-11T00:00:00+00:00",
        not_before_literal="2026-08-11",
        due=None,
        due_literal=None,
        recur=None,
        tz="UTC",
    )
    item = _make_todo_item(schedule=schedule)
    assert item.schedule is not None
    assert item.schedule.not_before == "2026-08-11T00:00:00+00:00"
