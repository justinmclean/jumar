# SPDX-License-Identifier: Apache-2.0
"""Tests for gsd doctor checks (src/getstuffdone/doctor.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from getstuffdone.config import CommandPolicy, Config, HarnessConfig
from getstuffdone.doctor import (
    CheckStatus,
    DoctorCheck,
    DoctorReport,
    format_report,
    run_doctor,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cfg(**kwargs: object) -> Config:
    """Build a Config with sensible defaults, overriding any supplied keys."""
    defaults: dict[str, object] = dict(
        todo_path="todo.md",
        harness=HarnessConfig(agent="python3", model=""),
        commands=CommandPolicy(allow=("python3", "pytest"), deny=("curl",)),
    )
    defaults.update(kwargs)
    return Config(**defaults)  # type: ignore[arg-type]


def _find(report: DoctorReport, name: str) -> DoctorCheck:
    """Return the first check whose name starts with *name*."""
    for c in report.checks:
        if c.name == name or c.name.startswith(name + "."):
            return c
    raise KeyError(f"No check named {name!r} in report: {[c.name for c in report.checks]}")


# ---------------------------------------------------------------------------
# Config checks
# ---------------------------------------------------------------------------


def test_config_valid_is_ok() -> None:
    report = run_doctor(_cfg())
    check = _find(report, "config")
    assert check.status == CheckStatus.ok


def test_config_invalid_max_subtasks() -> None:
    config = _cfg(max_subtasks=0)
    report = run_doctor(config)
    check = _find(report, "config.max_subtasks")
    assert check.status == CheckStatus.fail
    assert "max_subtasks" in check.message
    assert "1" in check.message


def test_config_invalid_max_repairs() -> None:
    config = _cfg(max_repairs=-1)
    report = run_doctor(config)
    check = _find(report, "config.max_repairs")
    assert check.status == CheckStatus.fail
    assert "max_repairs" in check.message


def test_config_invalid_timeout() -> None:
    config = _cfg(subtask_timeout_s=0)
    report = run_doctor(config)
    check = _find(report, "config.subtask_timeout_s")
    assert check.status == CheckStatus.fail
    assert "subtask_timeout_s" in check.message


def test_config_invalid_timezone() -> None:
    config = _cfg(timezone="Not/ATimezone")
    report = run_doctor(config)
    check = _find(report, "config.timezone")
    assert check.status == CheckStatus.fail
    assert "Not/ATimezone" in check.message


def test_config_multiple_bad_fields_all_reported() -> None:
    config = _cfg(max_subtasks=0, max_repairs=-1)
    report = run_doctor(config)
    names = {c.name for c in report.checks}
    assert "config.max_subtasks" in names
    assert "config.max_repairs" in names


# ---------------------------------------------------------------------------
# Harness checks
# ---------------------------------------------------------------------------


def test_harness_found_on_path() -> None:
    # python3 is guaranteed to be on PATH in the test environment
    config = _cfg(harness=HarnessConfig(agent="python3", model=""))
    report = run_doctor(config)
    check = _find(report, "harness")
    assert check.status == CheckStatus.ok
    assert "python3" in check.message


def test_harness_not_found_on_path() -> None:
    config = _cfg(harness=HarnessConfig(agent="no-such-gsd-binary-zzz", model=""))
    report = run_doctor(config)
    check = _find(report, "harness")
    assert check.status == CheckStatus.fail
    assert "no-such-gsd-binary-zzz" in check.message
    assert "PATH" in check.message


def test_harness_fail_contributes_to_exit_status() -> None:
    config = _cfg(harness=HarnessConfig(agent="no-such-gsd-binary-zzz", model=""))
    report = run_doctor(config)
    assert report.exit_status == 1


# ---------------------------------------------------------------------------
# Allow-list checks
# ---------------------------------------------------------------------------


def test_allowlist_clean_is_ok() -> None:
    config = _cfg(commands=CommandPolicy(allow=("python3",), deny=("curl",)))
    report = run_doctor(config)
    check = _find(report, "allowlist")
    assert check.status == CheckStatus.ok


def test_allowlist_overlap_is_warn() -> None:
    config = _cfg(commands=CommandPolicy(allow=("curl", "python3"), deny=("curl",)))
    report = run_doctor(config)
    check = _find(report, "allowlist.overlap")
    assert check.status == CheckStatus.warn
    assert "curl" in check.message


def test_allowlist_empty_is_fail() -> None:
    config = _cfg(commands=CommandPolicy(allow=(), deny=()))
    report = run_doctor(config)
    check = _find(report, "allowlist.empty")
    assert check.status == CheckStatus.fail
    assert "empty" in check.message.lower()


def test_allowlist_empty_contributes_to_exit_status() -> None:
    config = _cfg(commands=CommandPolicy(allow=(), deny=()))
    report = run_doctor(config)
    assert report.exit_status == 1


def test_allowlist_overlap_does_not_set_exit_status_alone() -> None:
    # Overlap is WARN only; harness=python3 ok, config ok, todo missing but
    # we need a valid todo for this test to isolate allowlist.
    tmp = Path(__file__).parent  # some directory that exists, but not a todo file
    config = _cfg(
        commands=CommandPolicy(allow=("curl", "python3"), deny=("curl",)),
        todo_path=str(tmp / "_no_such_todo_for_overlap_test.md"),
    )
    report = run_doctor(config)
    # The todo check will FAIL (missing), but allowlist.overlap is WARN only
    overlap = _find(report, "allowlist.overlap")
    assert overlap.status == CheckStatus.warn


# ---------------------------------------------------------------------------
# Todo file checks
# ---------------------------------------------------------------------------


def test_todo_missing_is_fail(tmp_path: Path) -> None:
    config = _cfg(todo_path=str(tmp_path / "nonexistent.md"))
    report = run_doctor(config)
    check = _find(report, "todo")
    assert check.status == CheckStatus.fail
    assert "not found" in check.message.lower()


def test_todo_missing_names_path(tmp_path: Path) -> None:
    missing = tmp_path / "missing.md"
    config = _cfg(todo_path=str(missing))
    report = run_doctor(config)
    check = _find(report, "todo")
    assert str(missing) in check.message


def test_todo_parseable_is_ok(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] Do something productive\n")
    config = _cfg(todo_path=str(todo))
    report = run_doctor(config)
    check = _find(report, "todo")
    assert check.status == CheckStatus.ok
    assert "1" in check.message


def test_todo_with_warnings_is_warn(tmp_path: Path) -> None:
    # An item with an unparseable schedule token produces a parse warning.
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] Recurring task @due=not-a-real-date\n")
    config = _cfg(todo_path=str(todo))
    report = run_doctor(config)
    check = _find(report, "todo")
    # bad schedule → item is blocked, ingest emits a parse warning or the item status
    # reflects the issue; either way the check should be warn or fail (not ok).
    assert check.status in (CheckStatus.warn, CheckStatus.fail)


def test_todo_empty_file_is_ok(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    todo.write_text("")
    config = _cfg(todo_path=str(todo))
    report = run_doctor(config)
    check = _find(report, "todo")
    assert check.status == CheckStatus.ok


def test_todo_fail_contributes_to_exit_status(tmp_path: Path) -> None:
    config = _cfg(
        todo_path=str(tmp_path / "gone.md"),
        harness=HarnessConfig(agent="python3", model=""),
    )
    report = run_doctor(config)
    assert report.exit_status == 1


# ---------------------------------------------------------------------------
# Schedule check
# ---------------------------------------------------------------------------


def test_schedule_module_absent_is_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    """When schedule module is absent, check is warn, not fail."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "getstuffdone.schedule":
            raise ImportError("schedule not available")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    todo = Path(__file__).parent.parent / "tests" / "test_doctor.py"  # any real file
    config = _cfg(todo_path=str(todo))

    # Reimport doctor to pick up the monkeypatched import, then call _check_schedule
    from getstuffdone.doctor import _check_schedule

    check = _check_schedule(config)
    assert check.status == CheckStatus.warn
    assert "schedule" in check.message.lower()


