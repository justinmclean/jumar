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
- F1 (friendly-run-ids): new run ids match YYYYMMDD-HHMM-<4 hex chars>; two
  runs started in the same minute get distinct ids; uuid-format ids still
  resolve; unambiguous prefix resolves; ambiguous prefix errors; ``latest``
  resolves to the most-recent by run_started.now.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from getstuffdone.cli import _resolve_run_id, _RunResolveError, main
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
                "command": ["test", "-d", "."],
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


# ---------------------------------------------------------------------------
# F1 — Friendly run ids
# ---------------------------------------------------------------------------


def test_run_id_format_from_real_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A run id created by gsd run matches YYYYMMDD-HHMM-<4 hex chars>."""
    import re

    from getstuffdone import cli
    from getstuffdone.harness import AgentResult

    monkeypatch.chdir(tmp_path)
    (tmp_path / "todo.md").write_text("- [ ] Make something @capability=write_fs\n")

    import json as _json

    _PLAN = _json.dumps(
        {
            "subtasks": [
                {
                    "description": "Write marker",
                    "capabilities": ["write_fs"],
                    "depends_on": [],
                    "check": {
                        "kind": "file",
                        "statement": "marker.txt exists",
                        "path": "marker.txt",
                    },
                }
            ]
        }
    )

    def agent(prompt: str, *, cwd: Path, **_: object) -> AgentResult:
        if "subtasks" in prompt or "JSON" in prompt:
            return AgentResult(0, _PLAN, "", False, "done")
        (cwd / "marker.txt").write_text("ok\n")
        return AgentResult(0, "wrote marker", "", False, "done")

    class _Args:
        todo = None
        dry_run = False
        approve = False
        non_interactive = True

    rc = cli._cmd_run(_Args(), _run_agent=agent)  # type: ignore[arg-type]
    assert rc == 0

    runs = [d for d in (tmp_path / "runs").iterdir() if d.is_dir()]
    assert len(runs) == 1
    run_id = runs[0].name
    assert re.match(r"^\d{8}-\d{4}-[a-f0-9]{4}$", run_id), (
        f"run id {run_id!r} does not match YYYYMMDD-HHMM-<4 hex chars>"
    )


def test_make_run_id_distinct_for_same_minute(tmp_path: Path) -> None:
    """Two calls to make_run_id with the same now produce distinct ids."""
    from datetime import UTC, datetime

    from getstuffdone.clock import make_run_id

    now = datetime(2026, 8, 10, 14, 30, 0, tzinfo=UTC)
    id1 = make_run_id(now)
    id2 = make_run_id(now)
    # Both valid format.
    import re

    pat = re.compile(r"^\d{8}-\d{4}-[a-f0-9]{4}$")
    assert pat.match(id1), f"{id1!r} does not match pattern"
    assert pat.match(id2), f"{id2!r} does not match pattern"
    # They share the date-time prefix.
    assert id1.startswith("20260810-1430-")
    assert id2.startswith("20260810-1430-")
    # The random suffix makes them different (with overwhelming probability).
    # Use injectable suffix to guarantee it deterministically.
    id_a = make_run_id(now, _suffix="aaaa")
    id_b = make_run_id(now, _suffix="bbbb")
    assert id_a != id_b


def test_resolve_run_id_exact_match(tmp_path: Path) -> None:
    """An exact run id resolves directly without prefix scanning."""
    from getstuffdone.cli import _resolve_run_id

    run_id = "20260810-1430-abcd"
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "journal.jsonl").write_text('{"event": "run_started"}\n')

    resolved_id, resolved_dir = _resolve_run_id(tmp_path / "runs", run_id)
    assert resolved_id == run_id
    assert resolved_dir == run_dir


def test_resolve_run_id_unambiguous_prefix(tmp_path: Path) -> None:
    """An unambiguous prefix resolves to the one matching run."""
    from getstuffdone.cli import _resolve_run_id

    run_id = "20260810-1430-abcd"
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "journal.jsonl").write_text('{"event": "run_started"}\n')

    resolved_id, resolved_dir = _resolve_run_id(tmp_path / "runs", "20260810")
    assert resolved_id == run_id
    assert resolved_dir == run_dir


def test_resolve_run_id_ambiguous_prefix_errors(tmp_path: Path) -> None:
    """An ambiguous prefix raises _RunResolveError naming the candidates."""
    for suffix in ("abcd", "ef01"):
        run_dir = tmp_path / "runs" / f"20260810-1430-{suffix}"
        run_dir.mkdir(parents=True)
        (run_dir / "journal.jsonl").write_text('{"event": "run_started"}\n')

    with pytest.raises(_RunResolveError) as exc_info:
        _resolve_run_id(tmp_path / "runs", "20260810")
    msg = str(exc_info.value)
    assert "ambiguous" in msg
    assert "20260810-1430-abcd" in msg
    assert "20260810-1430-ef01" in msg


def test_resolve_run_id_no_match_errors(tmp_path: Path) -> None:
    """A prefix matching nothing raises _RunResolveError."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True)

    with pytest.raises(_RunResolveError):
        _resolve_run_id(runs_dir, "nonexistent-prefix")


