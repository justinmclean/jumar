# SPDX-License-Identifier: Apache-2.0
"""Tests for check-input snapshots (W21): a repair that satisfies its check by
replacing what the check reads is reported on the verdict."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jumar import cli
from jumar.config import Config
from jumar.harness import AgentResult
from jumar.journal import Journal
from jumar.models import Check, CheckKind
from jumar.verify import VerifyContext, run_verify
from jumar.verify.inputs import (
    check_inputs_evidence,
    compare,
    input_paths,
    snapshot_plan,
    take_snapshot,
)

_FILE_CHECK = Check(kind=CheckKind.file, statement="out says OK", path="out.txt", pattern="OK")

# ---------------------------------------------------------------------------
# What counts as an input
# ---------------------------------------------------------------------------


def test_a_file_checks_path_is_an_input(tmp_path: Path) -> None:
    assert input_paths(_FILE_CHECK, tmp_path) == ["out.txt"]


def test_a_glob_path_is_not_snapshotted(tmp_path: Path) -> None:
    check = Check(kind=CheckKind.absence, statement="no tmp files", path="build/*.tmp")
    assert input_paths(check, tmp_path) == []


def test_command_tokens_naming_files_in_the_tree_are_inputs(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test(): pass\n")
    (tmp_path / "verify.sh").write_text("exit 0\n")
    check = Check(
        kind=CheckKind.command,
        statement="tests pass",
        command=("python3", "-m", "pytest", "tests/test_x.py", "-q", "--rootdir=.", "missing.py"),
    )
    assert input_paths(check, tmp_path) == ["tests/test_x.py"]
    script = Check(kind=CheckKind.command, statement="verify", command=("./verify.sh",))
    assert input_paths(script, tmp_path) == ["./verify.sh"]


def test_a_file_outside_the_working_tree_is_not_an_input(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("x\n")
    try:
        check = Check(
            kind=CheckKind.command, statement="x", command=("grep", "-q", "x", str(outside))
        )
        assert input_paths(check, tmp_path) == []
    finally:
        outside.unlink()


# ---------------------------------------------------------------------------
# Comparing snapshots
# ---------------------------------------------------------------------------


def _snap(tmp_path: Path, text: str | None) -> dict[str, Any]:
    target = tmp_path / "out.txt"
    if text is None:
        target.unlink(missing_ok=True)
    else:
        target.write_text(text)
    return take_snapshot(["out.txt"], tmp_path)


_DRAFT = "".join(f"line {i} of the draft report\n" for i in range(20))


def test_an_edit_that_keeps_most_lines_is_changed_not_replaced(tmp_path: Path) -> None:
    before = _snap(tmp_path, _DRAFT)
    after = _snap(tmp_path, _DRAFT + "OK\n")
    report = compare(before, after)
    assert report["changed"] == ["out.txt"]
    assert report["replaced_wholesale"] == []


def test_new_content_sharing_almost_nothing_is_replaced_wholesale(tmp_path: Path) -> None:
    before = _snap(tmp_path, _DRAFT)
    after = _snap(tmp_path, "OK\n")
    report = compare(before, after)
    assert report["changed"] == ["out.txt"]
    assert report["replaced_wholesale"] == ["out.txt"]


def test_created_removed_and_unchanged_are_told_apart(tmp_path: Path) -> None:
    missing = _snap(tmp_path, None)
    present = _snap(tmp_path, "a\n")
    assert compare(missing, present)["created"] == ["out.txt"]
    assert compare(present, _snap(tmp_path, None))["removed"] == ["out.txt"]
    same = compare(present, _snap(tmp_path, "a\n"))
    assert not any(same.values())


# ---------------------------------------------------------------------------
# Baselines: the plan, then each attempt
# ---------------------------------------------------------------------------


def test_the_plan_baseline_is_written_once(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    (tmp_path / "out.txt").write_text("first\n")
    first = snapshot_plan(_FILE_CHECK, subtask_id="i#0", cwd=tmp_path, run_dir=run_dir)
    (tmp_path / "out.txt").write_text("second\n")
    again = snapshot_plan(_FILE_CHECK, subtask_id="i#0", cwd=tmp_path, run_dir=run_dir)
    assert first == again  # a resumed run keeps the original baseline


def test_attempt_zero_compares_with_the_plan_and_later_ones_with_the_last_attempt(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    snapshot_plan(_FILE_CHECK, subtask_id="i#0", cwd=tmp_path, run_dir=run_dir)
    (tmp_path / "out.txt").write_text(_DRAFT)
    first = check_inputs_evidence(
        _FILE_CHECK, subtask_id="i#0", attempt_no=0, cwd=tmp_path, run_dir=run_dir
    )
    assert first["since"] == "plan"
    assert first["created"] == ["out.txt"]

    (tmp_path / "out.txt").write_text("OK\n")
    second = check_inputs_evidence(
        _FILE_CHECK, subtask_id="i#0", attempt_no=1, cwd=tmp_path, run_dir=run_dir
    )
    assert second["since"] == "attempt 0"
    assert second["replaced_wholesale"] == ["out.txt"]


def test_no_baseline_means_nothing_is_compared(tmp_path: Path) -> None:
    (tmp_path / "out.txt").write_text("OK\n")
    report = check_inputs_evidence(
        _FILE_CHECK, subtask_id="i#0", attempt_no=0, cwd=tmp_path, run_dir=tmp_path / "run"
    )
    assert report == {"since": None, "inputs": ["out.txt"]}


# ---------------------------------------------------------------------------
# run_verify reports it on the verdict
# ---------------------------------------------------------------------------


def _verify(tmp_path: Path, check: Check, attempt_no: int) -> Any:
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    ctx = VerifyContext(
        cwd=tmp_path,
        run_dir=run_dir,
        evidence_head_bytes=256,
        subtask_id="i#0",
        attempt_no=attempt_no,
        config=Config(),
    )
    return run_verify(check, journal=Journal(run_dir / "journal.jsonl", "r"), item_id="i", ctx=ctx)


def test_the_verdict_names_a_file_a_command_check_reads_rewritten_by_a_repair(
    tmp_path: Path,
) -> None:
    """The command check re-runs unchanged, but the data it reads was swapped.

    (A check *script* swapped the same way is no longer reported but refused:
    the code a check executes is pinned at plan time -- see test_pinned.py.)
    """
    (tmp_path / "report.csv").write_text("".join(f"row {i},pending\n" for i in range(10)))
    check = Check(
        kind=CheckKind.command,
        statement="report has a done row",
        command=("grep", "-q", ",done", "report.csv"),
    )
    snapshot_plan(check, subtask_id="i#0", cwd=tmp_path, run_dir=tmp_path / "run")

    _verify(tmp_path, check, 0)  # fails: no row is done
    (tmp_path / "report.csv").write_text("x,done\n")  # the "repair"
    result = _verify(tmp_path, check, 1)

    assert result.verdict.value == "passed"
    assert result.evidence["check_inputs"]["replaced_wholesale"] == ["report.csv"]
    assert "report.csv replaced wholesale since attempt 0" in result.summary


def test_the_report_never_changes_the_verdict(tmp_path: Path) -> None:
    snapshot_plan(_FILE_CHECK, subtask_id="i#0", cwd=tmp_path, run_dir=tmp_path / "run")
    (tmp_path / "out.txt").write_text(_DRAFT)
    _verify(tmp_path, _FILE_CHECK, 0)
    (tmp_path / "out.txt").write_text("OK\n")
    result = _verify(tmp_path, _FILE_CHECK, 1)
    assert result.verdict.value == "passed"
    assert result.evidence["check_inputs"]["replaced_wholesale"] == ["out.txt"]


# ---------------------------------------------------------------------------
# W21 validation, end to end through `jumar run`
# ---------------------------------------------------------------------------

_PLAN = json.dumps(
    {
        "subtasks": [
            {
                "description": "Write report.txt; it must contain OK",
                "check": {
                    "kind": "file",
                    "statement": "report.txt contains OK",
                    "path": "report.txt",
                    "pattern": "OK",
                },
            }
        ]
    }
)


def _ok(stdout: str) -> AgentResult:
    return AgentResult(exit_status=0, stdout=stdout, stderr="", timed_out=False, agent_claim="done")


def replacing_agent(prompt: str, *, cwd: Path, **_: Any) -> AgentResult:
    """Writes a real draft first; the repair throws it away and writes just "OK"."""
    if prompt.startswith("Decompose the following todo item"):
        return _ok(_PLAN)
    if prompt.startswith("A previous attempt at this subtask"):
        (Path(cwd) / "report.txt").write_text("OK\n")
        return _ok("fixed")
    (Path(cwd) / "report.txt").write_text(_DRAFT)
    return _ok("drafted")


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


def test_a_repair_that_replaces_the_checked_file_is_reported_on_the_verdict(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "todo.md").write_text("- [ ] Write the report @capability=write_fs\n")

    assert cli._cmd_run(_Args(), _run_agent=replacing_agent) == 0

    run_dir = next(d for d in (tmp_path / "runs").iterdir() if d.is_dir())
    lines = [json.loads(ln) for ln in (run_dir / "journal.jsonl").read_text().splitlines() if ln]
    (snapshot,) = [ln for ln in lines if ln["event"] == "check_inputs_snapshot"]
    (sub_id,) = snapshot["payload"]["subtasks"]
    assert snapshot["payload"]["subtasks"][sub_id] == {"report.txt": None}  # not yet written

    verifications = [ln["payload"] for ln in lines if ln["event"] == "verification"]
    first, last = verifications[0], verifications[-1]
    assert first["verdict"] == "failed"
    assert first["evidence"]["check_inputs"]["since"] == "plan"
    assert first["evidence"]["check_inputs"]["created"] == ["report.txt"]
    assert last["verdict"] == "passed"
    assert last["evidence"]["check_inputs"]["since"] == "attempt 0"
    assert last["evidence"]["check_inputs"]["replaced_wholesale"] == ["report.txt"]
    assert "replaced wholesale since attempt 0" in last["summary"]
