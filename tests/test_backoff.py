# SPDX-License-Identifier: Apache-2.0
"""Tests for backoff.py — failure count advancement and parking.

Acceptance criteria exercised here (from IMPLEMENTATION_PLAN.md):

- Failure increments @failed= byte-preserving (whole-file diff is exactly one line).
- Reaching the threshold appends @paused=auto-failures.
- A @paused= item is excluded by select and printed as Parked with its reason.
- A parked item does not affect exit status but appears in report.md.
- A subsequent success removes @failed=.
- --dry-run leaves the file byte-identical.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from getstuffdone.backoff import BackoffError, advance_failure_count, clear_failure_count
from getstuffdone.config import Config
from getstuffdone.journal import (
    FAILURE_COUNT_ADVANCED,
    FAILURE_COUNT_CLEARED,
    ITEM_PARKED,
    RUN_FINISHED,
    RUN_STARTED,
    Journal,
)
from getstuffdone.models import Capability, ItemStatus, TodoItem
from getstuffdone.report import build_report, format_markdown, format_summary, report_exit_status
from getstuffdone.select import select_next

# ---------------------------------------------------------------------------
# Frozen test clock
# ---------------------------------------------------------------------------

NOW = datetime(2026, 8, 7, 12, 0, 0, tzinfo=UTC)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_item(
    item_id: str = "item-1",
    *,
    text: str = "My task",
    raw_line: str | None = None,
    line_no: int = 1,
    meta: dict[str, str] | None = None,
    status: ItemStatus = ItemStatus.pending,
) -> TodoItem:
    if raw_line is None:
        raw_line = f"- [ ] {text}\n"
        if meta:
            for k, v in meta.items():
                raw_line = raw_line.rstrip("\n") + f" @{k}={v}\n"
    return TodoItem(
        item_id=item_id,
        text=text,
        raw_line=raw_line,
        line_no=line_no,
        status=status,
        context=(),
        authored_subtasks=(),
        meta=meta or {},
        priority=None,
        depends=(),
        capabilities=frozenset({Capability.read_fs}),
        schedule=None,
    )


def _make_config(max_consecutive_failures: int = 3) -> Config:
    return Config(max_consecutive_failures=max_consecutive_failures)


def _make_journal(tmp_path: Path) -> Journal:
    run_dir = tmp_path / "runs" / "test-run"
    run_dir.mkdir(parents=True)
    return Journal(run_dir / "journal.jsonl", "test-run")


_RUN_STARTED_PAYLOAD = {
    "now": "2026-01-01T00:00:00Z",
    "tz": "UTC",
    "mode": "auto",
    "todo_path": "todo.md",
}


def _make_run_dir(tmp_path: Path, run_id: str = "rpt-run") -> Path:
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    return run_dir


# ---------------------------------------------------------------------------
# advance_failure_count — basic increment
# ---------------------------------------------------------------------------


def test_advance_adds_failed_token_when_absent(tmp_path: Path) -> None:
    """First failure: @failed=1 is appended to the item's line."""
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] My task\n")

    item = _make_item(raw_line="- [ ] My task\n")
    journal = _make_journal(tmp_path)

    advance_failure_count(todo, item, _make_config(), journal)

    content = todo.read_text()
    assert "@failed=1" in content


def test_advance_increments_existing_failed_token(tmp_path: Path) -> None:
    """Second failure: @failed=2 replaces @failed=1."""
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] My task @failed=1\n")

    item = _make_item(raw_line="- [ ] My task @failed=1\n", meta={"failed": "1"})
    journal = _make_journal(tmp_path)

    advance_failure_count(todo, item, _make_config(), journal)

    content = todo.read_text()
    assert "@failed=2" in content
    assert "@failed=1" not in content


