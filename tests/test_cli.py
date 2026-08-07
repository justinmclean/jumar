# SPDX-License-Identifier: Apache-2.0
"""Tests for ``gsd plan --dry-run`` and the clock.py wall-clock discipline."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from getstuffdone.cli import main

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
# Happy-path: gsd plan --dry-run
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
    """``gsd plan`` without --dry-run returns 2 (full pipeline not yet built)."""
    rc = main(["plan"])
    assert rc == 2


# ---------------------------------------------------------------------------
# Clock discipline: static analysis
# ---------------------------------------------------------------------------


def test_clock_is_only_wall_clock_reader() -> None:
    """Assert datetime.now() only appears in clock.py and journal.py.

    journal.py is explicitly allowed: it stamps audit-log ``ts`` fields.
    Every other module must inject ``now`` from ``clock.capture_now()``.
    """
    src_dir = Path(__file__).parent.parent / "src" / "getstuffdone"
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
    from getstuffdone.clock import capture_now
    from getstuffdone.config import load_config

    config = load_config()
    now = capture_now(config)
    assert now.tzinfo is not None
    assert now.utcoffset() is not None
    assert now.utcoffset().total_seconds() == 0  # type: ignore[union-attr]


def test_capture_now_injectable() -> None:
    from getstuffdone.clock import capture_now
    from getstuffdone.config import load_config

    config = load_config()
    fixed = datetime(2026, 8, 7, 10, 0, 0, tzinfo=UTC)
    result = capture_now(config, _now=fixed)
    assert result == fixed


def test_capture_now_converts_to_utc() -> None:
    """A non-UTC-aware datetime passed as _now is converted to UTC."""
    import zoneinfo

    from getstuffdone.clock import capture_now
    from getstuffdone.config import load_config

    config = load_config()
    ny = zoneinfo.ZoneInfo("America/New_York")
    local = datetime(2026, 8, 7, 10, 0, 0, tzinfo=ny)
    result = capture_now(config, _now=local)
    assert result.utcoffset() is not None
    assert result.utcoffset().total_seconds() == 0  # type: ignore[union-attr]
    assert result.hour == 14  # EST+4 = UTC 14:00 (summer/EDT is UTC-4)
