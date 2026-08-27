# SPDX-License-Identifier: Apache-2.0
"""Tests for ``jumar run`` and the shared :func:`run_item` orchestration.

``run`` and ``resume`` share one code path (``run_item``) so the two commands
cannot drift apart. These tests drive the real pipeline — ingest, select,
decompose, gate, execute, verify, repair, complete — with a fake agent, so the
composition itself is under test rather than any single stage.

The negative paths are the point: an agent that *claims* success while doing
nothing must not produce a ticked checkbox.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jumar import cli
from jumar.harness import AgentResult

# ---------------------------------------------------------------------------
# Fake agents
# ---------------------------------------------------------------------------


def _result(stdout: str, *, claim: str = "done") -> AgentResult:
    return AgentResult(exit_status=0, stdout=stdout, stderr="", timed_out=False, agent_claim=claim)


_PLAN = json.dumps(
    {
        "subtasks": [
            {
                "description": "Write marker.txt containing OK",
                "capabilities": ["write_fs"],
                "depends_on": [],
                "check": {
                    "kind": "file",
                    "statement": "marker.txt exists and contains OK",
                    "path": "marker.txt",
                    "pattern": "OK",
                },
            }
        ]
    }
)


def _is_decompose_prompt(prompt: str) -> bool:
    return "JSON" in prompt or "subtasks" in prompt


def honest_agent(prompt: str, *, cwd: Path, **_: Any) -> AgentResult:
    """Plans one subtask, then actually performs it."""
    if _is_decompose_prompt(prompt):
        return _result(_PLAN)
    (Path(cwd) / "marker.txt").write_text("OK\n")
    return _result("wrote marker.txt")


def lying_agent(prompt: str, *, cwd: Path, **_: Any) -> AgentResult:
    """Plans one subtask, then claims success while writing nothing."""
    if _is_decompose_prompt(prompt):
        return _result(_PLAN)
    return _result("All done!", claim="All done!")


def timeout_agent(prompt: str, *, cwd: Path, **_: Any) -> AgentResult:
    """Plans one subtask, then times out on every execution attempt.

    Keys off the plan request's exact opening, not ``_is_decompose_prompt``'s
    "subtasks" substring match — the execution prompt itself contains the word
    "subtasks" ("Do not proceed to subsequent subtasks"), which would
    misroute every execution attempt back to the plan response.
    """
    if prompt.startswith("Decompose the following todo item"):
        return _result(_PLAN)
    return AgentResult(exit_status=-1, stdout="", stderr="", timed_out=True, agent_claim=None)


def transport_error_agent(prompt: str, *, cwd: Path, **_: Any) -> AgentResult:
    """Plans one subtask, then fails before the execution harness can run it."""
    transport_error_agent.calls += 1
    if prompt.startswith("Decompose the following todo item"):
        return _result(_PLAN)
    return AgentResult(
        exit_status=-1,
        stdout="",
        stderr="request to http://192.168.1.8:1234/v1 failed: timed out",
        timed_out=False,
        agent_claim=None,
    )


transport_error_agent.calls = 0


class _Args:
    """Minimal stand-in for the parsed argparse namespace."""

    def __init__(self, **kw: Any) -> None:
        self.todo: str | None = None
        self.dry_run = False
        self.approve = False
        self.non_interactive = True
        self.run_id: str | None = None
        self.runs_dir: str | None = None
        self.retry_failed = False
        self.__dict__.update(kw)


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A directory with a one-item todo file, as the current working directory."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "todo.md").write_text(
        "- [ ] Create the marker file @capability=write_fs\n- [ ] Second item\n"
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_run_completes_item_and_ticks_only_that_checkbox(workspace: Path) -> None:
    """A verified item is marked done; sibling items are untouched (AC8.1, AC8.2)."""
    rc = cli._cmd_run(_Args(), _run_agent=honest_agent)

    assert rc == 0
    assert (workspace / "marker.txt").read_text().strip() == "OK"
    assert workspace.joinpath("todo.md").read_text() == (
        "- [x] Create the marker file @capability=write_fs\n- [ ] Second item\n"
    )


def test_run_writes_a_journal_and_report(workspace: Path) -> None:
    """Every run leaves an auditable journal and report."""
    assert cli._cmd_run(_Args(), _run_agent=honest_agent) == 0

    runs = [d for d in (workspace / "runs").iterdir() if d.is_dir()]
    assert len(runs) == 1
    assert (runs[0] / "journal.jsonl").exists()
    assert (runs[0] / "report.md").exists()


# ---------------------------------------------------------------------------
# Negative paths — the product is a refusal machine
# ---------------------------------------------------------------------------


def test_run_does_not_tick_the_box_when_the_agent_only_claims_success(
    workspace: Path,
) -> None:
    """An unverified claim must never produce a completed item (AC5.5, AC8.1)."""
    rc = cli._cmd_run(_Args(), _run_agent=lying_agent)

    assert rc == 1
    assert not (workspace / "marker.txt").exists()
    assert "- [ ] Create the marker file" in (workspace / "todo.md").read_text()


def test_run_exhausts_repairs_before_failing(
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A failing check routes to bounded repair, then fails the item (AC7.1, AC7.4)."""
    rc = cli._cmd_run(_Args(), _run_agent=lying_agent)
    out = capsys.readouterr().out

    assert rc == 1
    assert "repairs_exhausted" in out