def test_advance_byte_preserving_exactly_one_line_changed(tmp_path: Path) -> None:
    """The whole-file diff is exactly one line — everything else is byte-identical."""
    todo = tmp_path / "todo.md"
    todo.write_text("# My work\n\n- [ ] First task\n- [ ] Target task\n- [ ] Third task\n")
    before = todo.read_bytes().splitlines(keepends=True)

    item = _make_item(raw_line="- [ ] Target task\n", line_no=4)
    journal = _make_journal(tmp_path)

    advance_failure_count(todo, item, _make_config(), journal)

    after = todo.read_bytes().splitlines(keepends=True)
    assert len(before) == len(after)

    changed = [i for i in range(len(before)) if before[i] != after[i]]
    assert changed == [3], f"Expected line 4 (idx 3) to change; got {changed}"
    assert b"@failed=1" in after[3]

    # All other lines are byte-identical.
    for i, (b, a) in enumerate(zip(before, after, strict=True)):
        if i != 3:
            assert b == a, f"Line {i + 1} changed unexpectedly"


def test_advance_preserves_other_tokens(tmp_path: Path) -> None:
    """Other @tokens on the line are not altered."""
    todo = tmp_path / "todo.md"
    line = "- [ ] My task @priority=1 @due=2026-09-01\n"
    todo.write_text(line)

    item = _make_item(raw_line=line, meta={"priority": "1", "due": "2026-09-01"})
    journal = _make_journal(tmp_path)

    advance_failure_count(todo, item, _make_config(), journal)

    content = todo.read_text()
    assert "@priority=1" in content
    assert "@due=2026-09-01" in content
    assert "@failed=1" in content


# ---------------------------------------------------------------------------
# advance_failure_count — threshold and parking
# ---------------------------------------------------------------------------


def test_advance_parks_at_threshold(tmp_path: Path) -> None:
    """When new count == max_consecutive_failures, @paused=auto-failures is appended."""
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] My task @failed=2\n")

    item = _make_item(raw_line="- [ ] My task @failed=2\n", meta={"failed": "2"})
    journal = _make_journal(tmp_path)

    advance_failure_count(todo, item, _make_config(max_consecutive_failures=3), journal)

    content = todo.read_text()
    assert "@failed=3" in content
    assert "@paused=auto-failures" in content


def test_advance_parks_exactly_at_threshold(tmp_path: Path) -> None:
    """Parking occurs at the threshold, not before it."""
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] My task @failed=1\n")

    item = _make_item(raw_line="- [ ] My task @failed=1\n", meta={"failed": "1"})
    journal = _make_journal(tmp_path)

    # With threshold=3, count goes 1→2: not parked yet.
    advance_failure_count(todo, item, _make_config(max_consecutive_failures=3), journal)

    content = todo.read_text()
    assert "@failed=2" in content
    assert "@paused=" not in content


def test_advance_does_not_duplicate_paused_token(tmp_path: Path) -> None:
    """If @paused= is already on the line, a second @paused= is never appended."""
    todo = tmp_path / "todo.md"
    # Already parked from a previous run; somehow being advanced again.
    todo.write_text("- [ ] My task @failed=3 @paused=auto-failures\n")

    item = _make_item(
        raw_line="- [ ] My task @failed=3 @paused=auto-failures\n",
        meta={"failed": "3", "paused": "auto-failures"},
    )
    journal = _make_journal(tmp_path)

    advance_failure_count(todo, item, _make_config(max_consecutive_failures=3), journal)

    content = todo.read_text()
    # Count advances but no second @paused= is added.
    assert content.count("@paused=") == 1


def test_advance_threshold_journalled(tmp_path: Path) -> None:
    """The FAILURE_COUNT_ADVANCED event records parked=True when threshold is reached."""
    import json

    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] My task @failed=2\n")
    item = _make_item(raw_line="- [ ] My task @failed=2\n", meta={"failed": "2"})
    run_dir = tmp_path / "runs" / "test-run"
    run_dir.mkdir(parents=True)
    journal = Journal(run_dir / "journal.jsonl", "test-run")

    advance_failure_count(todo, item, _make_config(max_consecutive_failures=3), journal)

    entries = [json.loads(line) for line in (run_dir / "journal.jsonl").read_text().splitlines()]
    adv = next(e for e in entries if e["event"] == FAILURE_COUNT_ADVANCED)
    assert adv["payload"]["parked"] is True
    assert adv["payload"]["new_count"] == 3


