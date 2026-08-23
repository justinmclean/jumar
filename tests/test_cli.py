# SPDX-License-Identifier: Apache-2.0
"""Tests for ``jumar plan --dry-run`` and the clock.py wall-clock discipline."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from jumar.cli import main

if TYPE_CHECKING:
    from jumar.schedule import FakeBackend

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def todo_one_item(tmp_path: Path) -> Path:
    """A minimal todo file with one pending item."""
    p = tmp_path / "todo.md"
    p.write_text("- [ ] Write unit tests for the parser\n")
    return p


@pytest.fixture()
def todo_two_items(tmp_path: Path) -> Path:
    """Two pending items with priorities for ordering."""
    p = tmp_path / "todo.md"
    p.write_text("- [ ] Low priority task @priority=5\n- [ ] High priority task @priority=1\n")
    return p


@pytest.fixture()
def todo_deferred(tmp_path: Path) -> Path:
    """A todo file where the only item is deferred into the future."""
    p = tmp_path / "todo.md"
    p.write_text("- [ ] Future task @not-before=2099-01-01\n")
    return p


@pytest.fixture()
def todo_cycle(tmp_path: Path) -> Path:
    """A todo file with a dependency cycle."""
    p = tmp_path / "todo.md"
    p.write_text("- [ ] Task A @id=a @depends=b\n- [ ] Task B @id=b @depends=a\n")
    return p


@pytest.fixture()
def todo_with_subtasks(tmp_path: Path) -> Path:
    """A todo file with authored subtasks."""
    p = tmp_path / "todo.md"
    p.write_text("- [ ] Build the feature\n  - [ ] Write the code\n  - [ ] Run the tests\n")
    return p


# ---------------------------------------------------------------------------
# Happy-path: jumar plan --dry-run
# ---------------------------------------------------------------------------


def test_plan_dry_run_exits_zero(todo_one_item: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["plan", "--dry-run", "--todo", str(todo_one_item)])
    assert rc == 0


def test_plan_dry_run_produces_output(
    todo_one_item: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["plan", "--dry-run", "--todo", str(todo_one_item)])
    out = capsys.readouterr().out
    assert out.strip(), "expected non-empty output"


def test_plan_dry_run_shows_selected_item(
    todo_one_item: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["plan", "--dry-run", "--todo", str(todo_one_item)])
    out = capsys.readouterr().out
    assert "Write unit tests for the parser" in out


def test_plan_dry_run_shows_run_id(todo_one_item: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["plan", "--dry-run", "--todo", str(todo_one_item)])
    out = capsys.readouterr().out
    assert "Run:" in out


def test_plan_dry_run_selects_high_priority_first(
    todo_two_items: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["plan", "--dry-run", "--todo", str(todo_two_items)])
    out = capsys.readouterr().out
    assert "High priority task" in out
    assert "Low priority task" not in out.split("Selected:")[1].split("\n")[0]


def test_plan_dry_run_shows_authored_subtasks(
    todo_with_subtasks: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["plan", "--dry-run", "--todo", str(todo_with_subtasks)])
    out = capsys.readouterr().out
    assert "Write the code" in out
    assert "Run the tests" in out


def test_plan_dry_run_journals_run_started(
    todo_one_item: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run_started event is written to the journal before any other event."""
    import json

    # Run from tmp_path so runs/ lands there, not in the project root.
    monkeypatch.chdir(tmp_path)
    main(["plan", "--dry-run", "--todo", str(todo_one_item)])

    runs_dir = tmp_path / "runs"
    assert runs_dir.is_dir(), "runs/ directory should be created"
    journals = list(runs_dir.glob("*/journal.jsonl"))
    assert len(journals) == 1, "exactly one journal should be created"

    lines = journals[0].read_text().splitlines()
    assert lines, "journal must not be empty"
    first = json.loads(lines[0])
    assert first["event"] == "run_started"
    assert "now" in first.get("payload", {})
    assert "tz" in first.get("payload", {})
    assert first["payload"]["mode"] == "dry-run"