def test_run_a_timed_out_attempt_does_not_advance_the_item(workspace: Path) -> None:
    """A timed-out attempt is recorded, and the item never advances past it (AC5.2)."""
    rc = cli._cmd_run(_Args(), _run_agent=timeout_agent)

    assert rc == 1
    assert not (workspace / "marker.txt").exists()
    assert "- [ ] Create the marker file" in (workspace / "todo.md").read_text()

    run_dir = next(d for d in (workspace / "runs").iterdir() if d.is_dir())
    lines = [
        json.loads(ln) for ln in (run_dir / "journal.jsonl").read_text().splitlines() if ln.strip()
    ]
    finished = [ln for ln in lines if ln["event"] == "attempt_finished"]
    assert finished and all(ln["payload"].get("timed_out") for ln in finished)
    assert not any(ln["event"] == "item_completed" for ln in lines)


def test_run_harness_transport_error_fails_without_repairs(workspace: Path) -> None:
    """A harness outage is infrastructure failure, not a repairable bad subtask."""
    transport_error_agent.calls = 0

    rc = cli._cmd_run(_Args(), _run_agent=transport_error_agent)

    assert rc == 1
    assert transport_error_agent.calls == 2  # decompose once, execute once, no repair
    assert not (workspace / "marker.txt").exists()

    run_dir = next(d for d in (workspace / "runs").iterdir() if d.is_dir())
    lines = [
        json.loads(ln) for ln in (run_dir / "journal.jsonl").read_text().splitlines() if ln.strip()
    ]
    assert any(
        ln["event"] == "harness_error" and ln["payload"]["reason"] == "transport" for ln in lines
    )
    failed = next(ln for ln in lines if ln["event"] == "item_failed")
    assert failed["payload"]["failure_code"] == "harness_error"
    assert not any(ln["event"] == "repair_started" for ln in lines)


def test_run_dry_run_executes_nothing(workspace: Path) -> None:
    """``--dry-run`` prints the plan and stops before execution (AC4.1)."""
    rc = cli._cmd_run(_Args(dry_run=True), _run_agent=honest_agent)

    assert rc == 0
    assert not (workspace / "marker.txt").exists()
    assert "- [ ] Create the marker file" in (workspace / "todo.md").read_text()


def test_run_refuses_approve_with_non_interactive(workspace: Path) -> None:
    """The one flag pair with no safe resolution is refused at startup (AC4.4)."""
    rc = cli._cmd_run(_Args(approve=True, non_interactive=True), _run_agent=honest_agent)
    assert rc == 2


def test_run_exits_zero_when_another_run_holds_the_lock(workspace: Path) -> None:
    """A live lock is `already_running`, not a failure (AC10.5)."""
    from jumar.lock import Lock

    held = Lock(workspace / "todo.md")
    held.acquire(run_id="held-by-another-process")
    try:
        rc = cli._cmd_run(_Args(), _run_agent=honest_agent)
    finally:
        held.release()

    assert rc == 0
    assert not (workspace / "marker.txt").exists()


