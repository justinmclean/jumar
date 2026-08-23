# SPDX-License-Identifier: Apache-2.0
"""Tests for schedule.py — Stage 10: Scheduled runs.

Acceptance criteria covered
----------------------------
AC10.1  schedule add --dry-run prints the entry and does NOT write to the backend.
AC10.2  An installed entry is wrapped in jumar markers; remove deletes only those
        lines and leaves unrelated user entries byte-identical (round-trip).
AC10.3  The installed command contains absolute paths for jumar and the todo file,
        and carries --non-interactive.
AC10.4  An invalid cron expression is rejected with a message naming the field,
        and nothing is installed.
AC10.7  list_schedules reports only jumar-owned entries with id, cron expr,
        resolved timezone, and target todo path.
AC10.8  Removing an id that does not exist is a clean non-zero error.
AC10.9  The resolved timezone is recorded on the entry and printed at install time.
Round-trip  A fixture crontab containing unrelated entries survives add+remove
            byte-identical.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jumar import schedule as schedule_module
from jumar.schedule import (
    CronBackend,
    CronExprError,
    FakeBackend,
    LaunchdBackend,
    ScheduleEntry,
    SystemdBackend,
    _autodetect_backend_name,
    _cron_to_launchd_sci,
    _cron_to_oncalendar,
    _expand_cron_field,
    _insert_block,
    _jumar_executable,
    _oncalendar_lines,
    _parse_blocks,
    _remove_block,
    _systemd_user_available,
    add_schedule,
    default_backend,
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

_GSD = "/usr/local/bin/jumar"
_TODO = "/home/user/todo.md"


def _make_entry(
    schedule_id: str = "abc12345",
    cron_expr: str = "0 9 * * 1-5",
    todo_path: str = _TODO,
    jumar_path: str = _GSD,
    timezone: str = "America/Los_Angeles",
    config_path: str | None = None,
) -> ScheduleEntry:
    return ScheduleEntry(
        schedule_id=schedule_id,
        cron_expr=cron_expr,
        todo_path=todo_path,
        jumar_path=jumar_path,
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
        assert f"# >>> jumar {entry.schedule_id} >>>" in text
        assert f"# <<< jumar {entry.schedule_id} <<<" in text

    def test_insert_contains_command_and_cron(self) -> None:
        entry = _make_entry()
        text = _insert_block("", entry)
        assert entry.cron_expr in text
        assert entry.jumar_path in text
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
        assert text2.count(f"# >>> jumar {entry.schedule_id}") == 1
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

    def test_ignores_non_jumar_lines(self) -> None:
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
            jumar_path=_GSD,
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
            jumar_path=_GSD,
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
            jumar_path=_GSD,
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
            jumar_path=_GSD,
        )
        text = backend.current_text
        assert "--non-interactive" in text

    def test_jumar_path_absolute(self, tmp_path: Path) -> None:
        """AC10.3: installed command carries absolute jumar path."""
        backend = FakeBackend()
        todo = tmp_path / "todo.md"
        todo.write_text("")
        entry = add_schedule(
            "0 9 * * *",
            todo_path=todo,
            timezone="UTC",
            backend=backend,
            jumar_path=_GSD,
        )
        assert Path(entry.jumar_path).is_absolute()

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
                jumar_path=_GSD,
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
            jumar_path=_GSD,
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
            jumar_path=_GSD,
            schedule_id="myfixedid",
        )
        assert entry.schedule_id == "myfixedid"
        assert backend.list_entries()[0].schedule_id == "myfixedid"

    def test_config_path_in_command(self, tmp_path: Path) -> None:
        """AC10.3: config path included in command when provided."""
        backend = FakeBackend()
        todo = tmp_path / "todo.md"
        todo.write_text("")
        cfg = tmp_path / "jumar.toml"
        cfg.write_text("")
        add_schedule(
            "0 9 * * *",
            todo_path=todo,
            config_path=cfg,
            timezone="UTC",
            backend=backend,
            jumar_path=_GSD,
        )
        text = backend.current_text
        assert str(cfg.resolve()) in text

    def test_work_dir_defaults_to_todo_parent(self, tmp_path: Path) -> None:
        """W8: with no --config, the entry's work_dir is the todo file's
        directory, so a scheduled run's cwd-relative jumar.toml lookup and
        `runs/` creation land next to the todo file, not wherever the
        scheduler's own default cwd happens to be."""
        backend = FakeBackend()
        sub = tmp_path / "project"
        sub.mkdir()
        todo = sub / "todo.md"
        todo.write_text("")
        entry = add_schedule(
            "0 9 * * *",
            todo_path=todo,
            timezone="UTC",
            backend=backend,
            jumar_path=_GSD,
        )
        assert entry.work_dir == str(sub.resolve())

    def test_work_dir_prefers_config_parent(self, tmp_path: Path) -> None:
        """W8: when --config is given, work_dir follows the config file's
        directory rather than the todo file's — the config is the more
        specific statement of "where this invocation belongs"."""
        backend = FakeBackend()
        todo_dir = tmp_path / "todos"
        todo_dir.mkdir()
        todo = todo_dir / "todo.md"
        todo.write_text("")
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        cfg = cfg_dir / "jumar.toml"
        cfg.write_text("")
        entry = add_schedule(
            "0 9 * * *",
            todo_path=todo,
            config_path=cfg,
            timezone="UTC",
            backend=backend,
            jumar_path=_GSD,
        )
        assert entry.work_dir == str(cfg_dir.resolve())


