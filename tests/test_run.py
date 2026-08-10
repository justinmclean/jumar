# SPDX-License-Identifier: Apache-2.0
"""Tests for ``gsd run`` and the shared :func:`run_item` orchestration.

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

from getstuffdone import cli
from getstuffdone.harness import AgentResult

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


class _Args:
    """Minimal stand-in for the parsed argparse namespace."""

    def __init__(self, **kw: Any) -> None:
        self.todo: str | None = None
        self.dry_run = False
        self.approve = False
        self.non_interactive = True
        self.run_id: str | None = None
        self.runs_dir: str | None = None
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

    runs = list((workspace / "runs").iterdir())
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
    from getstuffdone.lock import Lock

    held = Lock(workspace / "todo.md")
    held.acquire(run_id="held-by-another-process")
    try:
        rc = cli._cmd_run(_Args(), _run_agent=honest_agent)
    finally:
        held.release()

    assert rc == 0
    assert not (workspace / "marker.txt").exists()


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


# ---------------------------------------------------------------------------
# run / resume share one orchestration
# ---------------------------------------------------------------------------


def test_resume_of_a_completed_run_never_reinvokes_the_agent(
    workspace: Path,
) -> None:
    """Verified subtasks are replayed from the journal, not redone (AC-S1)."""
    assert cli._cmd_run(_Args(), _run_agent=honest_agent) == 0
    run_id = next(iter((workspace / "runs").iterdir())).name

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