def _dead_pid() -> int:
    """Return a PID that is guaranteed not to be running."""
    import os

    for candidate in range(99_999, 1, -1):
        try:
            os.kill(candidate, 0)
        except ProcessLookupError:
            return candidate
        except PermissionError:
            continue  # process alive — try the next one
    pytest.skip("could not find a dead PID for stale-lock test")


def test_run_proceeds_after_reclaiming_a_stale_lock(workspace: Path) -> None:
    """A stale lock (dead PID) is reclaimed and a real run proceeds (AC10.6)."""
    lock_path = workspace / ".jumar.lock"
    lock_path.write_text(
        json.dumps({"pid": _dead_pid(), "run_id": "dead-run", "todo": str(workspace / "todo.md")})
    )

    rc = cli._cmd_run(_Args(), _run_agent=honest_agent)

    assert rc == 0
    assert (workspace / "marker.txt").read_text().strip() == "OK"
    assert workspace.joinpath("todo.md").read_text().startswith("- [x]")

    run_dir = next(d for d in (workspace / "runs").iterdir() if d.is_dir())
    lines = [
        json.loads(ln) for ln in (run_dir / "journal.jsonl").read_text().splitlines() if ln.strip()
    ]
    assert any(ln["event"] == "lock_reclaimed" for ln in lines)


def test_run_exits_zero_when_nothing_is_eligible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A run with nothing to do is a success and must not page anyone (AC2.10)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "todo.md").write_text("- [ ] Later @not-before=2099-01-01\n")

    rc = cli._cmd_run(_Args(), _run_agent=honest_agent)
    out = capsys.readouterr().out

    assert rc == 0
    assert "Nothing eligible" in out


def test_run_journals_item_deferred_for_a_not_before_gated_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A @not-before= gated item is journalled as item_deferred, not silently dropped."""
    from jumar.journal import ITEM_DEFERRED

    monkeypatch.chdir(tmp_path)
    (tmp_path / "todo.md").write_text("- [ ] Later @not-before=2099-01-01\n")

    rc = cli._cmd_run(_Args(), _run_agent=honest_agent)
    out = capsys.readouterr().out
    assert rc == 0
    next_eligible = out.split("Next eligible: ")[1].splitlines()[0]

    run_dir = next(d for d in (tmp_path / "runs").iterdir() if d.is_dir())
    lines = [
        json.loads(ln) for ln in (run_dir / "journal.jsonl").read_text().splitlines() if ln.strip()
    ]
    deferred_entries = [ln for ln in lines if ln["event"] == ITEM_DEFERRED]

    assert len(deferred_entries) == 1
    entry = deferred_entries[0]
    assert entry["item_id"]
    assert entry["payload"]["eligible_at"] == next_eligible
    assert entry["payload"]["eligible_at"] in entry["payload"]["reason"]


# ---------------------------------------------------------------------------
# run / resume share one orchestration
# ---------------------------------------------------------------------------


def test_resume_of_a_completed_run_never_reinvokes_the_agent(
    workspace: Path,
) -> None:
    """Verified subtasks are replayed from the journal, not redone (AC-S1)."""
    assert cli._cmd_run(_Args(), _run_agent=honest_agent) == 0
    run_id = next(d for d in (workspace / "runs").iterdir() if d.is_dir()).name

    def exploding_agent(*_a: Any, **_kw: Any) -> AgentResult:
        raise AssertionError("resume re-invoked the agent for an already-passed subtask")

    rc = cli._cmd_resume(_Args(run_id=run_id), _run_agent=exploding_agent)
    assert rc == 0


# ---------------------------------------------------------------------------
# The judge verifier must be reachable from a real run (M2)
# ---------------------------------------------------------------------------

_JUDGE_PLAN = json.dumps(
    {
        "subtasks": [
            {
                "description": "Write notes.md",
                "capabilities": ["write_fs"],
                "depends_on": [],
                "check": {
                    "kind": "judge",
                    "statement": "notes.md reads as a coherent summary",
                    "rationale": "no executable check can assess prose quality",
                },
            }
        ]
    }
)


def test_a_judge_check_reaches_the_judge_with_a_harness(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression: `run_item` built VerifyContext without `harness=`, so every
    judge check returned `no_harness_configured` — inconclusive — burned the
    repair budget and failed the item. The verifier passed its own unit tests
    the whole time because those build a context with a harness by hand.
    """
    seen: list[str] = []

    def agent(prompt: str, *, cwd: Path, **_: Any) -> AgentResult:
        # Route on the prompt's opening, not on keywords: an *execution* prompt
        # for a judge-checked subtask also contains the word "judge", and a
        # repair prompt opens differently again.
        if prompt.startswith("You are a strict"):
            seen.append(prompt)
            return _result(
                '{"verdict": "pass", "reason": "notes.md is a coherent summary", '
                '"artefacts_shown": ["notes.md"]}'
            )
        if _is_decompose_prompt(prompt):
            return _result(_JUDGE_PLAN)
        (Path(cwd) / "notes.md").write_text("a summary\n")
        return _result("wrote notes.md")

    rc = cli._cmd_run(_Args(), _run_agent=agent)
    err = capsys.readouterr().err

    assert "no_harness_configured" not in err
    assert seen, "the judge verifier was never reached with a usable harness"
    assert rc == 0
    # The judge ran in a fresh context: it never saw the executing agent's
    # reasoning, only the world it left behind (AC6.4).
    assert "wrote notes.md" not in seen[0]