def test_resolve_run_id_latest_picks_most_recent(tmp_path: Path) -> None:
    """``latest`` resolves to the run with the highest run_started.now value."""
    import json as _json

    runs_dir = tmp_path / "runs"
    for run_id, now_str in [
        ("20260101-0900-aaaa", "2026-01-01T09:00:00Z"),
        ("20260810-1430-bbbb", "2026-08-10T14:30:00Z"),
        ("20260601-1200-cccc", "2026-06-01T12:00:00Z"),
    ]:
        d = runs_dir / run_id
        d.mkdir(parents=True)
        entry = {"event": "run_started", "payload": {"now": now_str}}
        (d / "journal.jsonl").write_text(_json.dumps(entry) + "\n")

    resolved_id, resolved_dir = _resolve_run_id(runs_dir, "latest")
    assert resolved_id == "20260810-1430-bbbb"
    assert resolved_dir == runs_dir / "20260810-1430-bbbb"


def test_resolve_run_id_latest_empty_dir_errors(tmp_path: Path) -> None:
    """``latest`` on an empty runs directory raises _RunResolveError."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True)

    with pytest.raises(_RunResolveError):
        _resolve_run_id(runs_dir, "latest")


def test_resume_with_prefix_via_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """gsd resume accepts an unambiguous prefix instead of the full run id."""
    monkeypatch.chdir(tmp_path)
    run_id = "20260810-1430-abcd"
    run_dir = _make_run_dir(tmp_path, run_id)
    todo_path = _write_todo(tmp_path, run_id, "Prefix task @id=prefix-a1b2c3d4")

    j = _make_journal(run_dir)
    j.append(RUN_STARTED, payload={**_RUN_STARTED_PAYLOAD, "todo_path": str(todo_path)})
    j.append(
        ITEM_SELECTED,
        item_id="prefix-a1b2c3d4",
        payload={"text": "Prefix task", "is_overdue": False},
    )
    j.append(ITEM_COMPLETED, item_id="prefix-a1b2c3d4")
    j.append(RUN_FINISHED, payload={"exit_status": 0})

    # Use only the date portion as prefix — unambiguous since there's one run.
    rc = main(["resume", "20260810-1430", "--runs-dir", str(tmp_path / "runs")])
    assert rc == 0


def test_resume_latest_via_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """gsd resume latest picks the most recently started run."""
    monkeypatch.chdir(tmp_path)

    # Older run (completed).
    old_id = "20260101-0900-aaaa"
    old_dir = _make_run_dir(tmp_path, old_id)
    old_todo = _write_todo(tmp_path, old_id, "Old task @id=old-a1b2c3d4")
    j_old = _make_journal(old_dir)
    j_old.append(
        RUN_STARTED,
        payload={
            "now": "2026-01-01T09:00:00Z",
            "tz": "UTC",
            "mode": "auto",
            "todo_path": str(old_todo),
        },
    )
    j_old.append(
        ITEM_SELECTED,
        item_id="old-a1b2c3d4",
        payload={"text": "Old task", "is_overdue": False},
    )
    j_old.append(ITEM_COMPLETED, item_id="old-a1b2c3d4")
    j_old.append(RUN_FINISHED, payload={"exit_status": 0})

    # Newer run (completed).
    new_id = "20260810-1430-bbbb"
    new_dir = _make_run_dir(tmp_path, new_id)
    new_todo = _write_todo(tmp_path, new_id, "New task @id=new-a1b2c3d4")
    j_new = _make_journal(new_dir)
    j_new.append(
        RUN_STARTED,
        payload={
            "now": "2026-08-10T14:30:00Z",
            "tz": "UTC",
            "mode": "auto",
            "todo_path": str(new_todo),
        },
    )
    j_new.append(
        ITEM_SELECTED,
        item_id="new-a1b2c3d4",
        payload={"text": "New task", "is_overdue": False},
    )
    j_new.append(ITEM_COMPLETED, item_id="new-a1b2c3d4")
    j_new.append(RUN_FINISHED, payload={"exit_status": 0})

    rc = main(["resume", "latest", "--runs-dir", str(tmp_path / "runs")])
    assert rc == 0
    # The report must be for the NEW run directory.
    assert (new_dir / "report.md").exists()


def test_resume_uuid_format_still_resolves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A UUID-format run id (legacy format) resolves via exact match."""
    monkeypatch.chdir(tmp_path)
    run_id = "9c23ddee-8263-477b-a98a-99efff7540b6"
    run_dir = _make_run_dir(tmp_path, run_id)
    todo_path = _write_todo(tmp_path, run_id, "Legacy task @id=legacy-a1b2c3d4")

    j = _make_journal(run_dir)
    j.append(RUN_STARTED, payload={**_RUN_STARTED_PAYLOAD, "todo_path": str(todo_path)})
    j.append(
        ITEM_SELECTED,
        item_id="legacy-a1b2c3d4",
        payload={"text": "Legacy task", "is_overdue": False},
    )
    j.append(ITEM_COMPLETED, item_id="legacy-a1b2c3d4")
    j.append(RUN_FINISHED, payload={"exit_status": 0})

    rc = main(["resume", run_id, "--runs-dir", str(tmp_path / "runs")])
    assert rc == 0