# ---------------------------------------------------------------------------
# clear_failure_count — success removes @failed=
# ---------------------------------------------------------------------------


def test_clear_removes_failed_token(tmp_path: Path) -> None:
    """After a successful completion, @failed= is removed from the line."""
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] My task @failed=2\n")

    item = _make_item(raw_line="- [ ] My task @failed=2\n", meta={"failed": "2"})
    journal = _make_journal(tmp_path)

    clear_failure_count(todo, item, journal)

    content = todo.read_text()
    assert "@failed=" not in content


def test_clear_byte_preserving_exactly_one_line_changed(tmp_path: Path) -> None:
    """clear_failure_count changes only the target line."""
    todo = tmp_path / "todo.md"
    todo.write_text("# Work\n- [ ] Other task\n- [ ] Target task @failed=1\n")
    before = todo.read_bytes().splitlines(keepends=True)

    item = _make_item(raw_line="- [ ] Target task @failed=1\n", line_no=3, meta={"failed": "1"})
    journal = _make_journal(tmp_path)

    clear_failure_count(todo, item, journal)

    after = todo.read_bytes().splitlines(keepends=True)
    assert len(before) == len(after)
    changed = [i for i in range(len(before)) if before[i] != after[i]]
    assert changed == [2]
    assert b"@failed=" not in after[2]
    assert before[0] == after[0]
    assert before[1] == after[1]


def test_clear_noop_when_no_failed_token(tmp_path: Path) -> None:
    """clear_failure_count is a no-op when the item has no @failed= token."""
    todo = tmp_path / "todo.md"
    original = "- [ ] My task\n"
    todo.write_text(original)

    item = _make_item(raw_line=original)
    journal = _make_journal(tmp_path)

    clear_failure_count(todo, item, journal)

    assert todo.read_text() == original


def test_clear_does_not_remove_paused_token(tmp_path: Path) -> None:
    """Success clears @failed= but never touches @paused= (human removes that)."""
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] My task @failed=3 @paused=auto-failures\n")

    item = _make_item(
        raw_line="- [ ] My task @failed=3 @paused=auto-failures\n",
        meta={"failed": "3", "paused": "auto-failures"},
    )
    journal = _make_journal(tmp_path)

    clear_failure_count(todo, item, journal)

    content = todo.read_text()
    assert "@failed=" not in content
    assert "@paused=auto-failures" in content


def test_clear_journals_before_write(tmp_path: Path) -> None:
    """FAILURE_COUNT_CLEARED event is written to the journal."""
    import json

    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] My task @failed=2\n")
    item = _make_item(raw_line="- [ ] My task @failed=2\n", meta={"failed": "2"})

    run_dir = tmp_path / "runs" / "test-run"
    run_dir.mkdir(parents=True)
    journal = Journal(run_dir / "journal.jsonl", "test-run")

    clear_failure_count(todo, item, journal)

    entries = [json.loads(line) for line in (run_dir / "journal.jsonl").read_text().splitlines()]
    cleared = next((e for e in entries if e["event"] == FAILURE_COUNT_CLEARED), None)
    assert cleared is not None
    assert cleared["payload"]["cleared_count"] == 2


# ---------------------------------------------------------------------------
# --dry-run leaves the file byte-identical
# ---------------------------------------------------------------------------


def test_advance_dry_run_leaves_file_unchanged(tmp_path: Path) -> None:
    """advance_failure_count(dry_run=True) journals the intent but writes nothing."""
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] My task\n")
    original = todo.read_bytes()

    item = _make_item(raw_line="- [ ] My task\n")
    journal = _make_journal(tmp_path)

    advance_failure_count(todo, item, _make_config(), journal, dry_run=True)

    assert todo.read_bytes() == original