# ---------------------------------------------------------------------------
# _build_command parses clean through cli.py's own argparse (W8 regression)
# ---------------------------------------------------------------------------


class TestBuildCommandParsesThroughCli:
    def test_installed_argv_is_valid_run_argv(self, tmp_path: Path) -> None:
        """W8: `_build_command()` appended --config to `jumar run`, but
        `run_p` had no --config argument — every firing of a schedule entry
        installed with a config path exited 2 with "unrecognized arguments".
        This asserts the exact argv installed by a backend parses clean."""
        from jumar.cli import build_parser
        from jumar.schedule import _build_command

        cfg = tmp_path / "jumar.toml"
        cfg.write_text("")
        entry = _make_entry(config_path=str(cfg))
        argv = _build_command(entry)

        args = build_parser().parse_args(argv[1:])  # argv[0] is the jumar path itself

        assert args.command == "run"
        assert args.todo == entry.todo_path
        assert args.config == str(cfg)
        assert args.non_interactive is True

    def test_installed_argv_without_config_still_parses(self) -> None:
        from jumar.cli import build_parser
        from jumar.schedule import _build_command

        entry = _make_entry(config_path=None)
        argv = _build_command(entry)

        args = build_parser().parse_args(argv[1:])

        assert args.command == "run"
        assert args.config is None


# ---------------------------------------------------------------------------
# Working-directory pinning per backend (W8)
# ---------------------------------------------------------------------------


