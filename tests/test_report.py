# SPDX-License-Identifier: Apache-2.0
"""Tests for report.py — Stage 9: per-run report from journal.

Acceptance criteria exercised here:
- AC9.1  Report names the failing subtask index, description, and check evidence summary.
- AC9.2  Exit status is 0 on a fully clean run and 1 when any item failed.
- AC9.3  The report is written even when the run is interrupted mid-item, clearly marked partial.
- AC9.4  A run with only deferred items exits 0 and names the next eligibility instant.
- AC9.5  Overdue items are listed as overdue in the report even when they completed successfully.
"""

from __future__ import annotations

from pathlib import Path

import pytest

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
    build_report,
    format_markdown,
    format_summary,
    report_exit_status,
    write_report,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RUN_STARTED_PAYLOAD = {
    "now": "2026-08-07T09:00:00Z",
    "tz": "UTC",
    "mode": "auto",
    "todo_path": "todo.md",
}


def _make_run_dir(tmp_path: Path, run_id: str = "run-test-001") -> Path:
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    return run_dir


def _make_journal(run_dir: Path) -> Journal:
    return Journal(run_dir / "journal.jsonl", run_dir.name)


def _plan_payload(item_id: str) -> dict:
    """A minimal PLAN_CREATED payload with two subtasks."""
    return {
        "source": "model",
        "created_at": "2026-08-07T09:00:01Z",
        "harness": {"agent": "claude", "model": "sonnet"},
        "subtask_count": 2,
        "subtasks": [
            {
                "subtask_id": f"{item_id}#0",
                "index": 0,
                "description": "Write the implementation",
                "check": {
                    "kind": "command",
                    "statement": "Tests pass",
                    "command": ["pytest", "-q"],
                    "expect_status": 0,
                    "timeout_s": 300,
                },
                "capabilities": ["run_commands"],
                "depends_on": [],
            },
            {
                "subtask_id": f"{item_id}#1",
                "index": 1,
                "description": "Update the docs",
                "check": {
                    "kind": "file",
                    "statement": "README updated",
                    "path": "README.md",
                    "timeout_s": 300,
                },
                "capabilities": ["write_fs"],
                "depends_on": [0],
            },
        ],
    }


# ---------------------------------------------------------------------------
# AC9.1 — report names failing subtask index, description, check evidence
# ---------------------------------------------------------------------------


def test_report_names_failing_subtask(tmp_path: Path) -> None:
    """AC9.1: failing item report includes subtask index, description, and check."""
    run_dir = _make_run_dir(tmp_path)
    j = _make_journal(run_dir)
    item_id = "write-tests-a1b2c3d4"

    j.append(RUN_STARTED, payload=_RUN_STARTED_PAYLOAD)
    j.append(ITEM_SELECTED, item_id=item_id, payload={"text": "Write tests", "is_overdue": False})
    j.append(PLAN_CREATED, item_id=item_id, payload=_plan_payload(item_id))
    j.append(
        VERIFICATION,
        subtask_id=f"{item_id}#1",
        item_id=item_id,
        payload={"verdict": "failed", "summary": "README.md not found"},
    )
    j.append(
        ITEM_FAILED,
        item_id=item_id,
        payload={"failure_code": "check_failed", "failed_subtask_index": 1},
    )
    j.append(RUN_FINISHED, payload={"exit_status": 1})

    report = build_report(run_dir)
    assert len([i for i in report.items if i.status == "failed"]) == 1

    failed_item = next(i for i in report.items if i.status == "failed")
    assert failed_item.failure_code == "check_failed"
    assert failed_item.failed_subtask_index == 1

    # Subtask at index 1 should have the description from plan_created.
    matching = [s for s in failed_item.subtasks if s.index == 1]
    assert matching, "failed subtask must appear in report"
    st = matching[0]
    assert "docs" in st.description.lower() or "Update" in st.description
    assert st.evidence_summary == "README.md not found"
    assert st.check_kind == "file"
    assert "README" in st.check_statement

    # summary text includes the failing subtask description (AC9.1).
    summary = format_summary(report)
    assert "README.md not found" in summary or "Update" in summary