# ---------------------------------------------------------------------------
# A failed item can be retried without redoing the work that passed (R1)
# ---------------------------------------------------------------------------

_TWO_STEP_PLAN = json.dumps(
    {
        "subtasks": [
            {
                "description": "Write a.txt",
                "capabilities": ["write_fs"],
                "depends_on": [],
                "check": {
                    "kind": "file",
                    "statement": "a.txt contains A",
                    "path": "a.txt",
                    "pattern": "A",
                },
            },
            {
                "description": "Write b.txt",
                "capabilities": ["write_fs"],
                "depends_on": [],
                "check": {
                    "kind": "file",
                    "statement": "b.txt contains B",
                    "path": "b.txt",
                    "pattern": "B",
                },
            },
        ]
    }
)


def _is_plan_request(prompt: str) -> bool:
    """Only the plan request opens this way.

    ``_is_decompose_prompt`` keys on the word "subtasks", which also appears in
    an *execution* prompt ("Do not proceed to subsequent subtasks") — fine for
    agents that write the file either way, wrong for one asserting it was never
    asked to decompose.
    """
    return prompt.startswith("Decompose the following todo item")


def _first_passes_second_fails(prompt: str, *, cwd: Path, **_: Any) -> AgentResult:
    if _is_plan_request(prompt):
        return _result(_TWO_STEP_PLAN)
    if "a.txt" in prompt:
        (Path(cwd) / "a.txt").write_text("A\n")
        return _result("wrote a.txt")
    return _result("all done!", claim="all done!")  # writes nothing


def test_retry_failed_reruns_only_the_unverified_subtask(workspace: Path) -> None:
    """The point of the flag: fix the cause, redo the failure, keep the rest.

    Before this, a failed item could not be resumed at all — `_cmd_resume`
    short-circuited on any terminal state and reprinted the old report, so a
    run that failed because of a bug in jumar had to be redone in full once the
    bug was fixed.
    """
    assert cli._cmd_run(_Args(), _run_agent=_first_passes_second_fails) == 1
    run_id = next(d for d in (workspace / "runs").iterdir() if d.is_dir()).name

    calls: list[str] = []

    def fixes_the_second(prompt: str, *, cwd: Path, **_: Any) -> AgentResult:
        if _is_plan_request(prompt):
            raise AssertionError("retry re-decomposed instead of replaying the plan")
        calls.append(prompt)
        (Path(cwd) / "b.txt").write_text("B\n")
        return _result("wrote b.txt")

    rc = cli._cmd_resume(_Args(run_id=run_id, retry_failed=True), _run_agent=fixes_the_second)

    assert rc == 0
    assert len(calls) == 1, "the passed subtask was re-executed"
    assert all("a.txt" not in c for c in calls)
    assert workspace.joinpath("todo.md").read_text().startswith("- [x]")


