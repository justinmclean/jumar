# SPDX-License-Identifier: Apache-2.0
"""Tests for --json output on jumar plan and jumar report.

Acceptance criteria exercised:
- --json flag on jumar plan --dry-run produces stdout that parses with json.loads
- --json flag on jumar report produces stdout that parses with json.loads
- Warnings go to stderr in both modes; stdout stays pure JSON (no warning text)
- Human and JSON modes are built from the same underlying result object
  (PlanResult / RunReport) — verified by asserting the same data appears in both
- Exit statuses are identical in human and JSON modes
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jumar.cli import main
from jumar.journal import (
    ITEM_COMPLETED,
    ITEM_FAILED,
    ITEM_SELECTED,
    PLAN_CREATED,
    RUN_FINISHED,
    RUN_STARTED,
    VERIFICATION,
    Journal,
)
from jumar.report import (
    PlanBlockedItem,
    PlanDeferredItem,
    PlanResult,
    PlanSelectedItem,
    RunReport,
    format_plan_json,
    format_plan_text,
    format_report_json,
    format_summary,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def todo_one_item(tmp_path: Path) -> Path:
    p = tmp_path / "todo.md"
    p.write_text("- [ ] Write unit tests for the parser\n")
    return p


@pytest.fixture()
def todo_deferred(tmp_path: Path) -> Path:
    p = tmp_path / "todo.md"
    p.write_text("- [ ] Future task @not-before=2099-01-01\n")
    return p


@pytest.fixture()
def todo_blocked_cycle(tmp_path: Path) -> Path:
    p = tmp_path / "todo.md"
    p.write_text("- [ ] Task A @id=a @depends=b\n- [ ] Task B @id=b @depends=a\n")
    return p


@pytest.fixture()
def todo_with_warning(tmp_path: Path) -> Path:
    """A file whose first line has a malformed schedule token (ingest warning)."""
    p = tmp_path / "todo.md"
    p.write_text("- [ ] Bad schedule @due=not-a-date\n- [ ] Good task\n")
    return p


def _make_run_dir(tmp_path: Path, run_id: str = "run-json-001") -> Path:
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    return run_dir


def _make_clean_journal(run_dir: Path) -> Journal:
    j = Journal(run_dir / "journal.jsonl", run_dir.name)
    j.append(
        RUN_STARTED,
        payload={
            "now": "2026-08-07T09:00:00+00:00",
            "tz": "UTC",
            "mode": "auto",
            "todo_path": "todo.md",
        },
    )
    j.append(
        ITEM_SELECTED,
        item_id="item-1",
        payload={"text": "Write the tests", "is_overdue": False},
    )
    j.append(
        PLAN_CREATED,
        item_id="item-1",
        payload={
            "source": "model",
            "created_at": "2026-08-07T09:00:01+00:00",
            "subtasks": [
                {
                    "subtask_id": "item-1#0",
                    "index": 0,
                    "description": "Run pytest",
                    "check": {
                        "kind": "command",
                        "statement": "pytest passes",
                        "command": ["pytest", "-q"],
                    },
                    "capabilities": [],
                    "depends_on": [],
                }
            ],
            "harness": {"agent": "claude", "model": "sonnet"},
        },
    )
    j.append(
        VERIFICATION,
        subtask_id="item-1#0",
        item_id="item-1",
        payload={"verdict": "passed", "summary": "exit 0"},
    )
    j.append(ITEM_COMPLETED, item_id="item-1", payload={})
    j.append(RUN_FINISHED, payload={"exit_status": 0})
    return j


def _make_failed_journal(run_dir: Path) -> Journal:
    j = Journal(run_dir / "journal.jsonl", run_dir.name)
    j.append(
        RUN_STARTED,
        payload={
            "now": "2026-08-07T09:00:00+00:00",
            "tz": "UTC",
            "mode": "auto",
            "todo_path": "todo.md",
        },
    )
    j.append(ITEM_SELECTED, item_id="item-1", payload={"text": "Failing item", "is_overdue": False})
    j.append(
        PLAN_CREATED,
        item_id="item-1",
        payload={
            "source": "model",
            "created_at": "2026-08-07T09:00:01+00:00",
            "subtasks": [
                {
                    "subtask_id": "item-1#0",
                    "index": 0,
                    "description": "Do something",
                    "check": {"kind": "command", "statement": "check passes", "command": ["false"]},
                    "capabilities": [],
                    "depends_on": [],
                }
            ],
            "harness": {"agent": "claude", "model": "sonnet"},
        },
    )
    j.append(
        VERIFICATION,
        subtask_id="item-1#0",
        item_id="item-1",
        payload={"verdict": "failed", "summary": "exit 1"},
    )
    j.append(
        ITEM_FAILED,
        item_id="item-1",
        payload={"failure_code": "check_failed", "failed_subtask_index": 0},
    )
    j.append(RUN_FINISHED, payload={"exit_status": 1})
    return j


# ---------------------------------------------------------------------------
# Unit tests for the format functions (same underlying object → both renderers)
# ---------------------------------------------------------------------------


class TestPlanFormatFunctions:
    """Both format_plan_text and format_plan_json must be built from the same PlanResult."""

    def _make_plan_result(self) -> PlanResult:
        return PlanResult(
            run_id="run-abc",
            now="2026-08-07T09:00:00+00:00",
            todo_path="todo.md",
            pending_count=2,
            selected=PlanSelectedItem(
                item_id="item-1",
                text="Write unit tests",
                capabilities=["read_fs", "run_commands"],
                due="2026-08-14",
                authored_subtasks=["Run pytest", "Check coverage"],
            ),
            deferred=[],
            blocked=[],
            next_eligible_at=None,
        )

    def test_format_plan_json_parses(self) -> None:
        result = self._make_plan_result()
        doc = json.loads(format_plan_json(result))
        assert doc["run_id"] == "run-abc"
        assert doc["pending_count"] == 2
        assert doc["selected"]["item_id"] == "item-1"
        assert doc["selected"]["text"] == "Write unit tests"
        assert "run_commands" in doc["selected"]["capabilities"]
        assert doc["selected"]["authored_subtasks"] == ["Run pytest", "Check coverage"]

    def test_format_plan_json_and_text_same_data(self) -> None:
        """Both renderers use the same PlanResult — same run_id and item text appear in both."""
        result = self._make_plan_result()
        text = format_plan_text(result)
        doc = json.loads(format_plan_json(result))
        assert result.run_id in text
        assert doc["run_id"] == result.run_id
        assert result.selected is not None
        assert result.selected.text in text
        assert doc["selected"]["text"] == result.selected.text

    def test_format_plan_json_nothing_selected(self) -> None:
        result = PlanResult(
            run_id="run-none",
            now="2026-08-07T09:00:00+00:00",
            todo_path="todo.md",
            pending_count=0,
            selected=None,
            deferred=[
                PlanDeferredItem(
                    item_id="d1",
                    text="Future task",
                    eligible_at="2099-01-01T00:00:00+00:00",
                )
            ],
            blocked=[],
            next_eligible_at="2099-01-01T00:00:00+00:00",
        )
        doc = json.loads(format_plan_json(result))
        assert doc["selected"] is None
        assert len(doc["deferred"]) == 1
        assert doc["deferred"][0]["text"] == "Future task"
        assert doc["next_eligible_at"] is not None

    def test_format_plan_json_with_blocked(self) -> None:
        result = PlanResult(
            run_id="run-b",
            now="2026-08-07T09:00:00+00:00",
            todo_path="todo.md",
            pending_count=1,
            selected=None,
            deferred=[],
            blocked=[
                PlanBlockedItem(item_id="b1", text="Blocked task", reason="depends on unfinished x")
            ],
            next_eligible_at=None,
        )
        doc = json.loads(format_plan_json(result))
        assert len(doc["blocked"]) == 1
        assert doc["blocked"][0]["reason"] == "depends on unfinished x"

    def test_format_plan_text_matches_original_output(self) -> None:
        """Verify the human-text renderer produces the expected structure."""
        result = self._make_plan_result()
        text = format_plan_text(result)
        assert "Run:   run-abc" in text
        assert "Todo:  todo.md  (2 pending)" in text
        assert "Selected:  Write unit tests" in text
        assert "id:           item-1" in text
        assert "due:          2026-08-14" in text
        assert "subtasks (2):" in text
        assert "1. Run pytest" in text

    def test_format_plan_text_deferred_section(self) -> None:
        result = PlanResult(
            run_id="r1",
            now="2026-08-07T09:00:00+00:00",
            todo_path="todo.md",
            pending_count=1,
            selected=None,
            deferred=[
                PlanDeferredItem(
                    item_id="d1", text="Later", eligible_at="2099-01-01T00:00:00+00:00"
                )
            ],
            blocked=[],
            next_eligible_at="2099-01-01T00:00:00+00:00",
        )
        text = format_plan_text(result)
        assert "Nothing eligible" in text
        assert "Deferred (1):" in text
        assert "'Later'" in text


class TestReportFormatFunctions:
    """format_summary and format_report_json must be built from the same RunReport."""

    def _make_report(self, run_dir: Path) -> RunReport:
        from jumar.report import build_report

        return build_report(run_dir)

    def test_format_report_json_parses(self, tmp_path: Path) -> None:
        run_dir = _make_run_dir(tmp_path)
        _make_clean_journal(run_dir)
        from jumar.report import build_report

        report = build_report(run_dir)
        doc = json.loads(format_report_json(report))
        assert doc["run_id"] == run_dir.name
        assert doc["interrupted"] is False
        assert isinstance(doc["items"], list)

    def test_format_report_json_and_summary_same_data(self, tmp_path: Path) -> None:
        """Both renderers use the same RunReport object."""
        run_dir = _make_run_dir(tmp_path)
        _make_clean_journal(run_dir)
        from jumar.report import build_report

        report = build_report(run_dir)
        summary = format_summary(report)
        doc = json.loads(format_report_json(report))
        # Both show the same run_id
        assert doc["run_id"] == report.run_id
        # Human output shows "done", JSON items list has status "done"
        assert "done" in summary
        assert any(i["status"] == "done" for i in doc["items"])

    def test_format_report_json_failed_run(self, tmp_path: Path) -> None:
        run_dir = _make_run_dir(tmp_path)
        _make_failed_journal(run_dir)
        from jumar.report import build_report

        report = build_report(run_dir)
        doc = json.loads(format_report_json(report))
        failed = [i for i in doc["items"] if i["status"] == "failed"]
        assert len(failed) == 1
        assert failed[0]["failure_code"] == "check_failed"
        assert failed[0]["failed_subtask_index"] == 0

    def test_format_report_json_subtasks(self, tmp_path: Path) -> None:
        run_dir = _make_run_dir(tmp_path)
        _make_clean_journal(run_dir)
        from jumar.report import build_report

        report = build_report(run_dir)
        doc = json.loads(format_report_json(report))
        done_items = [i for i in doc["items"] if i["status"] == "done"]
        assert done_items
        st = done_items[0]["subtasks"][0]
        assert st["check_kind"] == "command"
        assert st["verdict"] == "passed"


# ---------------------------------------------------------------------------
# CLI integration: jumar plan --dry-run --json
# ---------------------------------------------------------------------------


class TestPlanJsonCLI:
    def test_json_flag_exits_zero(
        self, todo_one_item: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = main(["plan", "--dry-run", "--json", "--todo", str(todo_one_item)])
        assert rc == 0

    def test_json_stdout_is_valid_json(
        self, todo_one_item: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["plan", "--dry-run", "--json", "--todo", str(todo_one_item)])
        out = capsys.readouterr().out
        doc = json.loads(out)
        assert "run_id" in doc
        assert "selected" in doc

    def test_json_selected_item_fields(
        self, todo_one_item: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["plan", "--dry-run", "--json", "--todo", str(todo_one_item)])
        doc = json.loads(capsys.readouterr().out)
        assert doc["selected"] is not None
        assert "item_id" in doc["selected"]
        assert "text" in doc["selected"]
        assert "capabilities" in doc["selected"]
        assert "authored_subtasks" in doc["selected"]

    def test_json_deferred_item(
        self, todo_deferred: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["plan", "--dry-run", "--json", "--todo", str(todo_deferred)])
        doc = json.loads(capsys.readouterr().out)
        assert doc["selected"] is None
        assert len(doc["deferred"]) == 1
        assert "item_id" in doc["deferred"][0]
        assert "eligible_at" in doc["deferred"][0]

    def test_json_exit_status_matches_human_mode(
        self, todo_one_item: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc_human = main(["plan", "--dry-run", "--todo", str(todo_one_item)])
        capsys.readouterr()
        rc_json = main(["plan", "--dry-run", "--json", "--todo", str(todo_one_item)])
        capsys.readouterr()
        assert rc_human == rc_json

    def test_json_exit_status_on_cycle_error(
        self, todo_blocked_cycle: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Cycle detection returns non-zero in both modes."""
        rc_human = main(["plan", "--dry-run", "--todo", str(todo_blocked_cycle)])
        capsys.readouterr()
        rc_json = main(["plan", "--dry-run", "--json", "--todo", str(todo_blocked_cycle)])
        capsys.readouterr()
        assert rc_human == rc_json
        assert rc_human != 0

    def test_warnings_go_to_stderr_not_stdout(
        self, todo_with_warning: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """In JSON mode, warnings must not appear on stdout — stdout must stay parseable."""
        main(["plan", "--dry-run", "--json", "--todo", str(todo_with_warning)])
        out, err = capsys.readouterr()
        # stdout must be valid JSON even when warnings exist
        json.loads(out)
        # warning text goes to stderr
        assert "warning" in err.lower() or "bad_schedule" in err.lower()

    def test_json_stdout_clean_when_no_warning(
        self, todo_one_item: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Stdout is pure JSON — no trailing text."""
        main(["plan", "--dry-run", "--json", "--todo", str(todo_one_item)])
        out, err = capsys.readouterr()
        # Must parse — nothing else on stdout
        json.loads(out)
        assert err == ""

    def test_json_and_human_modes_same_pending_count(
        self, todo_one_item: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["plan", "--dry-run", "--todo", str(todo_one_item)])
        human_out = capsys.readouterr().out
        main(["plan", "--dry-run", "--json", "--todo", str(todo_one_item)])
        doc = json.loads(capsys.readouterr().out)
        # Human mode prints "1 pending", JSON mode has pending_count = 1
        assert "1 pending" in human_out
        assert doc["pending_count"] == 1

    def test_json_without_dry_run_returns_error(
        self, todo_one_item: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Without --dry-run, plan returns 2 in both modes (not yet implemented)."""
        rc = main(["plan", "--json", "--todo", str(todo_one_item)])
        assert rc == 2


# ---------------------------------------------------------------------------
# CLI integration: jumar report --json
# ---------------------------------------------------------------------------


class TestReportJsonCLI:
    def test_json_flag_exits_zero_on_clean_run(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run_dir = _make_run_dir(tmp_path)
        _make_clean_journal(run_dir)
        rc = main(["report", run_dir.name, "--json", "--runs-dir", str(tmp_path / "runs")])
        assert rc == 0

    def test_json_stdout_is_valid_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run_dir = _make_run_dir(tmp_path)
        _make_clean_journal(run_dir)
        main(["report", run_dir.name, "--json", "--runs-dir", str(tmp_path / "runs")])
        out = capsys.readouterr().out
        doc = json.loads(out)
        assert "run_id" in doc
        assert "items" in doc

    def test_json_exit_status_matches_human_mode_clean(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run_dir = _make_run_dir(tmp_path)
        _make_clean_journal(run_dir)
        rc_human = main(["report", run_dir.name, "--runs-dir", str(tmp_path / "runs")])
        capsys.readouterr()
        rc_json = main(["report", run_dir.name, "--json", "--runs-dir", str(tmp_path / "runs")])
        capsys.readouterr()
        assert rc_human == rc_json == 0

    def test_json_exit_status_matches_human_mode_failed(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run_dir = _make_run_dir(tmp_path)
        _make_failed_journal(run_dir)
        rc_human = main(["report", run_dir.name, "--runs-dir", str(tmp_path / "runs")])
        capsys.readouterr()
        rc_json = main(["report", run_dir.name, "--json", "--runs-dir", str(tmp_path / "runs")])
        capsys.readouterr()
        assert rc_human == rc_json == 1

    def test_json_output_is_pure_json_no_report_written(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """In JSON mode, report.md is not written and stdout is pure JSON."""
        run_dir = _make_run_dir(tmp_path)
        _make_clean_journal(run_dir)
        main(["report", run_dir.name, "--json", "--runs-dir", str(tmp_path / "runs")])
        out = capsys.readouterr().out
        json.loads(out)
        # report.md should not be written in JSON mode
        assert not (run_dir / "report.md").exists()

    def test_json_items_have_expected_structure(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run_dir = _make_run_dir(tmp_path)
        _make_clean_journal(run_dir)
        main(["report", run_dir.name, "--json", "--runs-dir", str(tmp_path / "runs")])
        doc = json.loads(capsys.readouterr().out)
        item = doc["items"][0]
        assert item["status"] == "done"
        assert "item_id" in item
        assert "text" in item
        assert "subtasks" in item

    def test_json_missing_journal_returns_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = main(["report", "nonexistent-run", "--json", "--runs-dir", str(tmp_path / "runs")])
        assert rc == 1

    def test_human_and_json_modes_same_item_count(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run_dir = _make_run_dir(tmp_path)
        _make_clean_journal(run_dir)
        main(["report", run_dir.name, "--runs-dir", str(tmp_path / "runs")])
        human_out = capsys.readouterr().out
        main(["report", run_dir.name, "--json", "--runs-dir", str(tmp_path / "runs")])
        doc = json.loads(capsys.readouterr().out)
        # Human output shows "1 done"
        assert "1 done" in human_out
        done_items = [i for i in doc["items"] if i["status"] == "done"]
        assert len(done_items) == 1

    def test_json_truncated_journal_graceful(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A corrupt trailing journal line produces a partial but valid JSON report."""
        run_dir = _make_run_dir(tmp_path)
        _make_clean_journal(run_dir)
        journal_path = run_dir / "journal.jsonl"
        with journal_path.open("a", encoding="utf-8") as fh:
            fh.write("{corrupt json line\n")
        main(["report", run_dir.name, "--json", "--runs-dir", str(tmp_path / "runs")])
        out = capsys.readouterr().out
        doc = json.loads(out)
        assert doc["truncated"] is True
