# SPDX-License-Identifier: Apache-2.0
"""Tests for schedule.py — Stage 10: Scheduled runs.

Acceptance criteria covered
----------------------------
AC10.1  schedule add --dry-run prints the entry and does NOT write to the backend.
AC10.2  An installed entry is wrapped in gsd markers; remove deletes only those
        lines and leaves unrelated user entries byte-identical (round-trip).
AC10.3  The installed command contains absolute paths for gsd and the todo file,
        and carries --non-interactive.
AC10.4  An invalid cron expression is rejected with a message naming the field,
        and nothing is installed.
AC10.7  list_schedules reports only gsd-owned entries with id, cron expr,
        resolved timezone, and target todo path.
AC10.8  Removing an id that does not exist is a clean non-zero error.
AC10.9  The resolved timezone is recorded on the entry and printed at install time.
Round-trip  A fixture crontab containing unrelated entries survives add+remove
            byte-identical.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from getstuffdone.schedule import (
    CronExprError,
    FakeBackend,
    ScheduleEntry,
    _insert_block,
    _parse_blocks,
    _remove_block,
    add_schedule,
    list_schedules,
    remove_schedule,
    show_entry,
    validate_cron,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIXTURE_CRONTAB = """\
# User crontab — do not edit by hand
0 5 * * * /usr/local/bin/backup.sh >> /var/log/backup.log 2>&1