def test_report_failing_subtask_in_markdown(tmp_path: Path) -> None:
    """AC9.1: failing subtask detail appears in the markdown report."""
    run_dir = _make_run_dir(tmp_path)
    j = _make_journal(run_dir)
    item_id = "do-thing-a1b2c3d4"

    j.append(RUN_STARTED, payload=_RUN_STARTED_PAYLOAD)
    j.append(ITEM_SELECTED, item_id=item_id, payload={"text": "Do a thing", "is_overdue": False})
    j.append(PLAN_CREATED, item_id=item_id, payload=_plan_payload(item_id))
    j.append(
        VERIFICATION,
        subtask_id=f"{item_id}#0",
        item_id=item_id,
        payload={"verdict": "failed", "summary": "exit code 1"},
    )
    j.append(
        ITEM_FAILED,
        item_id=item_id,
        payload={"failure_code": "check_failed", "failed_subtask_index": 0},
    )
    j.append(RUN_FINISHED, payload={"exit_status": 1})

    report = build_report(run_dir)
    md = format_markdown(report)
    assert "Failed Items" in md
    assert "check_failed" in md or "check failed" in md.lower()
    assert "exit code 1" in md


# ---------------------------------------------------------------------------
# AC9.2 — exit status 0 on clean run, 1 on failure
# ---------------------------------------------------------------------------


def test_exit_status_clean_run(tmp_path: Path) -> None:
    """AC9.2: exit status 0 when all items completed."""
    run_dir = _make_run_dir(tmp_path)
    j = _make_journal(run_dir)
    item_id = "item-done-a1b2c3d4"

    j.append(RUN_STARTED, payload=_RUN_STARTED_PAYLOAD)
    j.append(ITEM_SELECTED, item_id=item_id, payload={"text": "Finished task", "is_overdue": False})
    j.append(ITEM_COMPLETED, item_id=item_id)
    j.append(RUN_FINISHED, payload={"exit_status": 0})

    report = build_report(run_dir)
    assert report_exit_status(report) == 0


def test_exit_status_failed_run(tmp_path: Path) -> None:
    """AC9.2: exit status 1 when any item failed."""
    run_dir = _make_run_dir(tmp_path)
    j = _make_journal(run_dir)
    item_id = "item-fail-a1b2c3d4"

    j.append(RUN_STARTED, payload=_RUN_STARTED_PAYLOAD)
    j.append(ITEM_SELECTED, item_id=item_id, payload={"text": "Failed task", "is_overdue": False})
    j.append(ITEM_FAILED, item_id=item_id, payload={"failure_code": "check_failed"})
    j.append(RUN_FINISHED, payload={"exit_status": 1})

    report = build_report(run_dir)
    assert report_exit_status(report) == 1


def test_exit_status_empty_run(tmp_path: Path) -> None:
    """AC9.2: exit status 0 for a run where nothing was eligible (deferred-only counts as clean)."""
    run_dir = _make_run_dir(tmp_path)
    j = _make_journal(run_dir)

    j.append(RUN_STARTED, payload=_RUN_STARTED_PAYLOAD)
    j.append(RUN_FINISHED, payload={"exit_status": 0})

    report = build_report(run_dir)
    assert report_exit_status(report) == 0


# ---------------------------------------------------------------------------
# AC9.3 — partial report from interrupted journal, clearly marked
# ---------------------------------------------------------------------------


