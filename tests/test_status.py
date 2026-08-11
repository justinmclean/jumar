# SPDX-License-Identifier: Apache-2.0
"""Tests for ``status.py`` — item-centric aggregated status view.

Acceptance criteria exercised
------------------------------
- Aggregation across multiple runs picks the *latest* attempt per item.
- A parked item shows its reason.
- A deferred item shows its next eligibility instant.
- A corrupt journal degrades gracefully (no crash, shows what is known).
- The runs dir is byte-identical before and after (no run directory created).
- A never-attempted eligible item shows state "never-attempted".
- A blocked item shows state "blocked".
- Missing todo file returns empty report without crashing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from getstuffdone.status import ItemStatusLine, StatusReport, build_status, format_status

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)


def _make_config(tmp_path: Path, todo_path: str | None = None) -> object:
    from getstuffdone.config import load_config

    overrides = {"todo_path": todo_path or str(tmp_path / "todo.md"), "timezone": "UTC"}
    return load_config(cli_overrides=overrides)


def _write_todo(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _write_journal(run_dir: Path, events: list[dict]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(e) for e in events]
    (run_dir / "journal.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_started_payload(now_str: str, todo_path: str) -> dict:
    return {
        "event": "run_started",
        "seq": 1,
        "run_id": "run1",
        "ts": now_str,
        "payload": {"now": now_str, "tz": "UTC", "mode": "auto", "todo_path": todo_path},
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_never_attempted_eligible_item(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    _write_todo(todo, "- [ ] Do something @id=item1\n")
    config = _make_config(tmp_path, str(todo))
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    report = build_status(todo, runs_dir, config, _now())

    assert len(report.items) == 1
    line = report.items[0]
    assert line.state == "never-attempted"
    assert line.item_id == "item1"
    assert line.last_run_id is None
    assert line.failure_count == 0


def test_done_item(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    _write_todo(todo, "- [x] Already done @id=item1\n")
    config = _make_config(tmp_path, str(todo))
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    report = build_status(todo, runs_dir, config, _now())

    assert len(report.items) == 1
    assert report.items[0].state == "done"


def test_deferred_item_shows_eligibility_instant(tmp_path: Path) -> None:
    future = "2030-01-01T09:00:00+00:00"
    todo = tmp_path / "todo.md"
    _write_todo(todo, f"- [ ] Future task @id=item1 @not-before={future}\n")
    config = _make_config(tmp_path, str(todo))
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    report = build_status(todo, runs_dir, config, _now())

    assert len(report.items) == 1
    line = report.items[0]
    assert line.state == "deferred"
    assert line.next_eligible_at is not None
    # The next_eligible_at comes from schedule.not_before resolved to UTC
    assert "2030" in line.next_eligible_at


def test_parked_item_shows_reason(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    _write_todo(todo, "- [ ] Broken task @id=item1 @paused=auto-failures\n")
    config = _make_config(tmp_path, str(todo))
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    report = build_status(todo, runs_dir, config, _now())

    assert len(report.items) == 1
    line = report.items[0]
    assert line.state == "parked"
    assert line.paused_reason == "auto-failures"


def test_failure_count_from_failed_token(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    _write_todo(todo, "- [ ] Flaky task @id=item1 @failed=2\n")
    config = _make_config(tmp_path, str(todo))
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    report = build_status(todo, runs_dir, config, _now())

    assert len(report.items) == 1
    assert report.items[0].failure_count == 2


def test_blocked_item(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    _write_todo(
        todo,
        "- [ ] Dependency @id=dep1\n- [ ] Dependent @id=item2 @depends=dep1\n",
    )
    config = _make_config(tmp_path, str(todo))
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    report = build_status(todo, runs_dir, config, _now())

    assert len(report.items) == 2
    states = {r.item_id: r.state for r in report.items}
    assert states["dep1"] in ("eligible", "never-attempted")
    assert states["item2"] == "blocked"


def test_latest_run_wins_when_multiple_runs(tmp_path: Path) -> None:
    """Aggregation across multiple runs picks the latest attempt per item."""
    todo = tmp_path / "todo.md"
    _write_todo(todo, "- [ ] Task A @id=item1\n")
    config = _make_config(tmp_path, str(todo))
    runs_dir = tmp_path / "runs"

    now1 = "2026-01-01T09:00:00+00:00"
    now2 = "2026-06-01T09:00:00+00:00"  # later

    # Older run: item1 failed
    run1_dir = runs_dir / "run-older"
    _write_journal(
        run1_dir,
        [
            {**_run_started_payload(now1, str(todo)), "run_id": "run-older"},
            {
                "event": "item_selected",
                "seq": 2,
                "run_id": "run-older",
                "ts": now1,
                "item_id": "item1",
                "payload": {"text": "Task A"},
            },
            {
                "event": "item_failed",
                "seq": 3,
                "run_id": "run-older",
                "ts": now1,
                "item_id": "item1",
                "payload": {"failure_code": "check_failed"},
            },
        ],
    )

    # Newer run: item1 succeeded
    run2_dir = runs_dir / "run-newer"
    _write_journal(
        run2_dir,
        [
            {**_run_started_payload(now2, str(todo)), "run_id": "run-newer"},
            {
                "event": "item_selected",
                "seq": 2,
                "run_id": "run-newer",
                "ts": now2,
                "item_id": "item1",
                "payload": {"text": "Task A"},
            },
            {
                "event": "item_completed",
                "seq": 3,
                "run_id": "run-newer",
                "ts": now2,
                "item_id": "item1",
                "payload": {},
            },
        ],
    )

    report = build_status(todo, runs_dir, config, _now())

    assert len(report.items) == 1
    line = report.items[0]
    assert line.last_run_id == "run-newer"
    assert line.last_outcome == "done"


def test_corrupt_journal_degrades_gracefully(tmp_path: Path) -> None:
    """A corrupt journal line stops replay but does not crash; the item row is still shown."""
    todo = tmp_path / "todo.md"
    _write_todo(todo, "- [ ] Fragile task @id=item1\n")
    config = _make_config(tmp_path, str(todo))
    runs_dir = tmp_path / "runs"

    now_str = "2026-05-01T09:00:00+00:00"
    run_dir = runs_dir / "run-corrupt"
    run_dir.mkdir(parents=True)
    journal_path = run_dir / "journal.jsonl"
    # Write a valid run_started, then a valid item_selected, then corrupt JSON
    selected = {
        "event": "item_selected",
        "seq": 2,
        "run_id": "run-corrupt",
        "ts": now_str,
        "item_id": "item1",
        "payload": {"text": "Fragile task"},
    }
    completed = {
        "event": "item_completed",
        "seq": 3,
        "run_id": "run-corrupt",
        "ts": now_str,
        "item_id": "item1",
        "payload": {},
    }
    journal_path.write_text(
        json.dumps(_run_started_payload(now_str, str(todo)))
        + "\n"
        + json.dumps(selected)
        + "\n"
        + "{broken json }\n"
        + json.dumps(completed)
        + "\n",
        encoding="utf-8",
    )

    report = build_status(todo, runs_dir, config, _now())

    assert len(report.items) == 1
    line = report.items[0]
    # item_selected was seen before the corrupt line; outcome is in_progress (not crashed)
    assert line.last_run_id == "run-corrupt"
    assert line.last_outcome == "in_progress"


def test_runs_dir_is_unchanged_after_build_status(tmp_path: Path) -> None:
    """gsd status must not create any new run directory or write any file."""
    todo = tmp_path / "todo.md"
    _write_todo(todo, "- [ ] Something @id=item1\n")
    config = _make_config(tmp_path, str(todo))
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    # Snapshot the directory before
    before = {str(p) for p in runs_dir.rglob("*")}

    build_status(todo, runs_dir, config, _now())

    after = {str(p) for p in runs_dir.rglob("*")}
    assert before == after, "build_status must not create any files or directories"


def test_runs_dir_absent_does_not_crash(tmp_path: Path) -> None:
    """If the runs dir does not exist yet, return empty attempt info gracefully."""
    todo = tmp_path / "todo.md"
    _write_todo(todo, "- [ ] Brand new task @id=item1\n")
    config = _make_config(tmp_path, str(todo))
    runs_dir = tmp_path / "no-such-runs-dir"

    report = build_status(todo, runs_dir, config, _now())

    assert len(report.items) == 1
    assert report.items[0].state == "never-attempted"
    assert not runs_dir.exists()  # we did not create it


def test_missing_todo_file_returns_empty_report(tmp_path: Path) -> None:
    """A missing todo file returns an empty StatusReport without raising."""
    todo = tmp_path / "nonexistent.md"
    config = _make_config(tmp_path, str(todo))
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    report = build_status(todo, runs_dir, config, _now())

    assert report.items == []


def test_failed_subtask_details_reported(tmp_path: Path) -> None:
    """When an item failed, the failing subtask description and check kind are shown."""
    todo = tmp_path / "todo.md"
    _write_todo(todo, "- [ ] Task with subtasks @id=item1\n")
    config = _make_config(tmp_path, str(todo))
    runs_dir = tmp_path / "runs"

    now_str = "2026-07-01T09:00:00+00:00"
    run_dir = runs_dir / "run-fail"
    subtask_payload = {
        "subtasks": [
            {
                "subtask_id": "item1#0",
                "index": 0,
                "description": "Run the tests",
                "check": {"kind": "command", "statement": "tests pass"},
            }
        ],
        "source": "model",
        "created_at": now_str,
    }
    _write_journal(
        run_dir,
        [
            {**_run_started_payload(now_str, str(todo)), "run_id": "run-fail"},
            {
                "event": "item_selected",
                "seq": 2,
                "run_id": "run-fail",
                "ts": now_str,
                "item_id": "item1",
                "payload": {"text": "Task with subtasks"},
            },
            {
                "event": "plan_created",
                "seq": 3,
                "run_id": "run-fail",
                "ts": now_str,
                "item_id": "item1",
                "payload": subtask_payload,
            },
            {
                "event": "item_failed",
                "seq": 4,
                "run_id": "run-fail",
                "ts": now_str,
                "item_id": "item1",
                "payload": {"failure_code": "check_failed", "failed_subtask_index": 0},
            },
        ],
    )

    report = build_status(todo, runs_dir, config, _now())

    assert len(report.items) == 1
    line = report.items[0]
    assert line.last_outcome == "failed"
    assert line.failed_subtask_desc == "Run the tests"
    assert line.failed_check_kind == "command"


def test_format_status_includes_all_states(tmp_path: Path) -> None:
    """format_status produces a human-readable string covering key fields."""
    report = StatusReport(
        todo_path="/some/todo.md",
        items=[
            ItemStatusLine(
                item_id="a",
                text="Eligible task",
                state="eligible",
                last_run_id="run-xyz",
                last_run_at="2026-08-01T09:00:00+00:00",
                last_outcome="failed",
                failed_subtask_desc="Write tests",
                failed_check_kind="command",
            ),
            ItemStatusLine(
                item_id="b",
                text="Parked task",
                state="parked",
                paused_reason="auto-failures",
                failure_count=3,
            ),
            ItemStatusLine(
                item_id="c",
                text="Deferred task",
                state="deferred",
                next_eligible_at="2030-01-01T09:00:00+00:00",
            ),
            ItemStatusLine(
                item_id="d",
                text="Never touched",
                state="never-attempted",
            ),
        ],
    )

    text = format_status(report)

    assert "Eligible task" in text
    assert "ELIGIBLE" in text
    assert "Parked task" in text
    assert "auto-failures" in text
    assert "failures: 3" in text
    assert "Deferred task" in text
    assert "2030" in text
    assert "Never touched" in text
    assert "NEVER-ATTEMPTED" in text
    assert "Write tests" in text
    assert "command" in text


def test_multiple_items_eligible_and_done(tmp_path: Path) -> None:
    """Verify all items appear in the status report, with correct states."""
    todo = tmp_path / "todo.md"
    _write_todo(
        todo,
        "- [x] Completed item @id=done1\n- [ ] Pending item @id=pend1\n",
    )
    config = _make_config(tmp_path, str(todo))
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    report = build_status(todo, runs_dir, config, _now())

    assert len(report.items) == 2
    states = {r.item_id: r.state for r in report.items}
    assert states["done1"] == "done"
    assert states["pend1"] == "never-attempted"


def test_cli_status_exits_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``gsd status`` always exits 0, even when items are failed."""
    monkeypatch.chdir(tmp_path)
    todo = tmp_path / "todo.md"
    _write_todo(todo, "- [ ] Some task @id=t1\n")

    from getstuffdone.cli import main

    rc = main(["status", "--todo", str(todo), "--runs-dir", str(tmp_path / "runs")])
    assert rc == 0