30 7 * * 1 /home/user/weekly.sh
"""

_GSD = "/usr/local/bin/gsd"
_TODO = "/home/user/todo.md"


def _make_entry(
    schedule_id: str = "abc12345",
    cron_expr: str = "0 9 * * 1-5",
    todo_path: str = _TODO,
    gsd_path: str = _GSD,
    timezone: str = "America/Los_Angeles",
    config_path: str | None = None,
) -> ScheduleEntry:
    return ScheduleEntry(
        schedule_id=schedule_id,
        cron_expr=cron_expr,
        todo_path=todo_path,
        gsd_path=gsd_path,
        config_path=config_path,
        timezone=timezone,
    )


# ---------------------------------------------------------------------------
# Cron expression validation (AC10.4)
# ---------------------------------------------------------------------------


class TestValidateCron:
    def test_valid_expressions(self) -> None:
        for expr in [
            "0 9 * * 1-5",
            "*/5 * * * *",
            "0 0 1 1 0",
            "30 7 * * mon",
            "0 9 * * 1,3,5",
            "0 0 * * sun",
            "0 0 1 jan *",
        ]:
            validate_cron(expr)  # must not raise

    def test_wrong_field_count(self) -> None:
        with pytest.raises(CronExprError) as exc_info:
            validate_cron("0 9 * *")
        assert exc_info.value.field == "expression"

    def test_minute_out_of_range(self) -> None:
        with pytest.raises(CronExprError) as exc_info:
            validate_cron("60 9 * * *")
        assert exc_info.value.field == "minute"
        assert "60" in str(exc_info.value)

    def test_hour_out_of_range(self) -> None:
        with pytest.raises(CronExprError) as exc_info:
            validate_cron("0 24 * * *")
        assert exc_info.value.field == "hour"

    def test_dom_out_of_range(self) -> None:
        with pytest.raises(CronExprError) as exc_info:
            validate_cron("0 9 32 * *")
        assert exc_info.value.field == "day-of-month"

    def test_month_out_of_range(self) -> None:
        with pytest.raises(CronExprError) as exc_info:
            validate_cron("0 9 * 13 *")
        assert exc_info.value.field == "month"

    def test_dow_out_of_range(self) -> None:
        with pytest.raises(CronExprError) as exc_info:
            validate_cron("0 9 * * 8")
        assert exc_info.value.field == "day-of-week"

    def test_invalid_step(self) -> None:
        with pytest.raises(CronExprError) as exc_info:
            validate_cron("*/abc * * * *")
        assert exc_info.value.field == "minute"

    def test_zero_step(self) -> None:
        with pytest.raises(CronExprError) as exc_info:
            validate_cron("*/0 * * * *")
        assert exc_info.value.field == "minute"

    def test_range_inverted(self) -> None:
        with pytest.raises(CronExprError) as exc_info:
            validate_cron("0 9 * * 5-1")
        assert exc_info.value.field == "day-of-week"

    def test_invalid_name(self) -> None:
        with pytest.raises(CronExprError) as exc_info:
            validate_cron("0 9 * * badday")
        assert exc_info.value.field == "day-of-week"


# ---------------------------------------------------------------------------
# Cron block text manipulation (AC10.2)
# ---------------------------------------------------------------------------


class TestCronBlockManipulation:
    def test_insert_creates_markers(self) -> None:
        entry = _make_entry()
        text = _insert_block("", entry)
        assert f"# >>> gsd {entry.schedule_id} >>>" in text
        assert f"# <<< gsd {entry.schedule_id} <<<" in text

    def test_insert_contains_command_and_cron(self) -> None:
        entry = _make_entry()
        text = _insert_block("", entry)
        assert entry.cron_expr in text
        assert entry.gsd_path in text
        assert entry.todo_path in text
        assert "--non-interactive" in text

    def test_insert_preserves_unrelated_lines(self) -> None:
        entry = _make_entry()
        initial = "# existing user entry\n0 5 * * * /usr/bin/backup.sh\n"
        text = _insert_block(initial, entry)
        assert "# existing user entry" in text
        assert "/usr/bin/backup.sh" in text

    def test_remove_block_removes_own_lines(self) -> None:
        entry = _make_entry()
        text = _insert_block("", entry)
        assert entry.schedule_id in text
        new_text, found = _remove_block(text, entry.schedule_id)
        assert found
        assert entry.schedule_id not in new_text

    def test_remove_block_returns_false_if_not_found(self) -> None:
        _, found = _remove_block("# nothing here\n", "does-not-exist")
        assert not found

    def test_remove_does_not_touch_unrelated_lines(self) -> None:
        entry = _make_entry()
        initial = "# keep this\n0 5 * * * /usr/bin/backup.sh\n"
        with_block = _insert_block(initial, entry)
        restored, found = _remove_block(with_block, entry.schedule_id)
        assert found
        assert "# keep this" in restored
        assert "/usr/bin/backup.sh" in restored


class TestRoundTrip:
    """AC10.2: fixture crontab with unrelated entries is byte-identical after add+remove."""

    def test_add_then_remove_restores_exact_text(self) -> None:
        entry = _make_entry()
        with_block = _insert_block(_FIXTURE_CRONTAB, entry)
        restored, found = _remove_block(with_block, entry.schedule_id)
        assert found
        assert restored == _FIXTURE_CRONTAB

    def test_second_add_replaces_existing_block(self) -> None:
        entry = _make_entry(cron_expr="0 9 * * *")
        entry2 = _make_entry(cron_expr="0 10 * * *")  # same id, different expr
        text = _insert_block("", entry)
        text2 = _insert_block(text, entry2)
        assert text2.count(f"# >>> gsd {entry.schedule_id}") == 1
        assert "0 10 * * *" in text2

    def test_multiple_entries_independent(self) -> None:
        e1 = _make_entry(schedule_id="aaaa1111", cron_expr="0 8 * * *")
        e2 = _make_entry(schedule_id="bbbb2222", cron_expr="0 9 * * *")
        text = _insert_block(_FIXTURE_CRONTAB, e1)
        text = _insert_block(text, e2)
        # Remove e1; e2 must still be present
        text_no_e1, _ = _remove_block(text, e1.schedule_id)
        assert e1.schedule_id not in text_no_e1
        assert e2.schedule_id in text_no_e1
        # Remove e2 as well; should be back to original
        text_no_e2, _ = _remove_block(text_no_e1, e2.schedule_id)
        assert text_no_e2 == _FIXTURE_CRONTAB


# ---------------------------------------------------------------------------
# parse_blocks (list backing)
# ---------------------------------------------------------------------------


class TestParseBlocks:
    def test_empty_text_returns_empty(self) -> None:
        assert _parse_blocks("") == []

    def test_parse_inserted_entry(self) -> None:
        entry = _make_entry(timezone="Europe/London")
        text = _insert_block("", entry)
        entries = _parse_blocks(text)
        assert len(entries) == 1
        e = entries[0]
        assert e.schedule_id == entry.schedule_id
        assert e.cron_expr == entry.cron_expr
        assert e.todo_path == entry.todo_path
        assert e.timezone == "Europe/London"

    def test_ignores_non_gsd_lines(self) -> None:
        entries = _parse_blocks(_FIXTURE_CRONTAB)
        assert entries == []

    def test_parse_multiple_entries(self) -> None:
        e1 = _make_entry(schedule_id="aaaa1111")
        e2 = _make_entry(schedule_id="bbbb2222")
        text = _insert_block(_insert_block("", e1), e2)
        entries = _parse_blocks(text)
        ids = {e.schedule_id for e in entries}
        assert ids == {"aaaa1111", "bbbb2222"}


# ---------------------------------------------------------------------------
# FakeBackend (used for all public-API tests)
# ---------------------------------------------------------------------------


class TestFakeBackend:
    def test_add_and_list(self) -> None:
        backend = FakeBackend()
        entry = _make_entry()
        backend.add_entry(entry)
        entries = backend.list_entries()
        assert len(entries) == 1
        assert entries[0].schedule_id == entry.schedule_id

    def test_remove_returns_true_if_found(self) -> None:
        backend = FakeBackend()
        entry = _make_entry()
        backend.add_entry(entry)
        assert backend.remove_entry(entry.schedule_id)
        assert backend.list_entries() == []

    def test_remove_returns_false_if_not_found(self) -> None:
        backend = FakeBackend()
        assert not backend.remove_entry("nonexistent")

    def test_fail_on_write_raises(self) -> None:
        backend = FakeBackend(fail_on_write=True)
        with pytest.raises(AssertionError):
            backend.add_entry(_make_entry())

    def test_initial_text_preserved_through_add(self) -> None:
        backend = FakeBackend(initial_text=_FIXTURE_CRONTAB)
        entry = _make_entry()
        backend.add_entry(entry)
        assert "backup.sh" in backend.current_text
        assert entry.schedule_id in backend.current_text


# ---------------------------------------------------------------------------
# add_schedule (AC10.1, AC10.3, AC10.4, AC10.9)
# ---------------------------------------------------------------------------


class TestAddSchedule:
    def test_dry_run_does_not_write(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC10.1: dry-run prints the entry, never calls backend.add_entry."""
        backend = FakeBackend(fail_on_write=True)
        todo = tmp_path / "todo.md"
        todo.write_text("")
        add_schedule(
            "0 9 * * 1-5",
            todo_path=todo,
            timezone="UTC",
            backend=backend,
            gsd_path=_GSD,
            dry_run=True,
        )
        # fail_on_write would have raised if add_entry was called
        assert backend.write_calls == 0
        out = capsys.readouterr().out
        assert "0 9 * * 1-5" in out

    def test_non_dry_run_writes(self, tmp_path: Path) -> None:
        backend = FakeBackend()
        todo = tmp_path / "todo.md"
        todo.write_text("")
        add_schedule(
            "0 9 * * *",
            todo_path=todo,
            timezone="UTC",
            backend=backend,
            gsd_path=_GSD,
            dry_run=False,
        )
        assert backend.write_calls == 1
        assert len(backend.list_entries()) == 1

    def test_absolute_todo_path_in_command(self, tmp_path: Path) -> None:
        """AC10.3: installed command contains absolute path for todo file."""
        backend = FakeBackend()
        todo = tmp_path / "todo.md"
        todo.write_text("")
        entry = add_schedule(
            "0 9 * * *",
            todo_path=todo,
            timezone="UTC",
            backend=backend,
            gsd_path=_GSD,
        )
        assert Path(entry.todo_path).is_absolute()
        # Check the crontab line too
        text = backend.current_text
        assert entry.todo_path in text

    def test_non_interactive_in_command(self, tmp_path: Path) -> None:
        """AC10.3: installed command carries --non-interactive."""
        backend = FakeBackend()
        todo = tmp_path / "todo.md"
        todo.write_text("")
        add_schedule(
            "0 9 * * *",
            todo_path=todo,
            timezone="UTC",
            backend=backend,
            gsd_path=_GSD,
        )
        text = backend.current_text
        assert "--non-interactive" in text

    def test_gsd_path_absolute(self, tmp_path: Path) -> None:
        """AC10.3: installed command carries absolute gsd path."""
        backend = FakeBackend()
        todo = tmp_path / "todo.md"
        todo.write_text("")
        entry = add_schedule(
            "0 9 * * *",
            todo_path=todo,
            timezone="UTC",
            backend=backend,
            gsd_path=_GSD,
        )
        assert Path(entry.gsd_path).is_absolute()

    def test_invalid_cron_raises_before_write(self, tmp_path: Path) -> None:
        """AC10.4: invalid cron rejected with field info; nothing installed."""
        backend = FakeBackend()
        todo = tmp_path / "todo.md"
        todo.write_text("")
        with pytest.raises(CronExprError) as exc_info:
            add_schedule(
                "99 9 * * *",  # minute 99 is invalid
                todo_path=todo,
                timezone="UTC",
                backend=backend,
                gsd_path=_GSD,
            )
        assert exc_info.value.field == "minute"
        assert backend.write_calls == 0  # nothing written

    def test_timezone_in_entry_and_output(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC10.9: resolved timezone recorded on entry and printed at install time."""
        backend = FakeBackend()
        todo = tmp_path / "todo.md"
        todo.write_text("")
        entry = add_schedule(
            "0 9 * * *",
            todo_path=todo,
            timezone="Pacific/Auckland",
            backend=backend,
            gsd_path=_GSD,
        )
        assert entry.timezone == "Pacific/Auckland"
        out = capsys.readouterr().out
        assert "Pacific/Auckland" in out

    def test_deterministic_schedule_id(self, tmp_path: Path) -> None:
        backend = FakeBackend()
        todo = tmp_path / "todo.md"
        todo.write_text("")
        entry = add_schedule(
            "0 9 * * *",
            todo_path=todo,
            timezone="UTC",
            backend=backend,
            gsd_path=_GSD,
            schedule_id="myfixedid",
        )
        assert entry.schedule_id == "myfixedid"
        assert backend.list_entries()[0].schedule_id == "myfixedid"

    def test_config_path_in_command(self, tmp_path: Path) -> None:
        """AC10.3: config path included in command when provided."""
        backend = FakeBackend()
        todo = tmp_path / "todo.md"
        todo.write_text("")
        cfg = tmp_path / "gsd.toml"
        cfg.write_text("")
        add_schedule(
            "0 9 * * *",
            todo_path=todo,
            config_path=cfg,
            timezone="UTC",
            backend=backend,
            gsd_path=_GSD,
        )
        text = backend.current_text
        assert str(cfg.resolve()) in text


# ---------------------------------------------------------------------------
# list_schedules (AC10.7)
# ---------------------------------------------------------------------------


class TestListSchedules:
    def test_empty_backend_returns_empty(self) -> None:
        """AC10.7: list with no entries returns empty list."""
        backend = FakeBackend()
        assert list_schedules(backend) == []

    def test_returns_only_gsd_entries(self) -> None:
        """AC10.7: list ignores non-gsd crontab lines."""
        backend = FakeBackend(initial_text=_FIXTURE_CRONTAB)
        assert list_schedules(backend) == []

    def test_list_returns_correct_fields(self, tmp_path: Path) -> None:
        """AC10.7: id, cron expr, tz, and todo path are all present."""
        backend = FakeBackend()
        todo = tmp_path / "todo.md"
        todo.write_text("")
        add_schedule(
            "30 7 * * 1",
            todo_path=todo,
            timezone="Europe/Berlin",
            backend=backend,
            gsd_path=_GSD,
            schedule_id="berlin01",
        )
        entries = list_schedules(backend)
        assert len(entries) == 1
        e = entries[0]
        assert e.schedule_id == "berlin01"
        assert e.cron_expr == "30 7 * * 1"
        assert e.timezone == "Europe/Berlin"
        assert e.todo_path == str(todo.resolve())

    def test_lists_multiple_entries(self, tmp_path: Path) -> None:
        backend = FakeBackend()
        todo = tmp_path / "todo.md"
        todo.write_text("")
        add_schedule(
            "0 8 * * *",
            todo_path=todo,
            timezone="UTC",
            backend=backend,
            gsd_path=_GSD,
            schedule_id="s1",
        )
        add_schedule(
            "0 9 * * *",
            todo_path=todo,
            timezone="UTC",
            backend=backend,
            gsd_path=_GSD,
            schedule_id="s2",
        )
        entries = list_schedules(backend)
        ids = {e.schedule_id for e in entries}
        assert ids == {"s1", "s2"}


# ---------------------------------------------------------------------------
# remove_schedule (AC10.8)
# ---------------------------------------------------------------------------


class TestRemoveSchedule:
    def test_remove_existing_entry(self, tmp_path: Path) -> None:
        backend = FakeBackend()
        todo = tmp_path / "todo.md"
        todo.write_text("")
        add_schedule(
            "0 9 * * *",
            todo_path=todo,
            timezone="UTC",
            backend=backend,
            gsd_path=_GSD,
            schedule_id="rem1",
        )
        assert remove_schedule("rem1", backend)
        assert list_schedules(backend) == []

    def test_remove_nonexistent_returns_false(self) -> None:
        """AC10.8: removing a non-existent id returns False (caller maps to exit 1)."""
        backend = FakeBackend()
        assert not remove_schedule("does-not-exist", backend)

    def test_remove_only_own_block(self) -> None:
        """AC10.2: remove deletes only the target block, not other entries."""
        backend = FakeBackend(initial_text=_FIXTURE_CRONTAB)
        e1 = _make_entry(schedule_id="keep1111")
        e2 = _make_entry(schedule_id="gone2222")
        backend.add_entry(e1)
        backend.add_entry(e2)
        remove_schedule("gone2222", backend)
        remaining = list_schedules(backend)
        assert len(remaining) == 1
        assert remaining[0].schedule_id == "keep1111"
        # Original crontab content untouched
        assert "backup.sh" in backend.current_text


# ---------------------------------------------------------------------------
# show_entry
# ---------------------------------------------------------------------------


class TestShowEntry:
    def test_show_entry_contains_key_fields(self) -> None:
        entry = _make_entry(timezone="Asia/Tokyo")
        text = show_entry(entry)
        assert entry.schedule_id in text
        assert entry.cron_expr in text
        assert entry.todo_path in text
        assert "Asia/Tokyo" in text
        assert "--non-interactive" in text

    def test_show_entry_contains_gsd_markers(self) -> None:
        entry = _make_entry()
        text = show_entry(entry)
        assert f"# >>> gsd {entry.schedule_id} >>>" in text
        assert f"# <<< gsd {entry.schedule_id} <<<" in text
