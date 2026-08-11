# SPDX-License-Identifier: Apache-2.0
"""Tests for run progress output (stderr) and its suppression rules.

``gsd run`` was silent between the gate and the final summary, which on a long
item is indistinguishable from a hung process. These tests pin the two rules
that make progress safe to add:

* it goes to **stderr**, so stdout stays clean for the report and ``--json``;
* it is **suppressed** under ``--json`` and when stderr is not a TTY, so
  scheduled runs stay quiet.

Progress is forced on via ``_progress_force`` where the output is under test:
pytest's capture replaces stderr with a non-TTY, so without the override every
assertion here would pass vacuously.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from getstuffdone import cli
from getstuffdone.harness import AgentResult
from getstuffdone.progress import Progress

_PLAN = json.dumps(
    {
        "subtasks": [
            {
                "description": "Write marker.txt containing OK",
                "capabilities": ["write_fs"],
                "depends_on": [],
                "check": {
                    "kind": "file",
                    "statement": "marker.txt exists",
                    "path": "marker.txt",
                    "pattern": "OK",
                },
            },
            {
                "description": "Write second.txt containing OK",
                "capabilities": ["write_fs"],
                "depends_on": [],
                "check": {
                    "kind": "file",
                    "statement": "second.txt exists",
                    "path": "second.txt",
                    "pattern": "OK",
                },
            },
        ]
    }
)

_AGENT_CHATTER = "I wrote the file\nand said a second thing"

# One subtask whose check cannot pass until the agent writes the right content.
_REPAIR_PLAN = json.dumps(
    {
        "subtasks": [
            {
                "description": "Write marker.txt containing OK",
                "capabilities": ["write_fs"],
                "depends_on": [],
                "check": {
                    "kind": "file",
                    "statement": "marker.txt contains OK",
                    "path": "marker.txt",
                    "pattern": "OK",
                },
            }
        ]
    }
)


def _result(stdout: str) -> AgentResult:
    return AgentResult(exit_status=0, stdout=stdout, stderr="", timed_out=False, agent_claim="done")


def _is_execution(prompt: str) -> bool:
    """Execution prompts open with this sentence; decomposition prompts do not."""
    return prompt.startswith("You are executing")


def _is_decomposition(prompt: str) -> bool:
    """Only the plan request asks for subtasks as JSON."""
    return prompt.startswith("Decompose the following todo item")


def honest_agent(prompt: str, *, cwd: Path, **_: Any) -> AgentResult:
    if not _is_execution(prompt):
        return _result(_PLAN)
    # Key off the subtask header, not the filename: subtask 2's prompt carries
    # subtask 1's evidence, which mentions marker.txt.
    name = "second.txt" if "Subtask 2" in prompt else "marker.txt"
    (Path(cwd) / name).write_text("OK\n")
    return _result(_AGENT_CHATTER)


def _agent_that_needs_one_repair() -> Any:
    """Writes the wrong content first, the right content on the first repair."""
    state = {"executions": 0}

    def agent(prompt: str, *, cwd: Path, **_: Any) -> AgentResult:
        # A repair prompt is neither a decomposition nor the standard execution
        # preamble — it opens with the failing verdict — so key off the plan
        # request and treat everything else as work.
        if _is_decomposition(prompt):
            return _result(_REPAIR_PLAN)
        state["executions"] += 1
        text = "WRONG\n" if state["executions"] == 1 else "OK\n"
        (Path(cwd) / "marker.txt").write_text(text)
        return _result(_AGENT_CHATTER)

    return agent


def never_writes_the_pattern(prompt: str, *, cwd: Path, **_: Any) -> AgentResult:
    """Always writes content the check cannot match — exhausts the budget."""
    if _is_decomposition(prompt):
        return _result(_REPAIR_PLAN)
    (Path(cwd) / "marker.txt").write_text("WRONG\n")
    return _result(_AGENT_CHATTER)


class _Args:
    def __init__(self, **kw: Any) -> None:
        self.todo: str | None = None
        self.dry_run = False
        self.approve = False
        self.non_interactive = True
        self.verbose = False
        self.json = False
        self.__dict__.update(kw)


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "todo.md").write_text("- [ ] Do the thing @capability=write_fs\n")
    return tmp_path


# ---------------------------------------------------------------------------
# Progress is emitted, on stderr only
# ---------------------------------------------------------------------------


def test_progress_lines_go_to_stderr_and_never_stdout(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli._cmd_run(_Args(), _run_agent=honest_agent, _progress_force=True)
    captured = capsys.readouterr()

    assert rc == 0
    assert "[1/2]" in captured.err
    assert "[2/2]" in captured.err
    assert "[1/2]" not in captured.out
    assert "[2/2]" not in captured.out


def test_progress_names_each_subtask_and_its_verdict(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli._cmd_run(_Args(), _run_agent=honest_agent, _progress_force=True)
    err = capsys.readouterr().err

    assert "Write marker.txt containing OK" in err
    assert "Write second.txt containing OK" in err
    assert "verifying" in err
    assert "passed" in err


# ---------------------------------------------------------------------------
# Suppression — the part that keeps scheduled runs quiet
# ---------------------------------------------------------------------------


def test_json_suppresses_progress(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """--json owns stdout; progress must not appear anywhere."""
    cli._cmd_run(_Args(json=True), _run_agent=honest_agent, _progress_force=None)
    captured = capsys.readouterr()

    assert "[1/2]" not in captured.err
    assert "[1/2]" not in captured.out


def test_non_tty_suppresses_progress(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A piped or scheduled run emits no progress chatter."""
    cli._cmd_run(_Args(), _run_agent=honest_agent, _progress_force=None)
    captured = capsys.readouterr()

    assert "[1/2]" not in captured.err
    assert "[1/2]" not in captured.out