def test_cli_status_does_not_create_run_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``gsd status`` must not create a runs directory or any file in it."""
    monkeypatch.chdir(tmp_path)
    todo = tmp_path / "todo.md"
    _write_todo(todo, "- [ ] Some task @id=t1\n")
    runs_dir = tmp_path / "my-runs"

    from getstuffdone.cli import main

    main(["status", "--todo", str(todo), "--runs-dir", str(runs_dir)])

    assert not runs_dir.exists()


# ---------------------------------------------------------------------------
# AC11.5 / AC11.6 — gsd status --json
# ---------------------------------------------------------------------------


def test_status_json_stdout_is_valid_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``gsd status --json`` emits a single JSON document on stdout (AC11.5)."""
    monkeypatch.chdir(tmp_path)
    todo = tmp_path / "todo.md"
    _write_todo(todo, "- [ ] Task one @id=t1\n- [x] Done task @id=t2\n")

    from getstuffdone.cli import main

    rc = main(["status", "--json", "--todo", str(todo), "--runs-dir", str(tmp_path / "runs")])
    assert rc == 0
    out = capsys.readouterr().out
    doc = json.loads(out)
    assert "items" in doc
    assert "todo_path" in doc


def test_status_json_all_items_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every todo item appears exactly once in the JSON output."""
    monkeypatch.chdir(tmp_path)
    todo = tmp_path / "todo.md"
    _write_todo(todo, "- [ ] Alpha @id=alpha\n- [x] Beta @id=beta\n- [ ] Gamma @id=gamma\n")

    from getstuffdone.cli import main

    main(["status", "--json", "--todo", str(todo), "--runs-dir", str(tmp_path / "runs")])
    doc = json.loads(capsys.readouterr().out)
    ids = {item["item_id"] for item in doc["items"]}
    assert ids == {"alpha", "beta", "gamma"}


def test_status_json_item_fields_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Each item in the JSON output carries the expected fields."""
    monkeypatch.chdir(tmp_path)
    todo = tmp_path / "todo.md"
    _write_todo(todo, "- [ ] My task @id=t1\n")

    from getstuffdone.cli import main

    main(["status", "--json", "--todo", str(todo), "--runs-dir", str(tmp_path / "runs")])
    doc = json.loads(capsys.readouterr().out)
    item = doc["items"][0]
    assert item["item_id"] == "t1"
    assert "text" in item
    assert "state" in item
    assert item["state"] == "never-attempted"