def test_partial_report_interrupted_run(tmp_path: Path) -> None:
    """AC9.3: report built from an interrupted journal is marked partial."""
    run_dir = _make_run_dir(tmp_path)
    j = _make_journal(run_dir)
    item_id = "item-progress-a1b2c3d4"

    j.append(RUN_STARTED, payload=_RUN_STARTED_PAYLOAD)
    j.append(
        ITEM_SELECTED,
        item_id=item_id,
        payload={"text": "Item in progress", "is_overdue": False},
    )
    j.append(PLAN_CREATED, item_id=item_id, payload=_plan_payload(item_id))
    # Run killed here — no item_completed, item_failed, or run_finished.

    report = build_report(run_dir)
    # Run is interrupted (no run_finished).
    assert report.interrupted is True
    assert not report.truncated  # not a corrupt line, just no run_finished

    # Item is listed as in_progress.
    assert any(i.status == "in_progress" for i in report.items)

    # Summary and markdown clearly indicate partial.
    summary = format_summary(report)
    assert "interrupted" in summary.lower() or "partial" in summary.lower()

    md = format_markdown(report)
    assert "partial" in md.lower() or "interrupted" in md.lower()


def test_partial_report_truncated_journal(tmp_path: Path) -> None:
    """AC9.3: truncated (corrupt trailing line) journal is also marked partial."""
    run_dir = _make_run_dir(tmp_path)
    j = _make_journal(run_dir)
    item_id = "item-trunc-a1b2c3d4"

    j.append(RUN_STARTED, payload=_RUN_STARTED_PAYLOAD)
    j.append(
        ITEM_SELECTED,
        item_id=item_id,
        payload={"text": "Truncated item", "is_overdue": False},
    )
    # Append a corrupt (truncated) line to simulate a crash mid-write.
    journal_path = run_dir / "journal.jsonl"
    with journal_path.open("a", encoding="utf-8") as fh:
        fh.write('{"seq": 99, "event": "item_completed", "item_id": "...')

    report = build_report(run_dir)
    assert report.truncated is True
    # The two valid entries are still reflected.
    assert report.now is not None
    assert any(i.status == "in_progress" for i in report.items)


def test_write_report_creates_file(tmp_path: Path) -> None:
    """AC9.3: write_report writes report.md even for a partial run."""
    run_dir = _make_run_dir(tmp_path)
    j = _make_journal(run_dir)
    j.append(RUN_STARTED, payload=_RUN_STARTED_PAYLOAD)
    # No run_finished — partial.

    report = build_report(run_dir)
    path = write_report(report, run_dir)
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "partial" in content.lower() or "interrupted" in content.lower()


# ---------------------------------------------------------------------------
# AC9.4 — deferred-only run exits 0, names next eligibility instant
# ---------------------------------------------------------------------------


def test_deferred_only_run_exit_zero(tmp_path: Path) -> None:
    """AC9.4: a run where every item is deferred exits 0."""
    run_dir = _make_run_dir(tmp_path)
    j = _make_journal(run_dir)

    j.append(RUN_STARTED, payload=_RUN_STARTED_PAYLOAD)
    j.append(
        "item_deferred",
        item_id="future-task-a1b2c3d4",
        payload={"text": "Future task", "eligible_at": "2026-09-01T00:00:00+00:00"},
    )
    j.append(RUN_FINISHED, payload={"exit_status": 0})

    report = build_report(run_dir)
    # Deferred items never cause exit 1.
    assert report_exit_status(report) == 0
    # next_eligible_at is populated.
    assert report.next_eligible_at is not None
    assert "2026-09-01" in report.next_eligible_at

    summary = format_summary(report)
    assert "deferred" in summary.lower()


def test_deferred_run_next_eligible_earliest(tmp_path: Path) -> None:
    """AC9.4: next_eligible_at is the *earliest* eligibility across all deferred items."""
    run_dir = _make_run_dir(tmp_path)
    j = _make_journal(run_dir)

    j.append(RUN_STARTED, payload=_RUN_STARTED_PAYLOAD)
    j.append(
        "item_deferred",
        item_id="far-future-a1b2c3d4",
        payload={"text": "Far future", "eligible_at": "2026-12-01T00:00:00+00:00"},
    )
    j.append(
        "item_deferred",
        item_id="near-future-a1b2c3d4",
        payload={"text": "Near future", "eligible_at": "2026-09-01T00:00:00+00:00"},
    )
    j.append(RUN_FINISHED, payload={"exit_status": 0})

    report = build_report(run_dir)
    assert report.next_eligible_at is not None
    assert "2026-09-01" in report.next_eligible_at