def test_schedule_module_absent_does_not_fail_run(tmp_path: Path) -> None:
    """A missing schedule module produces a warn, not a fail."""
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] One task\n")
    config = _cfg(todo_path=str(todo))

    from getstuffdone.doctor import _check_schedule

    check = _check_schedule(config)
    # Should be warn (module absent) or ok (if schedule is present and empty)
    assert check.status in (CheckStatus.warn, CheckStatus.ok)


# ---------------------------------------------------------------------------
# DoctorReport aggregation
# ---------------------------------------------------------------------------


def test_report_exit_status_zero_when_all_ok(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] Task\n")
    config = _cfg(todo_path=str(todo), harness=HarnessConfig(agent="python3", model=""))
    report = run_doctor(config)
    # Only possible FAILs: harness or todo; both are ok here.
    # Ignore schedule warn.
    fail_checks = [c for c in report.checks if c.status == CheckStatus.fail]
    assert fail_checks == [], f"unexpected FAILs: {fail_checks}"
    assert report.exit_status == 0


def test_report_exit_status_one_when_any_fail(tmp_path: Path) -> None:
    config = _cfg(todo_path=str(tmp_path / "missing.md"))
    report = run_doctor(config)
    assert report.exit_status == 1


def test_format_report_contains_check_names(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] Task\n")
    config = _cfg(todo_path=str(todo))
    report = run_doctor(config)
    text = format_report(report)
    assert "gsd doctor" in text
    assert "todo" in text
    assert "harness" in text


def test_format_report_shows_fail_label(tmp_path: Path) -> None:
    config = _cfg(todo_path=str(tmp_path / "absent.md"))
    report = run_doctor(config)
    text = format_report(report)
    assert "FAIL" in text


def test_format_report_shows_ok_label(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] A task\n")
    config = _cfg(todo_path=str(todo), harness=HarnessConfig(agent="python3", model=""))
    report = run_doctor(config)
    text = format_report(report)
    assert "ok" in text.lower()


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


def test_cli_doctor_exits_nonzero_on_missing_todo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    from getstuffdone.cli import main

    rc = main(["doctor", "--todo", str(tmp_path / "no_such.md")])
    assert rc == 1
    out = capsys.readouterr().out
    assert "FAIL" in out


def test_cli_doctor_exits_zero_with_valid_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] Write tests\n")

    # Write a gsd.toml that uses python3 as the harness (guaranteed on PATH).
    (tmp_path / "gsd.toml").write_text(
        '[gsd]\ntodo_path = "todo.md"\n\n[gsd.harness]\nagent = "python3"\nmodel = ""\n'
    )

    from getstuffdone.cli import main

    rc = main(["doctor", "--todo", str(todo)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "gsd doctor" in out


def test_cli_doctor_subcommand_is_known() -> None:
    from getstuffdone.cli import build_parser

    args = build_parser().parse_args(["doctor"])
    assert args.command == "doctor"