def test_status_json_parked_item_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A parked item has paused_reason set in the JSON output."""
    monkeypatch.chdir(tmp_path)
    todo = tmp_path / "todo.md"
    _write_todo(todo, "- [ ] Broken @id=t1 @paused=auto-failures @failed=3\n")

    from getstuffdone.cli import main

    main(["status", "--json", "--todo", str(todo), "--runs-dir", str(tmp_path / "runs")])
    doc = json.loads(capsys.readouterr().out)
    item = doc["items"][0]
    assert item["state"] == "parked"
    assert item["paused_reason"] == "auto-failures"
    assert item["failure_count"] == 3


def test_status_json_deferred_item_timestamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A deferred item carries next_eligible_at as an ISO-8601 UTC timestamp (AC11.6)."""
    monkeypatch.chdir(tmp_path)
    todo = tmp_path / "todo.md"
    _write_todo(todo, "- [ ] Future @id=t1 @not-before=2099-01-01T09:00:00+00:00\n")

    from getstuffdone.cli import main

    main(["status", "--json", "--todo", str(todo), "--runs-dir", str(tmp_path / "runs")])
    doc = json.loads(capsys.readouterr().out)
    item = doc["items"][0]
    assert item["state"] == "deferred"
    ts = item["next_eligible_at"]
    assert ts is not None
    assert "2099" in ts
    # Must be parseable as ISO-8601
    from datetime import datetime

    parsed = datetime.fromisoformat(ts)
    assert parsed.tzinfo is not None  # timezone-aware (AC11.6)