def test_retry_failed_does_not_double_count_the_failure(workspace: Path) -> None:
    """@failed= reflects the original failure, then is cleared by the retry's
    success — never bumped a second time for the same failure (AC-S5)."""
    assert cli._cmd_run(_Args(), _run_agent=_first_passes_second_fails) == 1
    run_id = next(d for d in (workspace / "runs").iterdir() if d.is_dir()).name

    todo_after_failure = workspace.joinpath("todo.md").read_text()
    assert "@failed=1" in todo_after_failure

    def fixes_the_second(prompt: str, *, cwd: Path, **_: Any) -> AgentResult:
        if _is_plan_request(prompt):
            raise AssertionError("retry re-decomposed instead of replaying the plan")
        (Path(cwd) / "b.txt").write_text("B\n")
        return _result("wrote b.txt")

    rc = cli._cmd_resume(_Args(run_id=run_id, retry_failed=True), _run_agent=fixes_the_second)

    assert rc == 0
    todo_after_retry = workspace.joinpath("todo.md").read_text()
    assert "@failed=" not in todo_after_retry
    assert "@failed=2" not in todo_after_retry


def test_retry_failed_clears_the_stale_failure_from_the_report(workspace: Path) -> None:
    """Status is last-write-wins; the failure detail must not outlive it."""
    assert cli._cmd_run(_Args(), _run_agent=_first_passes_second_fails) == 1
    run_id = next(d for d in (workspace / "runs").iterdir() if d.is_dir()).name

    def fixes_it(prompt: str, *, cwd: Path, **_: Any) -> AgentResult:
        if _is_plan_request(prompt):
            return _result(_TWO_STEP_PLAN)
        (Path(cwd) / "b.txt").write_text("B\n")
        return _result("wrote b.txt")

    cli._cmd_resume(_Args(run_id=run_id, retry_failed=True), _run_agent=fixes_it)
    report = (workspace / "runs" / run_id / "report.md").read_text()

    assert "repairs_exhausted" not in report


def test_without_the_flag_a_failed_item_still_only_reports(workspace: Path) -> None:
    """The default must stay idempotent — resuming twice is not two attempts."""
    assert cli._cmd_run(_Args(), _run_agent=_first_passes_second_fails) == 1
    run_id = next(d for d in (workspace / "runs").iterdir() if d.is_dir()).name

    def exploding(*_a: Any, **_kw: Any) -> AgentResult:
        raise AssertionError("resume without --retry-failed re-invoked the agent")

    assert cli._cmd_resume(_Args(run_id=run_id), _run_agent=exploding) == 1


def test_retry_failed_never_reopens_a_completed_item(workspace: Path) -> None:
    """Done is done. The flag reopens failures, not successes."""
    assert cli._cmd_run(_Args(), _run_agent=honest_agent) == 0
    run_id = next(d for d in (workspace / "runs").iterdir() if d.is_dir()).name

    def exploding(*_a: Any, **_kw: Any) -> AgentResult:
        raise AssertionError("a completed item was reopened by --retry-failed")

    assert cli._cmd_resume(_Args(run_id=run_id, retry_failed=True), _run_agent=exploding) == 0


def test_retry_failed_does_not_reopen_an_item_completed_on_a_prior_retry(
    workspace: Path,
) -> None:
    """An item that failed then succeeded on retry must not be reopenable again.

    After a successful retry the journal holds both item_failed and
    item_completed for the same item.  The naive reopenable set (items_failed)
    would include it; the correct set is items_failed - items_done.
    """
    # First run: second subtask fails.
    assert cli._cmd_run(_Args(), _run_agent=_first_passes_second_fails) == 1
    run_id = next(d for d in (workspace / "runs").iterdir() if d.is_dir()).name

    def fixes_the_second(prompt: str, *, cwd: Path, **_: Any) -> AgentResult:
        if _is_plan_request(prompt):
            raise AssertionError("retry re-decomposed instead of replaying the plan")
        (Path(cwd) / "b.txt").write_text("B\n")
        return _result("wrote b.txt")

    # First retry: succeeds.
    rc = cli._cmd_resume(_Args(run_id=run_id, retry_failed=True), _run_agent=fixes_the_second)
    assert rc == 0

    def exploding(*_a: Any, **_kw: Any) -> AgentResult:
        raise AssertionError("a completed item was reopened by a second --retry-failed")

    # Second retry attempt: must NOT re-execute — item is already done.
    assert cli._cmd_resume(_Args(run_id=run_id, retry_failed=True), _run_agent=exploding) == 0