# ---------------------------------------------------------------------------
# Verbose
# ---------------------------------------------------------------------------


def test_verbose_echoes_agent_output(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli._cmd_run(_Args(verbose=True), _run_agent=honest_agent, _progress_force=True)
    err = capsys.readouterr().err

    assert "I wrote the file" in err


def test_without_verbose_agent_output_is_not_echoed(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli._cmd_run(_Args(), _run_agent=honest_agent, _progress_force=True)
    err = capsys.readouterr().err

    assert "I wrote the file" not in err


# ---------------------------------------------------------------------------
# The reporter itself
# ---------------------------------------------------------------------------


def test_disabled_progress_writes_nothing(tmp_path: Path) -> None:
    out = tmp_path / "sink.txt"
    with out.open("w") as fh:
        p = Progress(enabled=False, stream=fh)
        p.item_started("x", 2)
        p.subtask_started(0, 2, "y")
        p.verifying("file")
        p.verdict("passed")
        p.item_finished("done")
    assert out.read_text() == ""


def test_for_run_disabled_when_json_even_on_a_tty(tmp_path: Path) -> None:
    class _FakeTTY:
        def isatty(self) -> bool:
            return True

        def write(self, _s: str) -> int:
            return 0

        def flush(self) -> None:
            return None

    assert Progress.for_run(json_output=True, stream=_FakeTTY()).enabled is False
    assert Progress.for_run(json_output=False, stream=_FakeTTY()).enabled is True


def test_missing_transcript_never_raises(tmp_path: Path) -> None:
    """Progress output must not be able to fail a run."""
    with (tmp_path / "sink.txt").open("w") as fh:
        p = Progress(enabled=True, verbose=True, stream=fh)
        p.agent_transcript(str(tmp_path / "does-not-exist.txt"))
        p.agent_transcript(None)


# ---------------------------------------------------------------------------
# A failing check must say so, and say why, before repair starts
# ---------------------------------------------------------------------------


def test_a_failing_check_reports_the_reason_before_repairing(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The failure and its cause are printed at the moment they happen.

    Before this, the terminal went straight from ``verifying (file) …`` to
    ``repairing …`` — never saying the check failed, and never saying why. On a
    long run that reads like a routine step rather than a problem.
    """
    cli._cmd_run(_Args(), _run_agent=_agent_that_needs_one_repair(), _progress_force=True)
    err = capsys.readouterr().err

    failed_at = err.index("failed")
    repairing_at = err.index("repairing")
    assert failed_at < repairing_at, "the failure must be reported before repair opens"
    assert "does not match pattern" in err


def test_each_repair_attempt_is_numbered_against_the_budget(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``attempt 1/2`` beats ``up to 2``: it shows progress through the budget."""
    rc = cli._cmd_run(_Args(), _run_agent=_agent_that_needs_one_repair(), _progress_force=True)
    err = capsys.readouterr().err

    assert rc == 0
    assert "attempt 1/2" in err


def test_a_repaired_subtask_does_not_print_its_verdict_twice(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli._cmd_run(_Args(), _run_agent=_agent_that_needs_one_repair(), _progress_force=True)
    err = capsys.readouterr().err

    assert err.count("passed") == 1


def test_every_attempt_is_shown_when_the_budget_is_exhausted(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli._cmd_run(_Args(), _run_agent=never_writes_the_pattern, _progress_force=True)
    err = capsys.readouterr().err

    assert rc == 1
    assert "attempt 1/2" in err
    assert "attempt 2/2" in err
    assert "repairs exhausted" in err


def test_repair_progress_is_suppressed_when_disabled(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The repair callbacks must respect the enable rule like everything else."""
    cli._cmd_run(_Args(json=True), _run_agent=_agent_that_needs_one_repair())
    err = capsys.readouterr().err

    assert "attempt" not in err
    assert "repairing" not in err


# ---------------------------------------------------------------------------
# The planning phase must not be silent
# ---------------------------------------------------------------------------


def test_planning_is_announced_before_the_agent_is_called(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Decomposition is one agent call and used to print nothing.

    `prog.item_started` needs a subtask count, which does not exist until the
    plan does — so the longest single wait in a run happened before the first
    line of output. On a real item that was eight minutes of blank terminal.
    """
    cli._cmd_run(_Args(), _run_agent=honest_agent, _progress_force=True)
    err = capsys.readouterr().err

    assert "planning" in err
    assert err.index("planning") < err.index("[1/")


def test_a_rejected_plan_says_so_and_says_it_is_retrying(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A retry silently doubles the wait. It must be visible."""
    state = {"n": 0}
    unfalsifiable = json.dumps(
        {
            "subtasks": [
                {
                    "description": "Do it",
                    "capabilities": ["write_fs"],
                    "depends_on": [],
                    "check": {
                        "kind": "command",
                        "statement": "it is done",
                        "command": ["true"],
                    },
                }
            ]
        }
    )

    def rejected_once(prompt: str, *, cwd: Path, **_: Any) -> AgentResult:
        if _is_decomposition(prompt):
            state["n"] += 1
            return _result(unfalsifiable if state["n"] == 1 else _PLAN)
        name = "second.txt" if "Subtask 2" in prompt else "marker.txt"
        (Path(cwd) / name).write_text("OK\n")
        return _result(_AGENT_CHATTER)

    cli._cmd_run(_Args(), _run_agent=rejected_once, _progress_force=True)
    err = capsys.readouterr().err

    assert "plan rejected" in err
    assert "retrying" in err


def test_the_item_header_is_not_printed_twice(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli._cmd_run(_Args(), _run_agent=honest_agent, _progress_force=True)
    err = capsys.readouterr().err

    assert err.count("→ Do the thing") == 1


def test_planning_is_silent_when_progress_is_suppressed(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli._cmd_run(_Args(json=True), _run_agent=honest_agent)
    assert "planning" not in capsys.readouterr().err
