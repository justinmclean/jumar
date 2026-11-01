# SPDX-License-Identifier: Apache-2.0
"""Tests for pinning the code a command check executes (W23): a check never
runs agent-authored code as its verifier."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from jumar import cli
from jumar.config import Config
from jumar.harness import AgentResult
from jumar.models import Check, CheckKind, Verdict
from jumar.verify import VerifyContext
from jumar.verify.command import verify_command
from jumar.verify.pinned import executed_files, pin_plan, unwritten_program

PY = Path(sys.executable).name  # the interpreter name the allow list knows


def _cmd(*argv: str) -> Check:
    return Check(kind=CheckKind.command, statement="s", command=tuple(argv))


# ---------------------------------------------------------------------------
# What a command check executes from the tree
# ---------------------------------------------------------------------------


def test_interpreter_scripts_in_the_tree_are_executed_files(tmp_path: Path) -> None:
    assert executed_files(_cmd("python3", "check.py"), tmp_path) == ["check.py"]
    assert executed_files(_cmd("python3", "-u", "-W", "ignore", "tools/c.py", "x"), tmp_path) == [
        "tools/c.py"
    ]
    assert executed_files(_cmd("bash", "verify.sh"), tmp_path) == ["verify.sh"]
    assert executed_files(_cmd("node", "check.js", "--strict"), tmp_path) == ["check.js"]
    assert executed_files(_cmd("python3.12", "check.py"), tmp_path) == ["check.py"]


def test_a_path_argv0_in_the_tree_is_an_executed_file(tmp_path: Path) -> None:
    assert executed_files(_cmd("./verify.sh"), tmp_path) == ["verify.sh"]
    assert executed_files(_cmd("scripts/check", "--all"), tmp_path) == ["scripts/check"]


def test_python_dash_m_resolving_into_the_tree_is_an_executed_file(tmp_path: Path) -> None:
    (tmp_path / "mycheck.py").write_text("")
    assert executed_files(_cmd("python3", "-m", "mycheck"), tmp_path) == ["mycheck.py"]
    assert executed_files(_cmd("python3", "-m", "json.tool", "x.json"), tmp_path) == []


def test_inline_code_and_out_of_tree_programs_are_not_pinned(tmp_path: Path) -> None:
    assert executed_files(_cmd("python3", "-c", "import x; print(1)"), tmp_path) == []
    assert executed_files(_cmd("grep", "-q", "OK", "out.txt"), tmp_path) == []
    assert executed_files(_cmd("python3", "/usr/lib/some/check.py"), tmp_path) == []


def test_a_bare_name_that_path_resolves_into_the_tree_is_an_executed_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An agent-planted ./bin/grep shadowing the real one."""
    planted = tmp_path / "bin" / "grep"
    planted.parent.mkdir()
    planted.write_text("#!/bin/sh\nexit 0\n")
    planted.chmod(0o755)
    monkeypatch.setenv("PATH", f"{planted.parent}{os.pathsep}{os.environ['PATH']}")
    assert executed_files(_cmd("grep", "-q", "OK", "out.txt"), tmp_path) == ["bin/grep"]


# ---------------------------------------------------------------------------
# The verifier refuses unpinned or changed code
# ---------------------------------------------------------------------------

_STRICT = "import pathlib, sys\nsys.exit(0 if 'OK' in pathlib.Path('out.txt').read_text() else 1)\n"


def _ctx(tmp_path: Path) -> VerifyContext:
    return VerifyContext(
        cwd=tmp_path,
        run_dir=tmp_path / "run",
        evidence_head_bytes=256,
        subtask_id="i#0",
        attempt_no=0,
        config=Config(),
    )


def test_a_pinned_unchanged_script_runs(tmp_path: Path) -> None:
    (tmp_path / "check.py").write_text(_STRICT)
    (tmp_path / "out.txt").write_text("OK\n")
    check = _cmd(PY, "check.py")
    pin_plan(check, subtask_id="i#0", cwd=tmp_path, run_dir=tmp_path / "run")
    assert verify_command(check, _ctx(tmp_path)).verdict is Verdict.passed


def test_a_script_changed_after_plan_time_is_refused_and_never_run(tmp_path: Path) -> None:
    """W23 validation: the check script changes after plan time; it is not run."""
    (tmp_path / "check.py").write_text(_STRICT)
    check = _cmd(PY, "check.py")
    pin_plan(check, subtask_id="i#0", cwd=tmp_path, run_dir=tmp_path / "run")

    # The "agent" rewrites its own verifier to pass, and to leave a trace if run.
    (tmp_path / "check.py").write_text("open('RAN', 'w').close()\nraise SystemExit(0)\n")
    result = verify_command(check, _ctx(tmp_path))

    assert result.verdict is Verdict.failed
    assert result.evidence["error"] == "check_program_modified"
    assert result.evidence["path"] == "check.py"
    assert result.evidence["run"] is False
    assert not (tmp_path / "RAN").exists(), "the modified script must never be executed"


