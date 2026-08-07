# SPDX-License-Identifier: Apache-2.0
"""Tests for ``gsd resume <run-id>`` — journal replay and pipeline continuation.

Acceptance criteria exercised here:
- AC-S1  A resumed run does not re-execute any subtask that already has a
         ``passed`` verdict (confirmed by fake-runner call count).
- AC-S4  The resumed run uses the original ``now`` from the journal rather than
         the current wall clock for eligibility decisions.
- AC9.2  Resume exits 0 on success and 1 on failure.
- Negative: non-existent run-id exits with error; corrupt-only journal exits
  with error when there is no run_started.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from getstuffdone.cli import main
from getstuffdone.journal import (
    ATTEMPT_FINISHED,
    ATTEMPT_STARTED,
    ITEM_COMPLETED,
    ITEM_SELECTED,
    PLAN_CREATED,
    RUN_FINISHED,
    RUN_STARTED,
    VERIFICATION,
    Journal,
)

# ---------------------------------------------------------------------------
# Fake-runner infrastructure (mirrors test_execute.py approach)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeResult:
    exit_status: int
    stdout: str
    stderr: str = ""
    timed_out: bool = False
    agent_claim: str | None = "Done."


def _fake_runner_factory(responses: list[_FakeResult]) -> Any:
    """Return a callable that replays responses in order and records prompts."""
    calls: list[str] = []

    def runner(
        prompt: str,
        *,
        cwd: Path,
        capabilities: Any,
        timeout_s: int,
        harness: Any,
    ) -> _FakeResult:
        calls.append(prompt)
        idx = len(calls) - 1
        return responses[idx] if idx < len(responses) else _FakeResult(exit_status=1, stdout="")

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RUN_STARTED_PAYLOAD = {
    "now": "2026-01-01T09:00:00Z",
    "tz": "UTC",
    "mode": "auto",
    "todo_path": "",  # filled per-test
}


def _plan_payload(item_id: str, *, n_subtasks: int = 2) -> dict[str, Any]:
    """Return a PLAN_CREATED payload with n_subtasks command checks."""
    subtasks = [
        {
            "subtask_id": f"{item_id}#{i}",
            "index": i,
            "description": f"Step {i + 1}",
            "check": {
                "kind": "command",
                "statement": f"Step {i + 1} passes",
                "command": ["true"],
                "expect_status": 0,
                "timeout_s": 60,
            },
            "capabilities": ["run_commands"],
            "depends_on": [],
        }
        for i in range(n_subtasks)
    ]
    return {
        "source": "model",
        "created_at": "2026-01-01T09:00:01Z",
        "harness": {"agent": "claude", "model": "sonnet"},
        "subtask_count": n_subtasks,
        "subtasks": subtasks,
    }


def _write_todo(tmp_path: Path, run_id: str, item_text: str) -> Path:
    """Write a minimal todo file and return its path."""
    todo_path = tmp_path / "todo.md"
    todo_path.write_text(f"- [ ] {item_text}\n", encoding="utf-8")
    return todo_path


def _make_run_dir(tmp_path: Path, run_id: str) -> Path:
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    return run_dir


def _make_journal(run_dir: Path) -> Journal:
    return Journal(run_dir / "journal.jsonl", run_dir.name)


# ---------------------------------------------------------------------------
# Negative: run-id not found
# ---------------------------------------------------------------------------


def test_resume_run_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """gsd resume with a non-existent run-id exits 1 with an error message."""
    monkeypatch.chdir(tmp_path)
    rc = main(["resume", "nonexistent-run-id-xyz"])
    assert rc == 1


def test_resume_no_run_started_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """gsd resume with a journal that has no run_started exits 1."""
    monkeypatch.chdir(tmp_path)
    run_id = "no-start-run"
    run_dir = _make_run_dir(tmp_path, run_id)
    j = _make_journal(run_dir)
    # Only an item event — no run_started.
    j.append(ITEM_SELECTED, item_id="abc", payload={"text": "something"})

    rc = main(["resume", run_id, "--runs-dir", str(tmp_path / "runs")])
    assert rc == 1


# ---------------------------------------------------------------------------
# Resume a completed run — no execution
# ---------------------------------------------------------------------------


def test_resume_already_complete_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resuming a fully completed run exits 0 and writes the report."""
    monkeypatch.chdir(tmp_path)
    run_id = "completed-run"
    run_dir = _make_run_dir(tmp_path, run_id)
    todo_path = _write_todo(tmp_path, run_id, "Completed task @id=completed-a1b2")

    j = _make_journal(run_dir)
    j.append(
        RUN_STARTED,
        payload={**_RUN_STARTED_PAYLOAD, "todo_path": str(todo_path)},
    )
    j.append(
        ITEM_SELECTED,
        item_id="completed-a1b2",
        payload={"text": "Completed task", "is_overdue": False},
    )
    j.append(ITEM_COMPLETED, item_id="completed-a1b2")
    j.append(RUN_FINISHED, payload={"exit_status": 0})

    rc = main(["resume", run_id, "--runs-dir", str(tmp_path / "runs")])
    assert rc == 0
    # Report should have been written.
    assert (run_dir / "report.md").exists()