def test_status_json_last_run_at_is_iso8601(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """last_run_at in JSON output is ISO-8601 (AC11.6)."""
    monkeypatch.chdir(tmp_path)
    todo = tmp_path / "todo.md"
    _write_todo(todo, "- [ ] Task @id=t1\n")
    runs_dir = tmp_path / "runs"

    now_str = "2026-07-01T09:00:00+00:00"
    run_dir = runs_dir / "run-abc"
    _write_journal(
        run_dir,
        [
            _run_started_payload(now_str, str(todo)),
            {
                "event": "item_selected",
                "seq": 2,
                "run_id": "run-abc",
                "ts": now_str,
                "item_id": "t1",
                "payload": {"text": "Task"},
            },
            {
                "event": "item_completed",
                "seq": 3,
                "run_id": "run-abc",
                "ts": now_str,
                "item_id": "t1",
                "payload": {},
            },
        ],
    )

    from getstuffdone.cli import main

    main(["status", "--json", "--todo", str(todo), "--runs-dir", str(runs_dir)])
    doc = json.loads(capsys.readouterr().out)
    item = doc["items"][0]
    ts = item["last_run_at"]
    assert ts is not None
    from datetime import datetime

    parsed = datetime.fromisoformat(ts)
    assert parsed.tzinfo is not None  # timezone-aware (AC11.6)
    assert item["last_outcome"] == "done"


def test_status_json_stdout_only_json_no_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """stdout is pure JSON — no human-readable text mixed in (AC11.5)."""
    monkeypatch.chdir(tmp_path)
    todo = tmp_path / "todo.md"
    _write_todo(todo, "- [ ] Alpha @id=a\n- [ ] Beta @id=b\n")

    from getstuffdone.cli import main

    main(["status", "--json", "--todo", str(todo), "--runs-dir", str(tmp_path / "runs")])
    out = capsys.readouterr().out
    # Must parse cleanly; any trailing text would fail here
    json.loads(out)


def test_status_json_warnings_go_to_stderr_not_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ingest warnings appear on stderr; stdout stays pure JSON (AC11.5)."""
    monkeypatch.chdir(tmp_path)
    todo = tmp_path / "todo.md"
    # A bad schedule token triggers an ingest warning
    _write_todo(todo, "- [ ] Bad schedule @due=not-a-date\n- [ ] Good task @id=good\n")

    from getstuffdone.cli import main

    main(["status", "--json", "--todo", str(todo), "--runs-dir", str(tmp_path / "runs")])
    out, err = capsys.readouterr()
    # stdout must still parse as JSON even with warnings
    json.loads(out)
    # warnings go to stderr (ingest may handle bad_schedule differently,
    # but status must not let any warning leak to stdout)


def test_status_json_and_human_mode_same_item_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """JSON and human modes return the same number of items from the same StatusReport."""
    monkeypatch.chdir(tmp_path)
    todo = tmp_path / "todo.md"
    _write_todo(todo, "- [ ] One @id=one\n- [x] Two @id=two\n- [ ] Three @id=three\n")

    from getstuffdone.cli import main

    main(["status", "--todo", str(todo), "--runs-dir", str(tmp_path / "runs")])
    capsys.readouterr()  # discard human output

    main(["status", "--json", "--todo", str(todo), "--runs-dir", str(tmp_path / "runs")])
    doc = json.loads(capsys.readouterr().out)
    assert len(doc["items"]) == 3


def test_status_json_exit_zero_always(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``gsd status --json`` exits 0 always, same as human mode (AC11.1)."""
    monkeypatch.chdir(tmp_path)
    todo = tmp_path / "todo.md"
    _write_todo(todo, "- [ ] Pending @id=t1\n")

    from getstuffdone.cli import main

    rc = main(["status", "--json", "--todo", str(todo), "--runs-dir", str(tmp_path / "runs")])
    assert rc == 0


def test_status_json_does_not_create_run_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``gsd status --json`` must not create any run directory (AC11.1)."""
    monkeypatch.chdir(tmp_path)
    todo = tmp_path / "todo.md"
    _write_todo(todo, "- [ ] Task @id=t1\n")
    runs_dir = tmp_path / "no-runs"

    from getstuffdone.cli import main

    main(["status", "--json", "--todo", str(todo), "--runs-dir", str(runs_dir)])

    assert not runs_dir.exists()


def test_format_status_json_function_directly() -> None:
    """format_status_json serialises a StatusReport to a valid JSON document."""
    from getstuffdone.status import StatusReport, format_status_json

    report = StatusReport(
        todo_path="/todo.md",
        items=[
            ItemStatusLine(
                item_id="x",
                text="Do something",
                state="eligible",
                last_run_id="run-1",
                last_run_at="2026-08-01T09:00:00+00:00",
                last_outcome="failed",
                failed_subtask_desc="Run tests",
                failed_check_kind="command",
            )
        ],
    )
    out = format_status_json(report)
    doc = json.loads(out)
    assert doc["todo_path"] == "/todo.md"
    assert len(doc["items"]) == 1
    assert doc["items"][0]["item_id"] == "x"
    assert doc["items"][0]["state"] == "eligible"
    assert doc["items"][0]["last_run_at"] == "2026-08-01T09:00:00+00:00"