def test_a_script_that_did_not_exist_at_plan_time_is_refused(tmp_path: Path) -> None:
    check = _cmd(PY, "check.py")
    pin_plan(check, subtask_id="i#0", cwd=tmp_path, run_dir=tmp_path / "run")
    (tmp_path / "check.py").write_text("raise SystemExit(0)\n")  # written by the subtask

    result = verify_command(check, _ctx(tmp_path))
    assert result.verdict is Verdict.failed
    assert result.evidence["error"] == "check_program_not_pinned"


def test_an_in_tree_script_with_no_plan_record_is_refused(tmp_path: Path) -> None:
    """Fail closed: no pin on record is not a pass."""
    (tmp_path / "check.py").write_text("raise SystemExit(0)\n")
    result = verify_command(_cmd(PY, "check.py"), _ctx(tmp_path))
    assert result.verdict is Verdict.failed
    assert result.evidence["error"] == "check_program_not_pinned"


def test_a_planted_program_shadowing_a_system_one_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    check = _cmd("grep", "-q", "OK", "out.txt")
    pin_plan(check, subtask_id="i#0", cwd=tmp_path, run_dir=tmp_path / "run")  # real grep: nothing
    planted = tmp_path / "bin" / "grep"
    planted.parent.mkdir()
    planted.write_text("#!/bin/sh\nexit 0\n")
    planted.chmod(0o755)
    monkeypatch.setenv("PATH", f"{planted.parent}{os.pathsep}{os.environ['PATH']}")

    result = verify_command(check, _ctx(tmp_path))
    assert result.verdict is Verdict.failed
    assert result.evidence["path"] == "bin/grep"


def test_out_of_tree_programs_are_not_affected(tmp_path: Path) -> None:
    (tmp_path / "out.txt").write_text("OK\n")
    assert verify_command(_cmd("grep", "-q", "OK", "out.txt"), _ctx(tmp_path)).verdict is (
        Verdict.passed
    )


def test_the_pin_is_written_once(tmp_path: Path) -> None:
    (tmp_path / "check.py").write_text("a\n")
    check = _cmd("python3", "check.py")
    first = pin_plan(check, subtask_id="i#0", cwd=tmp_path, run_dir=tmp_path / "run")
    (tmp_path / "check.py").write_text("b\n")
    assert pin_plan(check, subtask_id="i#0", cwd=tmp_path, run_dir=tmp_path / "run") == first


# ---------------------------------------------------------------------------
# Decompose sends back a plan whose check would run code the subtask writes
# ---------------------------------------------------------------------------


def test_a_check_running_a_script_that_does_not_exist_yet_is_rejected(tmp_path: Path) -> None:
    reason = unwritten_program(_cmd("python3", "check_output.py"), tmp_path)
    assert reason is not None
    assert "write its own verifier" in reason
    (tmp_path / "check_output.py").write_text("")
    assert unwritten_program(_cmd("python3", "check_output.py"), tmp_path) is None
    assert unwritten_program(_cmd("grep", "-q", "x", "f"), tmp_path) is None


# ---------------------------------------------------------------------------
# W23 validation, end to end through `jumar run`
# ---------------------------------------------------------------------------

_PLAN = json.dumps(
    {
        "subtasks": [
            {
                "description": "Write OK into out.txt",
                "check": {
                    "kind": "command",
                    "statement": "check.py accepts out.txt",
                    "command": [PY, "check.py"],
                },
            }
        ]
    }
)


def _ok(stdout: str) -> AgentResult:
    return AgentResult(exit_status=0, stdout=stdout, stderr="", timed_out=False, agent_claim="done")


def cheating_agent(prompt: str, *, cwd: Path, **_: Any) -> AgentResult:
    """Never does the work; rewrites the verifier to pass instead."""
    if prompt.startswith("Decompose the following todo item"):
        return _ok(_PLAN)
    (Path(cwd) / "check.py").write_text("open('RAN', 'w').close()\nraise SystemExit(0)\n")
    return _ok("all checks pass now")


class _Args:
    def __init__(self) -> None:
        self.todo: str | None = None
        self.dry_run = False
        self.approve = False
        self.non_interactive = True
        self.until_empty = False
        self.run_id: str | None = None
        self.runs_dir: str | None = None
        self.retry_failed = False


def test_an_agent_rewriting_the_check_script_cannot_pass_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "todo.md").write_text("- [ ] Write the output @capability=write_fs\n")
    (tmp_path / "check.py").write_text(_STRICT)

    assert cli._cmd_run(_Args(), _run_agent=cheating_agent) == 1

    assert not (tmp_path / "RAN").exists(), "the rewritten check script was executed"
    assert "- [ ] Write the output" in (tmp_path / "todo.md").read_text()
    run_dir = next(d for d in (tmp_path / "runs").iterdir() if d.is_dir())
    lines = [json.loads(ln) for ln in (run_dir / "journal.jsonl").read_text().splitlines() if ln]
    (snap,) = [ln for ln in lines if ln["event"] == "check_inputs_snapshot"]
    (pins,) = snap["payload"]["pinned_programs"].values()
    assert list(pins) == ["check.py"] and pins["check.py"]
    verdicts = [ln["payload"] for ln in lines if ln["event"] == "verification"]
    assert verdicts and all(v["verdict"] == "failed" for v in verdicts)
    assert all(v["evidence"]["error"] == "check_program_modified" for v in verdicts)