def test_deferred_run_markdown_names_instant(tmp_path: Path) -> None:
    """AC9.4: the markdown report names the next eligibility instant."""
    run_dir = _make_run_dir(tmp_path)
    j = _make_journal(run_dir)

    j.append(RUN_STARTED, payload=_RUN_STARTED_PAYLOAD)
    j.append(
        "item_deferred",
        item_id="defer-a1b2c3d4",
        payload={"text": "Deferred", "eligible_at": "2026-09-15T09:00:00+00:00"},
    )
    j.append(RUN_FINISHED, payload={"exit_status": 0})

    report = build_report(run_dir)
    md = format_markdown(report)
    assert "2026-09-15" in md
    assert "Next eligibility" in md or "eligible" in md.lower()


# ---------------------------------------------------------------------------
# AC9.5 — overdue items listed as overdue even when completed
# ---------------------------------------------------------------------------


def test_overdue_completed_item_flagged(tmp_path: Path) -> None:
    """AC9.5: an overdue item that completed is still listed as overdue."""
    run_dir = _make_run_dir(tmp_path)
    j = _make_journal(run_dir)
    item_id = "overdue-done-a1b2c3d4"

    j.append(RUN_STARTED, payload=_RUN_STARTED_PAYLOAD)
    j.append(
        ITEM_SELECTED,
        item_id=item_id,
        payload={
            "text": "Fix that old bug",
            "is_overdue": True,
            "due": "2026-08-01T00:00:00+00:00",
        },
    )
    j.append(ITEM_COMPLETED, item_id=item_id)
    j.append(RUN_FINISHED, payload={"exit_status": 0})

    report = build_report(run_dir)
    done_items = [i for i in report.items if i.status == "done"]
    assert done_items, "Item must be done"
    assert done_items[0].is_overdue is True

    # Summary mentions overdue.
    summary = format_summary(report)
    assert "overdue" in summary.lower()

    # Markdown also marks it.
    md = format_markdown(report)
    assert "overdue" in md.lower()


def test_non_overdue_completed_item_not_flagged(tmp_path: Path) -> None:
    """AC9.5 negative: a non-overdue completed item is NOT marked overdue."""
    run_dir = _make_run_dir(tmp_path)
    j = _make_journal(run_dir)
    item_id = "on-time-a1b2c3d4"

    j.append(RUN_STARTED, payload=_RUN_STARTED_PAYLOAD)
    j.append(
        ITEM_SELECTED,
        item_id=item_id,
        payload={"text": "On-time task", "is_overdue": False},
    )
    j.append(ITEM_COMPLETED, item_id=item_id)
    j.append(RUN_FINISHED, payload={"exit_status": 0})

    report = build_report(run_dir)
    done_items = [i for i in report.items if i.status == "done"]
    assert done_items
    assert done_items[0].is_overdue is False

    summary = format_summary(report)
    # "Overdue" should not appear for this non-overdue completed item.
    assert "overdue" not in summary.lower()


# ---------------------------------------------------------------------------
# Empty / missing journal
# ---------------------------------------------------------------------------


def test_empty_journal_directory(tmp_path: Path) -> None:
    """build_report on a missing journal returns an empty report with interrupted=False."""
    run_dir = _make_run_dir(tmp_path, run_id="empty-run")
    # No journal.jsonl created.
    report = build_report(run_dir)
    assert report.run_id == "empty-run"
    assert report.items == []
    assert not report.truncated
    assert not report.interrupted  # no entries at all, not interrupted


