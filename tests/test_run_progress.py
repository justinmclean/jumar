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


def _result(stdout: str) -> AgentResult:
    return AgentResult(
        exit_status=0, stdout=stdout, stderr="", timed_out=False, agent_claim="done"
    )


def _is_execution(prompt: str) -> bool:
    """Execution prompts open with this sentence; decomposition prompts do not."""
    return prompt.startswith("You are executing")


def honest_agent(prompt: str, *, cwd: Path, **_: Any) -> AgentResult:
    if not _is_execution(prompt):
        return _result(_PLAN)
    # Key off the subtask header, not the filename: subtask 2's prompt carries
    # subtask 1's evidence, which mentions marker.txt.
    name = "second.txt" if "Subtask 2" in prompt else "marker.txt"
    (Path(cwd) / name).write_text("OK\n")
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


def test_json_suppresses_progress(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--json owns stdout; progress must not appear anywhere."""
    cli._cmd_run(_Args(json=True), _run_agent=honest_agent, _progress_force=None)
    captured = capsys.readouterr()

    assert "[1/2]" not in captured.err
    assert "[1/2]" not in captured.out


def test_non_tty_suppresses_progress(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A piped or scheduled run emits no progress chatter."""
    cli._cmd_run(_Args(), _run_agent=honest_agent, _progress_force=None)
    captured = capsys.readouterr()

    assert "[1/2]" not in captured.err
    assert "[1/2]" not in captured.out


# ---------------------------------------------------------------------------
# Verbose
# ---------------------------------------------------------------------------


def test_verbose_echoes_agent_output(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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