def test_advance_dry_run_still_journals(tmp_path: Path) -> None:
    """Even with dry_run=True, the FAILURE_COUNT_ADVANCED event is journalled."""
    import json

    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] My task\n")
    item = _make_item(raw_line="- [ ] My task\n")

    run_dir = tmp_path / "runs" / "test-run"
    run_dir.mkdir(parents=True)
    journal = Journal(run_dir / "journal.jsonl", "test-run")

    advance_failure_count(todo, item, _make_config(), journal, dry_run=True)

    entries = [json.loads(line) for line in (run_dir / "journal.jsonl").read_text().splitlines()]
    adv = next((e for e in entries if e["event"] == FAILURE_COUNT_ADVANCED), None)
    assert adv is not None


def test_clear_dry_run_leaves_file_unchanged(tmp_path: Path) -> None:
    """clear_failure_count(dry_run=True) journals the intent but writes nothing."""
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] My task @failed=1\n")
    original = todo.read_bytes()

    item = _make_item(raw_line="- [ ] My task @failed=1\n", meta={"failed": "1"})
    journal = _make_journal(tmp_path)

    clear_failure_count(todo, item, journal, dry_run=True)

    assert todo.read_bytes() == original


# ---------------------------------------------------------------------------
# select.py — @paused= items are excluded and reported as Parked
# ---------------------------------------------------------------------------


def test_select_excludes_paused_item(tmp_path: Path) -> None:
    """A @paused= item is never selected, regardless of other eligibility."""
    paused = _make_item("paused-item", meta={"paused": "auto-failures"})
    normal = _make_item("normal-item", line_no=2)

    result = select_next([paused, normal], NOW)

    assert result.selected is not None
    assert result.selected.item_id == "normal-item"


def test_select_parked_list_contains_reason(tmp_path: Path) -> None:
    """Parked items appear in SelectResult.parked with the @paused= reason."""
    paused = _make_item("paused-item", meta={"paused": "auto-failures"})

    result = select_next([paused], NOW)

    assert result.selected is None
    assert len(result.parked) == 1
    parked_item, reason = result.parked[0]
    assert parked_item.item_id == "paused-item"
    assert reason == "auto-failures"


def test_select_parked_not_in_other_categories(tmp_path: Path) -> None:
    """Parked items are NOT counted as deferred, blocked, or eligible."""
    paused = _make_item("paused-item", meta={"paused": "auto-failures"})

    result = select_next([paused], NOW)

    assert not result.deferred
    assert not result.blocked


def test_select_all_parked_returns_no_selected(tmp_path: Path) -> None:
    """When all items are parked, selected is None and parked is populated."""
    items = [
        _make_item("a", meta={"paused": "auto-failures"}, line_no=1),
        _make_item("b", meta={"paused": "some-reason"}, line_no=2),
    ]
    result = select_next(items, NOW)

    assert result.selected is None
    assert len(result.parked) == 2


def test_select_done_item_with_paused_not_counted_as_parked(tmp_path: Path) -> None:
    """A done item with @paused= is skipped as done, not added to parked."""
    done_item = _make_item("done-item", status=ItemStatus.done, meta={"paused": "auto-failures"})

    result = select_next([done_item], NOW)

    assert result.selected is None
    assert not result.parked  # done items are just done


# ---------------------------------------------------------------------------
# report.py — parked items in report.md, exit status unaffected
# ---------------------------------------------------------------------------


def test_report_shows_parked_items(tmp_path: Path) -> None:
    """item_parked events appear in the Parked section of the report."""
    run_dir = _make_run_dir(tmp_path)
    journal = Journal(run_dir / "journal.jsonl", run_dir.name)

    journal.append(RUN_STARTED, payload=_RUN_STARTED_PAYLOAD)
    journal.append(
        ITEM_PARKED,
        item_id="item-1",
        payload={"text": "Broken task", "reason": "auto-failures"},
    )
    journal.append(RUN_FINISHED, payload={"exit_status": 0})

    report = build_report(run_dir)
    md = format_markdown(report)
    summary = format_summary(report)

    assert "Parked" in md
    assert "Broken task" in md
    assert "auto-failures" in md
    assert "parked" in summary