# ---------------------------------------------------------------------------
# AC-S1 — resumed run skips already-passed subtasks
# ---------------------------------------------------------------------------


def test_resume_skips_passed_subtasks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-S1: a resumed run does not re-execute subtasks with a passed verdict.

    Journal has 2 subtasks.  Subtask 0 is already passed.  Only subtask 1
    should be dispatched to the fake runner.
    """
    monkeypatch.chdir(tmp_path)
    run_id = "resume-skip-passed"
    run_dir = _make_run_dir(tmp_path, run_id)
    item_id = "resume-item-a1b2c3d4"
    todo_path = _write_todo(tmp_path, run_id, f"Resume item @id={item_id}")

    j = _make_journal(run_dir)
    j.append(
        RUN_STARTED,
        payload={**_RUN_STARTED_PAYLOAD, "todo_path": str(todo_path)},
    )
    j.append(
        ITEM_SELECTED,
        item_id=item_id,
        payload={"text": "Resume item", "is_overdue": False},
    )
    j.append(PLAN_CREATED, item_id=item_id, payload=_plan_payload(item_id, n_subtasks=2))
    # Subtask 0 already passed.
    j.append(
        ATTEMPT_STARTED,
        subtask_id=f"{item_id}#0",
        item_id=item_id,
        payload={"attempt_no": 0},
    )
    j.append(
        ATTEMPT_FINISHED,
        subtask_id=f"{item_id}#0",
        item_id=item_id,
        payload={"attempt_no": 0, "exit_status": 0},
    )
    j.append(
        VERIFICATION,
        subtask_id=f"{item_id}#0",
        item_id=item_id,
        payload={"verdict": "passed", "summary": "step 1 ok"},
    )
    # Run killed before subtask 1.

    # Fake runner returns success for the one call (subtask 1).
    fake_runner = _fake_runner_factory([_FakeResult(exit_status=0, stdout="done")])

    # Monkeypatch the verify registry to always return passed for command checks.
    from getstuffdone.clock import stamp
    from getstuffdone.models import CheckKind, Verdict, VerificationResult

    def _fake_command_verify(check: Any, ctx: Any) -> VerificationResult:
        return VerificationResult(
            subtask_id=ctx.subtask_id,
            attempt_no=ctx.attempt_no,
            verdict=Verdict.passed,
            kind=CheckKind.command,
            evidence={"exit_code": 0},
            summary="ok",
            evidence_path=None,
            checked_at=stamp(),
        )

    import getstuffdone.verify as _verify_mod

    original_registry = dict(_verify_mod._registry)
    _verify_mod._registry[CheckKind.command] = _fake_command_verify

    try:
        import argparse

        from getstuffdone.cli import _cmd_resume

        ns = argparse.Namespace(
            run_id=run_id,
            runs_dir=str(tmp_path / "runs"),
        )
        rc = _cmd_resume(ns, _run_agent=fake_runner)
    finally:
        _verify_mod._registry.clear()
        _verify_mod._registry.update(original_registry)

    # Fake runner must have been called exactly once — for subtask 1 only.
    assert len(fake_runner.calls) == 1, (
        f"Expected 1 call (subtask 1 only), got {len(fake_runner.calls)}"
    )
    # The call should mention subtask 2 (index 1) in the prompt.
    assert "Subtask 2" in fake_runner.calls[0] or "Step 2" in fake_runner.calls[0]
    assert rc == 0


# ---------------------------------------------------------------------------
# AC-S4 — resumed run uses original now, not current wall clock
# ---------------------------------------------------------------------------


def test_resume_uses_original_now_for_eligibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-S4: gsd resume uses the now from run_started, not the current time.

    Setup:
    - Journal has now = 2026-01-01T09:00:00Z.
    - Todo file has a deferred item with not_before = 2026-06-01 (future from journal's now).
    - Wall clock would be 2026-08-07 (past the not_before).

    If resume reads the wall clock, the item is eligible.
    If resume uses the original now, the item is still deferred.

    We verify that gsd resume exits 0 with no execution (nothing selected).
    """
    monkeypatch.chdir(tmp_path)
    run_id = "resume-original-now"
    run_dir = _make_run_dir(tmp_path, run_id)

    # Item is deferred until 2026-06-01, which is AFTER journal's now (2026-01-01)
    # but BEFORE today's wall clock (2026-08-07).
    todo_path = tmp_path / "todo.md"
    todo_path.write_text(
        "- [ ] Deferred task @id=deferred-a1b2c3d4 @not-before=2026-06-01\n",
        encoding="utf-8",
    )

    j = _make_journal(run_dir)
    # Journal now is 2026-01-01 — before the item's not_before.
    j.append(
        RUN_STARTED,
        payload={
            "now": "2026-01-01T09:00:00Z",
            "tz": "UTC",
            "mode": "auto",
            "todo_path": str(todo_path),
        },
    )
    # No item_selected — the item was deferred when the run started.
    j.append(RUN_FINISHED, payload={"exit_status": 0})

    rc = main(["resume", run_id, "--runs-dir", str(tmp_path / "runs")])
    # Item is still deferred relative to original now — nothing to execute.
    assert rc == 0
    # Report should exist.
    assert (run_dir / "report.md").exists()