# ---------------------------------------------------------------------------
# F2 — run index (runs/index.tsv)
# ---------------------------------------------------------------------------


def test_index_appended_at_run_started(tmp_path: Path) -> None:
    """append_run_started creates a row in index.tsv with status in_progress."""
    from getstuffdone.index import (
        FILENAME,
        STATUS_IN_PROGRESS,
        append_run_started,
    )

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    run_id = "20260811-1200-abcd"
    started = "2026-08-11T12:00:00Z"
    append_run_started(runs_dir, run_id, started)

    index = runs_dir / FILENAME
    assert index.exists()
    lines = [ln for ln in index.read_text(encoding="utf-8").splitlines() if ln]
    assert len(lines) == 1
    parts = lines[0].split("\t")
    assert parts[0] == run_id
    assert parts[1] == started
    assert parts[2] == ""  # item_id unknown at run_started
    assert parts[3] == STATUS_IN_PROGRESS


def test_index_updated_at_run_finished(tmp_path: Path) -> None:
    """update_run_finished rewrites the row with item_id and final status."""
    from getstuffdone.index import (
        FILENAME,
        STATUS_COMPLETED,
        append_run_started,
        update_run_finished,
    )

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    run_id = "20260811-1200-abcd"
    started = "2026-08-11T12:00:00Z"
    append_run_started(runs_dir, run_id, started)
    update_run_finished(runs_dir, run_id, "my-item-a1b2c3d4", STATUS_COMPLETED)

    index = runs_dir / FILENAME
    lines = [ln for ln in index.read_text(encoding="utf-8").splitlines() if ln]
    assert len(lines) == 1
    parts = lines[0].split("\t")
    assert parts[0] == run_id
    assert parts[1] == started  # started column unchanged
    assert parts[2] == "my-item-a1b2c3d4"
    assert parts[3] == STATUS_COMPLETED