def test_report_run_id_matches_directory_name(tmp_path: Path) -> None:
    """RunReport.run_id is taken from the directory name."""
    run_dir = _make_run_dir(tmp_path, run_id="my-special-run-id")
    j = _make_journal(run_dir)
    j.append(RUN_STARTED, payload=_RUN_STARTED_PAYLOAD)

    report = build_report(run_dir)
    assert report.run_id == "my-special-run-id"


def test_report_metadata_from_run_started(tmp_path: Path) -> None:
    """RunReport carries now, tz, todo_path, mode from the run_started event."""
    run_dir = _make_run_dir(tmp_path)
    j = _make_journal(run_dir)
    j.append(
        RUN_STARTED,
        payload={
            "now": "2026-08-07T09:00:00Z",
            "tz": "Australia/Sydney",
            "mode": "approve",
            "todo_path": "/home/user/todo.md",
        },
    )

    report = build_report(run_dir)
    assert report.now == "2026-08-07T09:00:00Z"
    assert report.tz == "Australia/Sydney"
    assert report.todo_path == "/home/user/todo.md"
    assert report.mode == "approve"


# ---------------------------------------------------------------------------
# F1 — friendly-run-ids: prefix matching and "latest" in jumar report
# ---------------------------------------------------------------------------


def test_report_unambiguous_prefix_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``jumar report <prefix>`` resolves when the prefix matches exactly one run."""
    import json as _json

    monkeypatch.chdir(tmp_path)
    run_id = "20260810-1430-abcd"
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    journal = run_dir / "journal.jsonl"
    journal.write_text(
        _json.dumps(
            {
                "seq": 1,
                "event": "run_started",
                "ts": "2026-08-10T14:30:00+00:00",
                "payload": {
                    "now": "2026-08-10T14:30:00Z",
                    "tz": "UTC",
                    "mode": "auto",
                    "todo_path": "todo.md",
                },
            }
        )
        + "\n"
        + _json.dumps(
            {
                "seq": 2,
                "event": "run_finished",
                "ts": "2026-08-10T14:30:01+00:00",
                "payload": {"exit_status": 0},
            }
        )
        + "\n"
    )

    from jumar.cli import main as cli_main

    rc = cli_main(["report", "20260810-1430", "--runs-dir", str(tmp_path / "runs")])
    assert rc == 0
    assert (run_dir / "report.md").exists()


def test_report_ambiguous_prefix_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``jumar report <ambiguous-prefix>`` exits 1 and names the candidates."""
    import json as _json

    monkeypatch.chdir(tmp_path)
    for suffix in ("abcd", "ef01"):
        d = tmp_path / "runs" / f"20260810-1430-{suffix}"
        d.mkdir(parents=True)
        entry = {"seq": 1, "event": "run_started", "ts": "t", "payload": {"now": "t"}}
        (d / "journal.jsonl").write_text(_json.dumps(entry) + "\n")

    from jumar.cli import main as cli_main

    rc = cli_main(["report", "20260810-1430", "--runs-dir", str(tmp_path / "runs")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "ambiguous" in err
    assert "20260810-1430-abcd" in err
    assert "20260810-1430-ef01" in err


def test_report_latest_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``jumar report latest`` resolves to the run with the highest run_started.now."""
    import json as _json

    monkeypatch.chdir(tmp_path)

    for run_id, now_str in [
        ("20260101-0900-aaaa", "2026-01-01T09:00:00Z"),
        ("20260810-1430-bbbb", "2026-08-10T14:30:00Z"),
    ]:
        d = tmp_path / "runs" / run_id
        d.mkdir(parents=True)
        started = {
            "seq": 1,
            "event": "run_started",
            "ts": now_str,
            "payload": {"now": now_str, "tz": "UTC", "mode": "auto", "todo_path": "todo.md"},
        }
        finished = {
            "seq": 2,
            "event": "run_finished",
            "ts": now_str,
            "payload": {"exit_status": 0},
        }
        lines = [_json.dumps(started), _json.dumps(finished)]
        (d / "journal.jsonl").write_text("\n".join(lines) + "\n")

    from jumar.cli import main as cli_main

    rc = cli_main(["report", "latest", "--runs-dir", str(tmp_path / "runs")])
    assert rc == 0
    # The newer run's report was written.
    assert (tmp_path / "runs" / "20260810-1430-bbbb" / "report.md").exists()
    # The older run's report was NOT generated this time.
    assert not (tmp_path / "runs" / "20260101-0900-aaaa" / "report.md").exists()


def test_report_id_in_header_matches_directory(tmp_path: Path) -> None:
    """The run id in the report header matches the directory name (new format)."""
    run_id = "20260810-1430-abcd"
    run_dir = _make_run_dir(tmp_path, run_id)
    j = _make_journal(run_dir)
    j.append(RUN_STARTED, payload=_RUN_STARTED_PAYLOAD)
    j.append(RUN_FINISHED, payload={"exit_status": 0})

    report = build_report(run_dir)
    assert report.run_id == run_id
    md = format_markdown(report)
    assert run_id in md


# ---------------------------------------------------------------------------
# F2 — run index used by jumar report latest
# ---------------------------------------------------------------------------


def test_report_latest_uses_index_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``jumar report latest`` resolves via the index when index.tsv exists."""
    import json as _json

    from jumar.index import STATUS_COMPLETED, append_run_started, update_run_finished

    monkeypatch.chdir(tmp_path)
    runs_dir = tmp_path / "runs"

    for run_id, now_str in [
        ("20260101-0900-aaaa", "2026-01-01T09:00:00Z"),
        ("20260811-1200-bbbb", "2026-08-11T12:00:00Z"),
    ]:
        d = runs_dir / run_id
        d.mkdir(parents=True)
        started_entry = {
            "seq": 1,
            "event": "run_started",
            "payload": {"now": now_str, "tz": "UTC", "mode": "auto", "todo_path": "todo.md"},
        }
        finished_entry = {"seq": 2, "event": "run_finished", "payload": {"exit_status": 0}}
        (d / "journal.jsonl").write_text(
            _json.dumps(started_entry) + "\n" + _json.dumps(finished_entry) + "\n",
            encoding="utf-8",
        )
        append_run_started(runs_dir, run_id, now_str)
        update_run_finished(runs_dir, run_id, "some-item", STATUS_COMPLETED)

    from jumar.cli import main as cli_main

    rc = cli_main(["report", "latest", "--runs-dir", str(runs_dir)])
    assert rc == 0
    assert (runs_dir / "20260811-1200-bbbb" / "report.md").exists()
    assert not (runs_dir / "20260101-0900-aaaa" / "report.md").exists()


def test_report_latest_works_without_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``jumar report latest`` resolves via journal scan when no index exists."""
    import json as _json

    monkeypatch.chdir(tmp_path)
    runs_dir = tmp_path / "runs"

    for run_id, now_str in [
        ("uuid-old-aaa", "2026-01-01T09:00:00Z"),
        ("uuid-new-bbb", "2026-08-11T12:00:00Z"),
    ]:
        d = runs_dir / run_id
        d.mkdir(parents=True)
        started_entry = {
            "seq": 1,
            "event": "run_started",
            "payload": {"now": now_str, "tz": "UTC", "mode": "auto", "todo_path": "todo.md"},
        }
        finished_entry = {"seq": 2, "event": "run_finished", "payload": {"exit_status": 0}}
        (d / "journal.jsonl").write_text(
            _json.dumps(started_entry) + "\n" + _json.dumps(finished_entry) + "\n",
            encoding="utf-8",
        )
    # No index.tsv — journal scan must find the newer run.

    from jumar.cli import main as cli_main

    rc = cli_main(["report", "latest", "--runs-dir", str(runs_dir)])
    assert rc == 0
    assert (runs_dir / "uuid-new-bbb" / "report.md").exists()