class TestWorkDirPinning:
    def test_cron_line_cds_into_work_dir(self) -> None:
        entry = _make_entry(config_path=None)
        entry.work_dir = "/home/user/project"
        text = show_entry(entry)
        assert "cd /home/user/project &&" in text
        assert f"cd /home/user/project && {entry.jumar_path}" in text

    def test_cron_line_omits_cd_when_no_work_dir(self) -> None:
        entry = _make_entry(config_path=None)
        entry.work_dir = ""
        text = show_entry(entry)
        assert "cd " not in text.split("--- crontab entry")[1]

    def test_cron_work_dir_quoted_for_spaces(self) -> None:
        entry = _make_entry(config_path=None)
        entry.work_dir = "/home/user/my project"
        text = show_entry(entry)
        assert "cd '/home/user/my project' &&" in text

    def test_cron_round_trips_work_dir(self) -> None:
        entry = _make_entry(config_path=None)
        entry.work_dir = "/home/user/project"
        text = _insert_block("", entry)
        parsed = _parse_blocks(text)
        assert len(parsed) == 1
        assert parsed[0].work_dir == "/home/user/project"

    def test_launchd_sets_working_directory(self, tmp_path: Path) -> None:
        backend = LaunchdBackend(agents_dir=tmp_path)
        entry = _make_entry(config_path=None)
        entry.work_dir = "/home/user/project"
        backend.add_entry(entry)

        import plistlib

        plist_path = tmp_path / f"com.jumar.{entry.schedule_id}.plist"
        with plist_path.open("rb") as fh:
            data = plistlib.load(fh)
        assert data["WorkingDirectory"] == "/home/user/project"

        listed = backend.list_entries()
        assert listed[0].work_dir == "/home/user/project"

    def test_launchd_omits_working_directory_when_unset(self, tmp_path: Path) -> None:
        backend = LaunchdBackend(agents_dir=tmp_path)
        entry = _make_entry(config_path=None)
        entry.work_dir = ""
        backend.add_entry(entry)

        import plistlib

        plist_path = tmp_path / f"com.jumar.{entry.schedule_id}.plist"
        with plist_path.open("rb") as fh:
            data = plistlib.load(fh)
        assert "WorkingDirectory" not in data

    def test_systemd_sets_working_directory(self, tmp_path: Path) -> None:
        backend = SystemdBackend(units_dir=tmp_path)
        entry = _make_entry(config_path=None)
        entry.work_dir = "/home/user/project"
        backend.add_entry(entry)

        service_text = (tmp_path / f"jumar-{entry.schedule_id}.service").read_text()
        assert "WorkingDirectory=/home/user/project" in service_text

        listed = backend.list_entries()
        assert listed[0].work_dir == "/home/user/project"

    def test_systemd_omits_working_directory_when_unset(self, tmp_path: Path) -> None:
        backend = SystemdBackend(units_dir=tmp_path)
        entry = _make_entry(config_path=None)
        entry.work_dir = ""
        backend.add_entry(entry)

        service_text = (tmp_path / f"jumar-{entry.schedule_id}.service").read_text()
        assert "WorkingDirectory=" not in service_text


# ---------------------------------------------------------------------------
# list_schedules (AC10.7)
# ---------------------------------------------------------------------------