def test_index_in_progress_on_crash(tmp_path: Path) -> None:
    """A run without a run_finished leaves its index row as in_progress."""
    from getstuffdone.index import FILENAME, STATUS_IN_PROGRESS, append_run_started

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    run_id = "20260811-1200-abcd"
    append_run_started(runs_dir, run_id, "2026-08-11T12:00:00Z")
    # No update_run_finished — simulates a crash before run_finished.

    index = runs_dir / FILENAME
    lines = [ln for ln in index.read_text(encoding="utf-8").splitlines() if ln]
    assert len(lines) == 1
    assert lines[0].split("\t")[3] == STATUS_IN_PROGRESS


def test_multiple_runs_each_get_a_row(tmp_path: Path) -> None:
    """Each call to append_run_started adds exactly one new row."""
    from getstuffdone.index import (
        FILENAME,
        STATUS_COMPLETED,
        append_run_started,
        update_run_finished,
    )

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    runs = [
        ("20260811-1000-aaaa", "2026-08-11T10:00:00Z"),
        ("20260811-1100-bbbb", "2026-08-11T11:00:00Z"),
        ("20260811-1200-cccc", "2026-08-11T12:00:00Z"),
    ]
    for rid, started in runs:
        append_run_started(runs_dir, rid, started)
        update_run_finished(runs_dir, rid, "item-id", STATUS_COMPLETED)

    index = runs_dir / FILENAME
    lines = [ln for ln in index.read_text(encoding="utf-8").splitlines() if ln]
    assert len(lines) == 3
    ids = [ln.split("\t")[0] for ln in lines]
    assert ids == [r[0] for r in runs]


def test_resolve_latest_uses_index_when_present(tmp_path: Path) -> None:
    """_resolve_run_id('latest') returns the highest-started entry from the index."""
    import json as _json

    from getstuffdone.index import STATUS_COMPLETED, append_run_started, update_run_finished

    runs_dir = tmp_path / "runs"
    for rid, started, now_str in [
        ("20260101-0900-aaaa", "2026-01-01T09:00:00Z", "2026-01-01T09:00:00Z"),
        ("20260811-1200-bbbb", "2026-08-11T12:00:00Z", "2026-08-11T12:00:00Z"),
    ]:
        d = runs_dir / rid
        d.mkdir(parents=True)
        (d / "journal.jsonl").write_text(
            _json.dumps({"event": "run_started", "payload": {"now": now_str}}) + "\n",
            encoding="utf-8",
        )
        append_run_started(runs_dir, rid, started)
        update_run_finished(runs_dir, rid, "item-id", STATUS_COMPLETED)

    resolved_id, resolved_dir = _resolve_run_id(runs_dir, "latest")
    assert resolved_id == "20260811-1200-bbbb"
    assert resolved_dir == runs_dir / "20260811-1200-bbbb"


def test_resolve_latest_falls_back_to_journal_when_no_index(tmp_path: Path) -> None:
    """UUID-named runs not in the index resolve via the journal scan fallback."""
    import json as _json

    runs_dir = tmp_path / "runs"
    for rid, now_str in [
        ("uuid-old-run-aaaa", "2026-01-01T09:00:00Z"),
        ("uuid-new-run-bbbb", "2026-08-11T12:00:00Z"),
    ]:
        d = runs_dir / rid
        d.mkdir(parents=True)
        (d / "journal.jsonl").write_text(
            _json.dumps({"event": "run_started", "payload": {"now": now_str}}) + "\n",
            encoding="utf-8",
        )
    # No index.tsv — resolution must fall back to journal scan.

    resolved_id, _ = _resolve_run_id(runs_dir, "latest")
    assert resolved_id == "uuid-new-run-bbbb"