def test_plan_dry_run_makes_no_harness_calls(
    todo_one_item: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No subprocess should be spawned during --dry-run."""
    import subprocess

    spawned: list[str] = []

    original_run = subprocess.run

    def fake_run(args: object, **kwargs: object) -> object:  # type: ignore[misc]
        spawned.append(str(args))
        return original_run(args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess, "run", fake_run)
    rc = main(["plan", "--dry-run", "--todo", str(todo_one_item)])
    assert rc == 0
    assert spawned == [], f"unexpected subprocess calls: {spawned}"


# ---------------------------------------------------------------------------
# Negative paths
# ---------------------------------------------------------------------------


def test_plan_dry_run_missing_todo(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["plan", "--dry-run", "--todo", str(tmp_path / "nonexistent.md")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "error" in err.lower()


def test_plan_dry_run_cycle(
    todo_cycle: Path,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    rc = main(["plan", "--dry-run", "--todo", str(todo_cycle)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "cycle" in err.lower() or "error" in err.lower()


def test_plan_dry_run_all_deferred_exits_zero(
    todo_deferred: Path,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    rc = main(["plan", "--dry-run", "--todo", str(todo_deferred)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Nothing eligible" in out or "Deferred" in out


def test_plan_without_dry_run_is_not_yet_implemented(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``jumar plan`` without --dry-run returns 2 (full pipeline not yet built)."""
    rc = main(["plan"])
    assert rc == 2


def test_plan_dry_run_with_ingest_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ingest warnings are printed to stderr."""
    monkeypatch.chdir(tmp_path)
    # An empty task item ("- [ ]" with no text) generates an ingest warning.
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] \n- [ ] Real task\n")
    rc = main(["plan", "--dry-run", "--todo", str(todo)])
    assert rc == 0
    err = capsys.readouterr().err
    assert "warning" in err.lower()


def test_plan_dry_run_item_with_due_date(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An item with a @due= token displays the due date in --dry-run output."""
    monkeypatch.chdir(tmp_path)
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] Submit report @due=2099-12-31\n")
    rc = main(["plan", "--dry-run", "--todo", str(todo)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "2099-12-31" in out


def test_doctor_runs_its_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``jumar doctor`` executes its checks and reports them.

    The verdict is environment-dependent (the harness binary may or may not be
    on PATH), so this asserts the command ran and rendered a report rather than
    pinning a pass/fail. Never 2 — that was the pre-implementation stub.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "todo.md").write_text("- [ ] Something to do\n")

    rc = main(["doctor"])
    out = capsys.readouterr().out

    assert rc in (0, 1)
    assert out.startswith("jumar doctor")
    for name in ("harness", "todo"):
        assert name in out


def test_doctor_reports_a_missing_todo_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A todo file that does not exist is surfaced as a check line, not a crash."""
    monkeypatch.chdir(tmp_path)

    rc = main(["doctor", "--todo", str(tmp_path / "definitely-absent.md")])
    out = capsys.readouterr().out

    assert rc in (0, 1)
    assert "definitely-absent.md" in out or "todo" in out


# ---------------------------------------------------------------------------
# --config PATH (W8): explicit config resolution, independent of cwd
# ---------------------------------------------------------------------------


def test_run_accepts_config_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--config`` must parse on ``run``: this is the argv `_build_command()`
    installs for a scheduled entry, and W8 found `run_p` rejecting it with
    exit 2 ("unrecognized arguments"). An empty todo file keeps the run at
    "nothing eligible", clear of decompose/the real harness binary.
    """
    monkeypatch.chdir(tmp_path)
    todo = tmp_path / "todo.md"
    todo.write_text("")
    cfg = tmp_path / "jumar.toml"
    cfg.write_text("[jumar]\nmax_subtasks = 3\n")

    rc = main(["run", "--todo", str(todo), "--config", str(cfg)])

    assert rc == 0
    assert "Nothing eligible" in capsys.readouterr().out


def test_run_missing_config_path_is_a_startup_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    todo = tmp_path / "todo.md"
    todo.write_text("")

    rc = main(["run", "--todo", str(todo), "--config", str(tmp_path / "nope.toml")])

    assert rc == 2
    assert "config" in capsys.readouterr().err.lower()


def test_plan_accepts_config_flag(
    todo_one_item: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = tmp_path / "jumar.toml"
    cfg.write_text("[jumar]\nmax_subtasks = 3\n")

    rc = main(["plan", "--dry-run", "--todo", str(todo_one_item), "--config", str(cfg)])

    assert rc == 0


def test_plan_config_used_regardless_of_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of --config: resolution must not depend on the
    invoking cwd, the way scheduled entries' cwd is unrelated to the project.
    """
    project = tmp_path / "project"
    project.mkdir()
    todo = project / "todo.md"
    todo.write_text("- [ ] Something to do\n")
    cfg = project / "jumar.toml"
    cfg.write_text("[jumar]\nmax_subtasks = 3\n")

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    rc = main(["plan", "--dry-run", "--todo", str(todo), "--config", str(cfg)])

    assert rc == 0


def test_doctor_accepts_config_flag(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] Something to do\n")
    cfg = tmp_path / "jumar.toml"
    cfg.write_text("[jumar]\nmax_subtasks = 3\n")

    rc = main(["doctor", "--todo", str(todo), "--config", str(cfg)])

    assert rc in (0, 1)


def test_doctor_missing_config_path_is_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] Something to do\n")

    rc = main(["doctor", "--todo", str(todo), "--config", str(tmp_path / "nope.toml")])

    assert rc == 2
    assert "config" in capsys.readouterr().err.lower()


def test_status_accepts_config_flag(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] Something to do\n")
    cfg = tmp_path / "jumar.toml"
    cfg.write_text("[jumar]\nmax_subtasks = 3\n")

    rc = main(["status", "--todo", str(todo), "--config", str(cfg)])

    assert rc == 0


def test_status_missing_config_path_is_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] Something to do\n")

    rc = main(["status", "--todo", str(todo), "--config", str(tmp_path / "nope.toml")])

    assert rc == 2
    assert "config" in capsys.readouterr().err.lower()


def test_plan_missing_config_path_is_an_error(
    todo_one_item: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(
        [
            "plan",
            "--dry-run",
            "--todo",
            str(todo_one_item),
            "--config",
            str(todo_one_item.parent / "nope.toml"),
        ]
    )

    assert rc == 2
    assert "config" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------------------
# Clock discipline: static analysis
# ---------------------------------------------------------------------------


def test_clock_is_only_wall_clock_reader() -> None:
    """Assert datetime.now() only appears in clock.py and journal.py.

    journal.py is explicitly allowed: it stamps audit-log ``ts`` fields.
    Every other module must inject ``now`` from ``clock.capture_now()``.
    """
    src_dir = Path(__file__).parent.parent / "src" / "jumar"
    allowed: frozenset[str] = frozenset({"clock.py", "journal.py"})
    violators: list[str] = []

    for py_file in sorted(src_dir.rglob("*.py")):
        if py_file.name in allowed:
            continue
        content = py_file.read_text()
        if re.search(r"\bdatetime\.now\(", content) or re.search(r"\bdatetime\.utcnow\(", content):
            violators.append(py_file.name)

    assert violators == [], (
        f"These modules call datetime.now()/utcnow() but are not in the allowed set: {violators}"
    )


# ---------------------------------------------------------------------------
# capture_now injectable clock
# ---------------------------------------------------------------------------


def test_capture_now_returns_utc() -> None:
    from jumar.clock import capture_now
    from jumar.config import load_config

    config = load_config()
    now = capture_now(config)
    assert now.tzinfo is not None
    assert now.utcoffset() is not None
    assert now.utcoffset().total_seconds() == 0  # type: ignore[union-attr]


def test_capture_now_injectable() -> None:
    from jumar.clock import capture_now
    from jumar.config import load_config

    config = load_config()
    fixed = datetime(2026, 8, 7, 10, 0, 0, tzinfo=UTC)
    result = capture_now(config, _now=fixed)
    assert result == fixed


def test_capture_now_converts_to_utc() -> None:
    """A non-UTC-aware datetime passed as _now is converted to UTC."""
    import zoneinfo

    from jumar.clock import capture_now
    from jumar.config import load_config

    config = load_config()
    ny = zoneinfo.ZoneInfo("America/New_York")
    local = datetime(2026, 8, 7, 10, 0, 0, tzinfo=ny)
    result = capture_now(config, _now=local)
    assert result.utcoffset() is not None
    assert result.utcoffset().total_seconds() == 0  # type: ignore[union-attr]
    assert result.hour == 14  # EST+4 = UTC 14:00 (summer/EDT is UTC-4)


# ---------------------------------------------------------------------------
# jumar run subcommand
# ---------------------------------------------------------------------------


def test_run_missing_todo_file_returns_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``jumar run`` with no todo file is an actionable error, not a traceback."""
    monkeypatch.chdir(tmp_path)
    rc = main(["run"])
    assert rc == 1
    assert "todo.md" in capsys.readouterr().err


def test_run_dry_run_still_validates_the_todo_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--dry-run`` clears the startup gate but still needs a readable todo file."""
    monkeypatch.chdir(tmp_path)
    rc = main(["run", "--dry-run"])
    assert rc == 1
    assert "todo.md" in capsys.readouterr().err


def test_run_approve_non_interactive_startup_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``jumar run --approve --non-interactive`` is refused at startup (AC4.4)."""
    rc = main(["run", "--approve", "--non-interactive"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "error" in err.lower()


def test_run_honours_an_explicit_todo_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--todo`` overrides config; the error names the path that was used.

    Deliberately points at a path that does not exist: ``main()`` has no seam
    for injecting a fake agent, so a test here must never reach decompose —
    that would launch the real harness binary. Full-pipeline coverage lives in
    tests/test_run.py, where the agent is injected.
    """
    monkeypatch.chdir(tmp_path)
    missing = tmp_path / "mytodo.md"

    rc = main(["run", "--todo", str(missing)])

    assert rc == 1
    assert "mytodo.md" in capsys.readouterr().err


def test_run_already_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``jumar run`` exits 0 with 'already_running' when a live lock file exists."""
    import json
    import os

    monkeypatch.chdir(tmp_path)
    lock_path = tmp_path / ".jumar.lock"
    lock_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "run_id": "existing-run",
                "todo": str(tmp_path / "todo.md"),
            }
        )
    )
    rc = main(["run"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "already_running" in err


# ---------------------------------------------------------------------------
# jumar report subcommand
# ---------------------------------------------------------------------------


def test_report_missing_journal_returns_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``jumar report <run-id>`` when no journal exists returns 1 with an error."""
    monkeypatch.chdir(tmp_path)
    rc = main(["report", "no-such-run-id"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "error" in err.lower()


def test_report_with_existing_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``jumar report <run-id>`` with a valid journal exits 0 and writes report.md."""
    import json

    monkeypatch.chdir(tmp_path)
    run_id = "test-run-abc123"
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    journal = run_dir / "journal.jsonl"
    journal.write_text(
        json.dumps(
            {
                "seq": 1,
                "event": "run_started",
                "ts": "2026-01-01T00:00:00+00:00",
                "payload": {
                    "now": "2026-01-01T00:00:00+00:00",
                    "tz": "UTC",
                    "mode": "run",
                    "todo_path": "todo.md",
                },
            }
        )
        + "\n"
        + json.dumps(
            {
                "seq": 2,
                "event": "run_finished",
                "ts": "2026-01-01T00:00:01+00:00",
                "payload": {"exit_status": 0},
            }
        )
        + "\n"
    )
    rc = main(["report", run_id, "--runs-dir", str(tmp_path / "runs")])
    assert rc == 0
    assert (run_dir / "report.md").exists()


# ---------------------------------------------------------------------------
# jumar schedule subcommand
# ---------------------------------------------------------------------------


@pytest.fixture()
def _fake_backend(monkeypatch: pytest.MonkeyPatch) -> FakeBackend:
    """Patch schedule.default_backend to return a FakeBackend for all CLI tests."""
    from jumar.schedule import FakeBackend as FB

    fake = FB()
    monkeypatch.setattr("jumar.schedule.default_backend", lambda override=None: fake)
    return fake


class TestScheduleCLI:
    def test_add_dry_run_exits_zero(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``jumar schedule add --dry-run`` prints the entry and exits 0."""
        monkeypatch.chdir(tmp_path)
        todo = tmp_path / "todo.md"
        todo.write_text("")
        rc = main(["schedule", "add", "0 9 * * 1-5", "--todo", str(todo), "--dry-run"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "0 9 * * 1-5" in out

    def test_add_writes_to_backend(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        _fake_backend: FakeBackend,
    ) -> None:
        """``jumar schedule add`` installs an entry in the backend."""
        monkeypatch.chdir(tmp_path)
        todo = tmp_path / "todo.md"
        todo.write_text("")
        rc = main(["schedule", "add", "0 9 * * *", "--todo", str(todo)])
        assert rc == 0
        from jumar.schedule import list_schedules

        assert len(list_schedules(_fake_backend)) == 1

    def test_add_invalid_cron_returns_1(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        _fake_backend: FakeBackend,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Invalid cron expression prints an error and returns 1."""
        monkeypatch.chdir(tmp_path)
        todo = tmp_path / "todo.md"
        todo.write_text("")
        rc = main(["schedule", "add", "99 9 * * *", "--todo", str(todo)])
        assert rc == 1
        err = capsys.readouterr().err
        assert "invalid cron expression" in err
        assert _fake_backend.write_calls == 0

    def test_show_exits_zero_prints_entry(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``jumar schedule show`` prints the entry without installing."""
        monkeypatch.chdir(tmp_path)
        todo = tmp_path / "todo.md"
        todo.write_text("")
        rc = main(["schedule", "show", "0 9 * * *", "--todo", str(todo)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "0 9 * * *" in out

    def test_show_invalid_cron_returns_1(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.chdir(tmp_path)
        todo = tmp_path / "todo.md"
        todo.write_text("")
        rc = main(["schedule", "show", "bad cron expr here", "--todo", str(todo)])
        assert rc == 1

    def test_list_empty_backend(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        _fake_backend: FakeBackend,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``jumar schedule list`` with no entries prints the no-entries message."""
        monkeypatch.chdir(tmp_path)
        rc = main(["schedule", "list"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "No jumar-owned" in out

    def test_list_with_entries(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        _fake_backend: FakeBackend,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``jumar schedule list`` shows installed entries."""
        monkeypatch.chdir(tmp_path)
        todo = tmp_path / "todo.md"
        todo.write_text("")
        main(["schedule", "add", "0 9 * * *", "--todo", str(todo)])
        rc = main(["schedule", "list"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "0 9 * * *" in out

    def test_remove_found_exits_zero(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        _fake_backend: FakeBackend,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``jumar schedule remove <id>`` removes the entry and exits 0."""
        monkeypatch.chdir(tmp_path)
        todo = tmp_path / "todo.md"
        todo.write_text("")
        main(["schedule", "add", "0 9 * * *", "--todo", str(todo), "--dry-run"])
        # Manually install one entry so we can remove it.
        from jumar.schedule import ScheduleEntry

        _fake_backend.add_entry(
            ScheduleEntry(
                schedule_id="testremove",
                cron_expr="0 9 * * *",
                todo_path=str(todo),
                jumar_path="/usr/local/bin/jumar",
                config_path=None,
                timezone="UTC",
            )
        )
        rc = main(["schedule", "remove", "testremove"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Removed" in out

    def test_remove_not_found_returns_1(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        _fake_backend: FakeBackend,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``jumar schedule remove`` for a missing id is a clean non-zero error
        that modifies nothing (AC10.8)."""
        from jumar.schedule import ScheduleEntry

        monkeypatch.chdir(tmp_path)
        todo = tmp_path / "todo.md"
        todo.write_text("")
        _fake_backend.add_entry(
            ScheduleEntry(
                schedule_id="untouched",
                cron_expr="0 9 * * *",
                todo_path=str(todo),
                jumar_path="/usr/local/bin/jumar",
                config_path=None,
                timezone="UTC",
            )
        )
        before = _fake_backend.current_text

        rc = main(["schedule", "remove", "no-such-id"])

        assert rc == 1
        err = capsys.readouterr().err
        assert "not found" in err
        assert _fake_backend.current_text == before
        assert [e.schedule_id for e in _fake_backend.list_entries()] == ["untouched"]

    def test_no_subcommand_exits_zero(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``jumar schedule`` with no subcommand prints help and exits 0."""
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            main(["schedule"])
        assert exc_info.value.code == 0