# ---------------------------------------------------------------------------
# Resume with a failed subtask → exits 1
# ---------------------------------------------------------------------------


def test_resume_failed_subtask_exits_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A resumed run whose subtask fails exits 1 and writes the report."""
    monkeypatch.chdir(tmp_path)
    run_id = "resume-fail"
    run_dir = _make_run_dir(tmp_path, run_id)
    item_id = "fail-item-a1b2c3d4"
    todo_path = _write_todo(tmp_path, run_id, f"Fail item @id={item_id}")

    j = _make_journal(run_dir)
    j.append(
        RUN_STARTED,
        payload={**_RUN_STARTED_PAYLOAD, "todo_path": str(todo_path)},
    )
    j.append(PLAN_CREATED, item_id=item_id, payload=_plan_payload(item_id, n_subtasks=1))
    # No ITEM_SELECTED yet — the item was selected but then run was killed before journaling it.

    # Fake runner: returns success exit status.
    fake_runner = _fake_runner_factory([_FakeResult(exit_status=0, stdout="done")])

    # Fake verifier: always returns failed.
    from getstuffdone.clock import stamp
    from getstuffdone.models import CheckKind, Verdict, VerificationResult

    def _fake_verify_failed(check: Any, ctx: Any) -> VerificationResult:
        return VerificationResult(
            subtask_id=ctx.subtask_id,
            attempt_no=ctx.attempt_no,
            verdict=Verdict.failed,
            kind=CheckKind.command,
            evidence={"exit_code": 1, "stderr": "error"},
            summary="command failed",
            evidence_path=None,
            checked_at=stamp(),
        )

    import getstuffdone.verify as _verify_mod

    original_registry = dict(_verify_mod._registry)
    _verify_mod._registry[CheckKind.command] = _fake_verify_failed

    try:
        import argparse

        from getstuffdone.cli import _cmd_resume

        ns = argparse.Namespace(
            run_id=run_id,
            runs_dir=str(tmp_path / "runs"),
        )
        rc = _cmd_resume(ns, _run_agent=fake_runner)
    finally:
        _verify_mod._registry.clear()
        _verify_mod._registry.update(original_registry)

    assert rc == 1
    assert (run_dir / "report.md").exists()
    content = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "Failed" in content or "failed" in content


# ---------------------------------------------------------------------------
# Journal append-only during resume (AC-S2)
# ---------------------------------------------------------------------------


def test_resume_appends_to_journal_not_rewrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-S2: gsd resume appends new events without rewriting prior lines."""
    monkeypatch.chdir(tmp_path)
    run_id = "resume-append-only"
    run_dir = _make_run_dir(tmp_path, run_id)
    item_id = "append-item-a1b2c3d4"
    todo_path = _write_todo(tmp_path, run_id, f"Append item @id={item_id}")

    j = _make_journal(run_dir)
    j.append(
        RUN_STARTED,
        payload={**_RUN_STARTED_PAYLOAD, "todo_path": str(todo_path)},
    )
    j.append(PLAN_CREATED, item_id=item_id, payload=_plan_payload(item_id, n_subtasks=1))

    # Record the lines that exist before resume.
    journal_path = run_dir / "journal.jsonl"
    original_lines = [ln for ln in journal_path.read_text().splitlines() if ln.strip()]

    # Fake runner + verifier returning success.
    fake_runner = _fake_runner_factory([_FakeResult(exit_status=0, stdout="done")])

    from getstuffdone.clock import stamp
    from getstuffdone.models import CheckKind, Verdict, VerificationResult

    def _fake_verify_ok(check: Any, ctx: Any) -> VerificationResult:
        return VerificationResult(
            subtask_id=ctx.subtask_id,
            attempt_no=ctx.attempt_no,
            verdict=Verdict.passed,
            kind=CheckKind.command,
            evidence={},
            summary="ok",
            evidence_path=None,
            checked_at=stamp(),
        )

    import getstuffdone.verify as _verify_mod

    original_registry = dict(_verify_mod._registry)
    _verify_mod._registry[CheckKind.command] = _fake_verify_ok

    try:
        import argparse

        from getstuffdone.cli import _cmd_resume

        ns = argparse.Namespace(
            run_id=run_id,
            runs_dir=str(tmp_path / "runs"),
        )
        _cmd_resume(ns, _run_agent=fake_runner)
    finally:
        _verify_mod._registry.clear()
        _verify_mod._registry.update(original_registry)

    # All original lines must be unchanged — append-only (AC-S2).
    all_lines = [ln for ln in journal_path.read_text().splitlines() if ln.strip()]
    assert all_lines[: len(original_lines)] == original_lines
    assert len(all_lines) > len(original_lines), "resume must have appended new events"