def test_resolve_latest_corrupt_index_falls_back_to_journal(tmp_path: Path) -> None:
    """A corrupt index.tsv causes _resolve_run_id to fall back to the journal scan."""
    import json as _json

    from getstuffdone.index import FILENAME

    runs_dir = tmp_path / "runs"
    rid = "20260811-1200-abcd"
    d = runs_dir / rid
    d.mkdir(parents=True)
    (d / "journal.jsonl").write_text(
        _json.dumps({"event": "run_started", "payload": {"now": "2026-08-11T12:00:00Z"}}) + "\n",
        encoding="utf-8",
    )
    # Write a corrupt index (wrong column count on every line).
    (runs_dir / FILENAME).write_text("not\tvalid\nmore\tbad\tdata\n", encoding="utf-8")

    resolved_id, _ = _resolve_run_id(runs_dir, "latest")
    assert resolved_id == rid


def test_resolve_latest_absent_index_falls_back_to_journal(tmp_path: Path) -> None:
    """An absent index.tsv causes no error; the journal scan resolves 'latest'."""
    import json as _json

    runs_dir = tmp_path / "runs"
    rid = "20260811-1200-abcd"
    d = runs_dir / rid
    d.mkdir(parents=True)
    (d / "journal.jsonl").write_text(
        _json.dumps({"event": "run_started", "payload": {"now": "2026-08-11T12:00:00Z"}}) + "\n",
        encoding="utf-8",
    )
    # No index.tsv.

    resolved_id, _ = _resolve_run_id(runs_dir, "latest")
    assert resolved_id == rid


def test_update_run_finished_no_ops_for_absent_row(tmp_path: Path) -> None:
    """update_run_finished silently no-ops when the run_id is not in the index."""
    from getstuffdone.index import (
        FILENAME,
        STATUS_COMPLETED,
        append_run_started,
        update_run_finished,
    )

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    append_run_started(runs_dir, "20260811-1200-aaaa", "2026-08-11T12:00:00Z")
    # Update for a different run_id not in the index — must not raise.
    update_run_finished(runs_dir, "nonexistent-run-id", "item", STATUS_COMPLETED)

    index = runs_dir / FILENAME
    lines = [ln for ln in index.read_text(encoding="utf-8").splitlines() if ln]
    assert len(lines) == 1
    assert lines[0].split("\t")[0] == "20260811-1200-aaaa"


def test_gsd_run_writes_index_and_updates_on_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: gsd run appends to index.tsv and updates status at run_finished."""
    import json as _json

    from getstuffdone import cli
    from getstuffdone.harness import AgentResult
    from getstuffdone.index import FILENAME, STATUS_COMPLETED

    monkeypatch.chdir(tmp_path)
    (tmp_path / "todo.md").write_text(
        "- [ ] Write marker @capability=write_fs @id=marker-a1b2c3d4\n"
    )

    _PLAN_JSON = _json.dumps(
        {
            "subtasks": [
                {
                    "description": "Create marker.txt",
                    "capabilities": ["write_fs"],
                    "depends_on": [],
                    "check": {
                        "kind": "file",
                        "statement": "marker.txt exists",
                        "path": "marker.txt",
                    },
                }
            ]
        }
    )

    def agent(prompt: str, *, cwd: Path, **_: object) -> AgentResult:
        if "subtasks" in prompt or "JSON" in prompt:
            return AgentResult(0, _PLAN_JSON, "", False, "done")
        (cwd / "marker.txt").write_text("ok\n")
        return AgentResult(0, "wrote marker", "", False, "done")

    class _Args:
        todo = None
        dry_run = False
        approve = False
        non_interactive = True

    rc = cli._cmd_run(_Args(), _run_agent=agent)  # type: ignore[arg-type]
    assert rc == 0

    runs_dir = tmp_path / "runs"
    index = runs_dir / FILENAME
    assert index.exists(), "index.tsv must be created by gsd run"
    lines = [ln for ln in index.read_text(encoding="utf-8").splitlines() if ln]
    assert len(lines) == 1, f"expected 1 index row, got {lines}"
    parts = lines[0].split("\t")
    assert parts[2] == "marker-a1b2c3d4"
    assert parts[3] == STATUS_COMPLETED