class TestListSchedules:
    def test_empty_backend_returns_empty(self) -> None:
        """AC10.7: list with no entries returns empty list."""
        backend = FakeBackend()
        assert list_schedules(backend) == []

    def test_returns_only_jumar_entries(self) -> None:
        """AC10.7: list ignores non-jumar crontab lines."""
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
            jumar_path=_GSD,
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
            jumar_path=_GSD,
            schedule_id="s1",
        )
        add_schedule(
            "0 9 * * *",
            todo_path=todo,
            timezone="UTC",
            backend=backend,
            jumar_path=_GSD,
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
            jumar_path=_GSD,
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

    def test_show_entry_contains_jumar_markers(self) -> None:
        entry = _make_entry()
        text = show_entry(entry)
        assert f"# >>> jumar {entry.schedule_id} >>>" in text
        assert f"# <<< jumar {entry.schedule_id} <<<" in text


# ---------------------------------------------------------------------------
# _validate_cron_field additional branches (step-over-range, plain-base-step)
# ---------------------------------------------------------------------------


class TestValidateCronFieldBranches:
    def test_step_over_range(self) -> None:
        """Step notation with a range base: 1-5/2 — covers the '-' in base branch."""
        validate_cron("1-5/2 * * * *")  # must not raise

    def test_step_over_plain_base(self) -> None:
        """Step notation with a plain integer base: 1/2."""
        validate_cron("1/2 * * * *")  # must not raise


# ---------------------------------------------------------------------------
# _insert_block: text with no trailing newline (line 202)
# ---------------------------------------------------------------------------


class TestInsertBlockNoNewline:
    def test_no_trailing_newline_gets_separator(self) -> None:
        """A current_text without a trailing newline must get one inserted."""
        entry = _make_entry()
        text = _insert_block("existing line without newline", entry)
        assert "existing line without newline" in text
        assert entry.schedule_id in text


# ---------------------------------------------------------------------------
# FakeBackend.name() and CronBackend.name()
# ---------------------------------------------------------------------------


class TestBackendNames:
    def test_fake_backend_name(self) -> None:
        assert FakeBackend().name() == "fake"

    def test_cron_backend_name(self) -> None:
        assert CronBackend().name() == "cron"


# ---------------------------------------------------------------------------
# _expand_cron_field
# ---------------------------------------------------------------------------


class TestExpandCronField:
    def test_wildcard_returns_none(self) -> None:
        assert _expand_cron_field("*", 0, 59, "minute") == [None]

    def test_step_from_wildcard(self) -> None:
        result = _expand_cron_field("*/15", 0, 59, "minute")
        assert sorted(result) == [0, 15, 30, 45]

    def test_step_from_range(self) -> None:
        result = _expand_cron_field("1-5/2", 0, 59, "minute")
        assert sorted(result) == [1, 3, 5]

    def test_step_from_plain_base(self) -> None:
        result = _expand_cron_field("10/20", 0, 59, "minute")
        assert sorted(result) == [10]

    def test_range(self) -> None:
        result = _expand_cron_field("1-3", 0, 59, "minute")
        assert sorted(result) == [1, 2, 3]

    def test_list(self) -> None:
        result = _expand_cron_field("1,3,5", 0, 59, "minute")
        assert sorted(result) == [1, 3, 5]

    def test_dow_alias(self) -> None:
        result = _expand_cron_field("mon", 0, 7, "day-of-week")
        assert result == [1]

    def test_month_alias(self) -> None:
        result = _expand_cron_field("jan", 1, 12, "month")
        assert result == [1]

    def test_plain_integer(self) -> None:
        result = _expand_cron_field("5", 0, 59, "minute")
        assert result == [5]


# ---------------------------------------------------------------------------
# _cron_to_launchd_sci
# ---------------------------------------------------------------------------


class TestCronToLaunchdSci:
    def test_specific_time_weekdays(self) -> None:
        sci = _cron_to_launchd_sci("0 9 * * 1-5")
        assert len(sci) == 5
        for e in sci:
            assert e.get("Minute") == 0
            assert e.get("Hour") == 9
            assert e.get("Weekday") in range(1, 6)

    def test_every_day_specific_time(self) -> None:
        sci = _cron_to_launchd_sci("30 8 * * *")
        assert len(sci) == 1
        assert sci[0]["Minute"] == 30
        assert sci[0]["Hour"] == 8

    def test_first_of_month(self) -> None:
        sci = _cron_to_launchd_sci("0 0 1 * *")
        assert len(sci) == 1
        assert sci[0]["Minute"] == 0
        assert sci[0]["Hour"] == 0
        assert sci[0]["Day"] == 1

    def test_every_15_minutes(self) -> None:
        sci = _cron_to_launchd_sci("*/15 * * * *")
        assert len(sci) == 4
        minutes = sorted(e["Minute"] for e in sci)
        assert minutes == [0, 15, 30, 45]

    def test_invalid_expression_returns_empty_dict(self) -> None:
        sci = _cron_to_launchd_sci("not valid")
        assert sci == [{}]


# ---------------------------------------------------------------------------
# LaunchdBackend
# ---------------------------------------------------------------------------


class TestLaunchdBackend:
    def test_add_creates_plist(self, tmp_path: Path) -> None:
        backend = LaunchdBackend(agents_dir=tmp_path)
        entry = _make_entry()
        backend.add_entry(entry)
        plist = tmp_path / f"com.jumar.{entry.schedule_id}.plist"
        assert plist.exists()

    def test_list_returns_installed_entry(self, tmp_path: Path) -> None:
        backend = LaunchdBackend(agents_dir=tmp_path)
        entry = _make_entry(schedule_id="test1234", timezone="UTC")
        backend.add_entry(entry)
        entries = backend.list_entries()
        assert len(entries) == 1
        e = entries[0]
        assert e.schedule_id == "test1234"
        assert e.cron_expr == entry.cron_expr
        assert e.todo_path == entry.todo_path

    def test_list_nonexistent_dir_returns_empty(self, tmp_path: Path) -> None:
        backend = LaunchdBackend(agents_dir=tmp_path / "nonexistent")
        assert backend.list_entries() == []

    def test_remove_deletes_plist(self, tmp_path: Path) -> None:
        backend = LaunchdBackend(agents_dir=tmp_path)
        entry = _make_entry()
        backend.add_entry(entry)
        found = backend.remove_entry(entry.schedule_id)
        assert found
        plist = tmp_path / f"com.jumar.{entry.schedule_id}.plist"
        assert not plist.exists()

    def test_remove_not_found_returns_false(self, tmp_path: Path) -> None:
        backend = LaunchdBackend(agents_dir=tmp_path)
        assert not backend.remove_entry("nonexistent-id")

    def test_name(self, tmp_path: Path) -> None:
        assert LaunchdBackend(agents_dir=tmp_path).name() == "launchd"

    def test_list_multiple_entries(self, tmp_path: Path) -> None:
        backend = LaunchdBackend(agents_dir=tmp_path)
        e1 = _make_entry(schedule_id="aa111111", cron_expr="0 8 * * *")
        e2 = _make_entry(schedule_id="bb222222", cron_expr="0 9 * * *")
        backend.add_entry(e1)
        backend.add_entry(e2)
        entries = backend.list_entries()
        ids = {e.schedule_id for e in entries}
        assert ids == {"aa111111", "bb222222"}


# ---------------------------------------------------------------------------
# default_backend
# ---------------------------------------------------------------------------


class TestDefaultBackend:
    def test_override_launchd(self) -> None:
        b = default_backend("launchd")
        assert isinstance(b, LaunchdBackend)

    def test_override_cron(self) -> None:
        b = default_backend("cron")
        assert isinstance(b, CronBackend)


# ---------------------------------------------------------------------------
# _jumar_executable
# ---------------------------------------------------------------------------


class TestGsdExecutable:
    def test_returns_non_empty_string(self) -> None:
        result = _jumar_executable()
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# _cron_to_oncalendar
# ---------------------------------------------------------------------------


class TestCronToOnCalendar:
    def test_specific_time_daily(self) -> None:
        oncal = _cron_to_oncalendar("30 8 * * *", "UTC")
        assert oncal == "*-*-* 08:30:00 UTC"

    def test_weekdays(self) -> None:
        oncal = _cron_to_oncalendar("0 9 * * 1-5", "America/Los_Angeles")
        assert oncal.startswith("Mon,Tue,Wed,Thu,Fri ")
        assert oncal.endswith("America/Los_Angeles")
        assert "09:00:00" in oncal

    def test_first_of_month(self) -> None:
        oncal = _cron_to_oncalendar("0 0 1 * *", "UTC")
        assert oncal == "*-*-01 00:00:00 UTC"

    def test_timezone_always_appended(self) -> None:
        assert _cron_to_oncalendar("0 9 * * *", "Europe/Berlin").endswith("Europe/Berlin")

    def test_invalid_expression_falls_back(self) -> None:
        oncal = _cron_to_oncalendar("not valid", "UTC")
        assert oncal == "*-*-* *:*:00 UTC"


# ---------------------------------------------------------------------------
# SystemdBackend
# ---------------------------------------------------------------------------


class TestSystemdBackend:
    def test_add_creates_service_and_timer(self, tmp_path: Path) -> None:
        backend = SystemdBackend(units_dir=tmp_path)
        entry = _make_entry()
        backend.add_entry(entry)
        service = tmp_path / f"jumar-{entry.schedule_id}.service"
        timer = tmp_path / f"jumar-{entry.schedule_id}.timer"
        assert service.exists()
        assert timer.exists()

    def test_list_returns_installed_entry(self, tmp_path: Path) -> None:
        backend = SystemdBackend(units_dir=tmp_path)
        entry = _make_entry(schedule_id="test1234", timezone="UTC")
        backend.add_entry(entry)
        entries = backend.list_entries()
        assert len(entries) == 1
        e = entries[0]
        assert e.schedule_id == "test1234"
        assert e.cron_expr == entry.cron_expr
        assert e.todo_path == entry.todo_path
        assert e.timezone == "UTC"

    def test_list_nonexistent_dir_returns_empty(self, tmp_path: Path) -> None:
        backend = SystemdBackend(units_dir=tmp_path / "nonexistent")
        assert backend.list_entries() == []

    def test_remove_deletes_both_files(self, tmp_path: Path) -> None:
        backend = SystemdBackend(units_dir=tmp_path)
        entry = _make_entry()
        backend.add_entry(entry)
        found = backend.remove_entry(entry.schedule_id)
        assert found
        assert not (tmp_path / f"jumar-{entry.schedule_id}.service").exists()
        assert not (tmp_path / f"jumar-{entry.schedule_id}.timer").exists()

    def test_remove_not_found_returns_false(self, tmp_path: Path) -> None:
        backend = SystemdBackend(units_dir=tmp_path)
        assert not backend.remove_entry("nonexistent-id")

    def test_name(self, tmp_path: Path) -> None:
        assert SystemdBackend(units_dir=tmp_path).name() == "systemd"

    def test_list_multiple_entries(self, tmp_path: Path) -> None:
        backend = SystemdBackend(units_dir=tmp_path)
        e1 = _make_entry(schedule_id="aa111111", cron_expr="0 8 * * *")
        e2 = _make_entry(schedule_id="bb222222", cron_expr="0 9 * * *")
        backend.add_entry(e1)
        backend.add_entry(e2)
        entries = backend.list_entries()
        ids = {e.schedule_id for e in entries}
        assert ids == {"aa111111", "bb222222"}

    def test_exec_start_absolute_and_non_interactive(self, tmp_path: Path) -> None:
        """AC10.3: installed command carries absolute jumar path and --non-interactive."""
        backend = SystemdBackend(units_dir=tmp_path)
        entry = _make_entry()
        backend.add_entry(entry)
        service_text = (tmp_path / f"jumar-{entry.schedule_id}.service").read_text()
        assert "ExecStart=" in service_text
        assert entry.jumar_path in service_text
        assert entry.todo_path in service_text
        assert "--non-interactive" in service_text

    def test_timezone_recorded_in_timer(self, tmp_path: Path) -> None:
        """AC10.9: resolved timezone recorded on the installed timer unit."""
        backend = SystemdBackend(units_dir=tmp_path)
        entry = _make_entry(timezone="Asia/Tokyo")
        backend.add_entry(entry)
        timer_text = (tmp_path / f"jumar-{entry.schedule_id}.timer").read_text()
        assert "Asia/Tokyo" in timer_text
        entries = backend.list_entries()
        assert entries[0].timezone == "Asia/Tokyo"

    def test_unrelated_units_survive_add_and_remove(self, tmp_path: Path) -> None:
        """AC10.2 analogue: remove touches only jumar's own two files."""
        unrelated_service = tmp_path / "some-other-app.service"
        unrelated_service.write_text("[Unit]\nDescription=not jumar\n")
        backend = SystemdBackend(units_dir=tmp_path)
        entry = _make_entry()
        backend.add_entry(entry)
        backend.remove_entry(entry.schedule_id)
        assert unrelated_service.read_text() == "[Unit]\nDescription=not jumar\n"
        assert list_schedules(backend) == []

    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        """AC10.1: dry-run never touches the systemd unit directory."""
        backend = SystemdBackend(units_dir=tmp_path)
        todo = tmp_path / "todo.md"
        todo.write_text("")
        add_schedule(
            "0 9 * * *",
            todo_path=todo,
            timezone="UTC",
            backend=backend,
            jumar_path=_GSD,
            dry_run=True,
        )
        assert backend.list_entries() == []
        assert list(tmp_path.glob("jumar-*")) == []


# ---------------------------------------------------------------------------
# _systemd_user_available / _autodetect_backend_name / default_backend
# ---------------------------------------------------------------------------


class TestOnCalendarLines:
    def test_single_line_for_ordinary_expression(self) -> None:
        assert _oncalendar_lines("0 9 * * *", "UTC") == ["*-*-* 09:00:00 UTC"]

    def test_single_line_when_only_weekday_is_restricted(self) -> None:
        assert _oncalendar_lines("0 9 * * 1-5", "UTC") == ["Mon,Tue,Wed,Thu,Fri *-*-* 09:00:00 UTC"]

    def test_dom_and_dow_both_restricted_becomes_two_lines(self) -> None:
        """Cron ORs day-of-month with day-of-week; a systemd calendar spec ANDs
        its weekday prefix with its date part. '0 9 1 * 1' means the 1st of the
        month OR any Monday, so it needs two OnCalendar= values — one per field
        with the other widened — which systemd then ORs back together."""
        lines = _oncalendar_lines("0 9 1 * 1", "UTC")
        assert lines == ["*-*-01 09:00:00 UTC", "Mon *-*-* 09:00:00 UTC"]

    def test_timer_unit_carries_every_oncalendar_line(self, tmp_path: Path) -> None:
        backend = SystemdBackend(units_dir=tmp_path)
        entry = _make_entry(cron_expr="0 9 1 * 1", timezone="UTC")
        backend.add_entry(entry)
        timer_text = (tmp_path / f"jumar-{entry.schedule_id}.timer").read_text()
        oncal = [ln for ln in timer_text.splitlines() if ln.startswith("OnCalendar=")]
        assert oncal == ["OnCalendar=*-*-01 09:00:00 UTC", "OnCalendar=Mon *-*-* 09:00:00 UTC"]


class _FakeSystemctl:
    """Records the systemctl argv the backend runs, in order."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **_kw: object) -> None:
        self.calls.append(list(argv))


class TestSystemdActivation:
    """Writing the unit files does not schedule anything: a .timer only fires
    once `systemctl --user enable` has created its timers.target.wants symlink.
    Install must activate, and remove must deactivate while the unit still
    exists."""

    def test_add_enables_the_timer(self, tmp_path: Path) -> None:
        fake = _FakeSystemctl()
        backend = SystemdBackend(units_dir=tmp_path, activate=True, runner=fake)
        entry = _make_entry(schedule_id="act11111")
        backend.add_entry(entry)
        assert fake.calls == [
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", "--now", "jumar-act11111.timer"],
        ]

    def test_remove_disables_before_deleting_the_units(self, tmp_path: Path) -> None:
        fake = _FakeSystemctl()
        backend = SystemdBackend(units_dir=tmp_path, activate=True, runner=fake)
        entry = _make_entry(schedule_id="act22222")
        backend.add_entry(entry)
        fake.calls.clear()

        seen_while_disabling: list[bool] = []

        def _record(argv: list[str], **_kw: object) -> None:
            if "disable" in argv:
                seen_while_disabling.append((tmp_path / "jumar-act22222.timer").exists())
            fake.calls.append(list(argv))

        backend._run = _record
        assert backend.remove_entry("act22222")
        assert fake.calls == [
            ["systemctl", "--user", "disable", "--now", "jumar-act22222.timer"],
            ["systemctl", "--user", "daemon-reload"],
        ]
        assert seen_while_disabling == [True]
        assert not (tmp_path / "jumar-act22222.timer").exists()

    def test_remove_of_unknown_id_touches_nothing(self, tmp_path: Path) -> None:
        fake = _FakeSystemctl()
        backend = SystemdBackend(units_dir=tmp_path, activate=True, runner=fake)
        assert not backend.remove_entry("nope1234")
        assert fake.calls == []

    def test_injected_units_dir_defaults_to_no_activation(self, tmp_path: Path) -> None:
        """The existing fake-filesystem suite must stay hermetic: passing a
        units_dir with no explicit activate= runs no subprocess at all."""
        fake = _FakeSystemctl()
        backend = SystemdBackend(units_dir=tmp_path, runner=fake)
        entry = _make_entry(schedule_id="act33333")
        backend.add_entry(entry)
        backend.remove_entry("act33333")
        assert fake.calls == []

    def test_a_failed_enable_surfaces(self, tmp_path: Path) -> None:
        """An install that cannot arm the timer must raise, not leave an inert
        unit that `jumar doctor` would report as an installed schedule."""

        def _boom(argv: list[str], **kw: object) -> None:
            if "enable" in argv:
                raise subprocess.CalledProcessError(1, argv)

        backend = SystemdBackend(units_dir=tmp_path, activate=True, runner=_boom)
        with pytest.raises(subprocess.CalledProcessError):
            backend.add_entry(_make_entry(schedule_id="act44444"))


class TestSystemdUserAvailable:
    def test_no_systemctl_on_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(schedule_module.shutil, "which", lambda _name: None)
        assert not _systemd_user_available()

    def test_systemctl_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(schedule_module.shutil, "which", lambda _name: "/usr/bin/systemctl")

        class _Result:
            stdout = "running\n"

        monkeypatch.setattr(
            schedule_module.subprocess,
            "run",
            lambda *a, **kw: _Result(),  # noqa: ARG005
        )
        assert _systemd_user_available()

    def test_systemctl_degraded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(schedule_module.shutil, "which", lambda _name: "/usr/bin/systemctl")

        class _Result:
            stdout = "degraded\n"

        monkeypatch.setattr(
            schedule_module.subprocess,
            "run",
            lambda *a, **kw: _Result(),  # noqa: ARG005
        )
        assert _systemd_user_available()

    def test_systemctl_unreachable_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(schedule_module.shutil, "which", lambda _name: "/usr/bin/systemctl")

        class _Result:
            stdout = "offline\n"

        monkeypatch.setattr(
            schedule_module.subprocess,
            "run",
            lambda *a, **kw: _Result(),  # noqa: ARG005
        )
        assert not _systemd_user_available()

    def test_systemctl_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(schedule_module.shutil, "which", lambda _name: "/usr/bin/systemctl")

        def _raise(*_a: object, **_kw: object) -> None:
            raise OSError("no session bus")

        monkeypatch.setattr(schedule_module.subprocess, "run", _raise)
        assert not _systemd_user_available()


class TestAutodetectBackendName:
    def test_darwin_prefers_launchd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(schedule_module.sys, "platform", "darwin")
        assert _autodetect_backend_name() == "launchd"

    def test_linux_prefers_systemd_when_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(schedule_module.sys, "platform", "linux")
        monkeypatch.setattr(schedule_module, "_systemd_user_available", lambda: True)
        assert _autodetect_backend_name() == "systemd"

    def test_linux_falls_back_to_cron(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(schedule_module.sys, "platform", "linux")
        monkeypatch.setattr(schedule_module, "_systemd_user_available", lambda: False)
        assert _autodetect_backend_name() == "cron"


class TestDefaultBackendSystemd:
    def test_override_systemd(self) -> None:
        assert isinstance(default_backend("systemd"), SystemdBackend)

    def test_no_override_uses_autodetect(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(schedule_module, "_autodetect_backend_name", lambda: "systemd")
        assert isinstance(default_backend(), SystemdBackend)