def test_parked_item_does_not_affect_exit_status(tmp_path: Path) -> None:
    """A run with only parked items exits 0 (parked ≠ failed)."""
    run_dir = _make_run_dir(tmp_path)
    journal = Journal(run_dir / "journal.jsonl", run_dir.name)

    journal.append(RUN_STARTED, payload=_RUN_STARTED_PAYLOAD)
    journal.append(
        ITEM_PARKED,
        item_id="item-1",
        payload={"text": "Broken task", "reason": "auto-failures"},
    )
    journal.append(RUN_FINISHED, payload={"exit_status": 0})

    report = build_report(run_dir)
    assert report_exit_status(report) == 0


def test_parked_item_listed_alongside_done_items(tmp_path: Path) -> None:
    """A mix of done and parked items: done exits 0, parked is listed."""
    from getstuffdone.journal import ITEM_COMPLETED, ITEM_SELECTED

    run_dir = _make_run_dir(tmp_path)
    journal = Journal(run_dir / "journal.jsonl", run_dir.name)

    journal.append(RUN_STARTED, payload=_RUN_STARTED_PAYLOAD)
    journal.append(
        ITEM_SELECTED,
        item_id="done-item",
        payload={"text": "Done task", "is_overdue": False},
    )
    journal.append(ITEM_COMPLETED, item_id="done-item", payload={})
    journal.append(
        ITEM_PARKED,
        item_id="park-item",
        payload={"text": "Parked task", "reason": "auto-failures"},
    )
    journal.append(RUN_FINISHED, payload={"exit_status": 0})

    report = build_report(run_dir)
    md = format_markdown(report)

    assert "Done task" in md
    assert "Parked task" in md
    assert report_exit_status(report) == 0


# ---------------------------------------------------------------------------
# config.py — max_consecutive_failures default
# ---------------------------------------------------------------------------


def test_config_max_consecutive_failures_default(tmp_path: Path) -> None:
    """max_consecutive_failures defaults to 3."""
    from getstuffdone.config import load_config

    cfg = load_config(tmp_path)
    assert cfg.max_consecutive_failures == 3


def test_config_max_consecutive_failures_from_file(tmp_path: Path) -> None:
    """max_consecutive_failures can be set in gsd.toml."""
    from getstuffdone.config import load_config

    (tmp_path / "gsd.toml").write_text("[gsd]\nmax_consecutive_failures = 5\n")
    cfg = load_config(tmp_path)
    assert cfg.max_consecutive_failures == 5


def test_config_max_consecutive_failures_cli_override(tmp_path: Path) -> None:
    """max_consecutive_failures can be overridden via CLI overrides dict."""
    from getstuffdone.config import load_config

    cfg = load_config(tmp_path, cli_overrides={"max_consecutive_failures": 1})
    assert cfg.max_consecutive_failures == 1


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_advance_missing_file_raises(tmp_path: Path) -> None:
    """BackoffError is raised when the todo file cannot be read."""
    todo = tmp_path / "nonexistent.md"
    item = _make_item()
    journal = _make_journal(tmp_path)

    with pytest.raises(BackoffError, match="Cannot read"):
        advance_failure_count(todo, item, _make_config(), journal)


def test_advance_out_of_range_line_raises(tmp_path: Path) -> None:
    """BackoffError when line_no points outside the file."""
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] Only line\n")
    item = _make_item(line_no=99)
    journal = _make_journal(tmp_path)

    with pytest.raises(BackoffError, match="out of range"):
        advance_failure_count(todo, item, _make_config(), journal)


def test_clear_missing_file_raises(tmp_path: Path) -> None:
    """BackoffError is raised when the todo file cannot be read."""
    todo = tmp_path / "nonexistent.md"
    item = _make_item(meta={"failed": "1"})
    journal = _make_journal(tmp_path)

    with pytest.raises(BackoffError, match="Cannot read"):
        clear_failure_count(todo, item, journal)
