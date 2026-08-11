# SPDX-License-Identifier: Apache-2.0
"""Tests for lock.py — single-flight PID lock (AC10.5, AC10.6)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from jumar.journal import ALREADY_RUNNING, LOCK_RECLAIMED, Journal
from jumar.lock import AlreadyRunning, Lock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_lock(lock_path: Path, pid: int, run_id: str = "old-run") -> None:
    lock_path.write_text(
        json.dumps({"pid": pid, "run_id": run_id, "todo": "/tmp/todo.md"}) + "\n",
        encoding="utf-8",
    )


def _dead_pid() -> int:
    """Return a PID that is guaranteed not to be running."""
    for candidate in range(99_999, 1, -1):
        try:
            os.kill(candidate, 0)
        except ProcessLookupError:
            return candidate
        except PermissionError:
            # Process alive — try the next one.
            continue
    pytest.skip("could not find a dead PID for stale-lock test")


def _make_journal(tmp_path: Path, run_id: str = "test-run") -> Journal:
    journal_path = tmp_path / "runs" / run_id / "journal.jsonl"
    return Journal(journal_path, run_id)


# ---------------------------------------------------------------------------
# Basic acquire / release
# ---------------------------------------------------------------------------


def test_acquire_creates_pid_lock_file(tmp_path: Path) -> None:
    """Acquiring a lock writes a JSON file containing our PID."""
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] task\n")
    lock = Lock(todo)
    lock.acquire()
    try:
        assert lock.lock_path.exists()
        data = json.loads(lock.lock_path.read_text())
        assert data["pid"] == os.getpid()
    finally:
        lock.release()


def test_release_removes_lock_file(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] task\n")
    lock = Lock(todo)
    lock.acquire()
    lock.release()
    assert not lock.lock_path.exists()


def test_release_is_idempotent(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] task\n")
    lock = Lock(todo)
    lock.acquire()
    lock.release()
    lock.release()  # second release must not raise


def test_release_without_acquire_is_safe(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] task\n")
    lock = Lock(todo)
    lock.release()  # must not raise


def test_context_manager(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] task\n")
    lock = Lock(todo)
    with lock:
        assert lock.lock_path.exists()
    assert not lock.lock_path.exists()


# ---------------------------------------------------------------------------
# AC10.5 — live lock raises AlreadyRunning
# ---------------------------------------------------------------------------


def test_live_lock_raises_already_running(tmp_path: Path) -> None:
    """AC10.5: a lock file with the current process PID is a live lock."""
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] task\n")
    lock = Lock(todo)
    # Pre-write a lock file with the current PID — this process is alive.
    _write_lock(lock.lock_path, pid=os.getpid(), run_id="live-run")

    try:
        with pytest.raises(AlreadyRunning) as exc_info:
            lock.acquire()
        assert exc_info.value.pid == os.getpid()
        assert exc_info.value.run_id == "live-run"
    finally:
        lock.lock_path.unlink(missing_ok=True)


def test_live_lock_journals_already_running(tmp_path: Path) -> None:
    """AC10.5: the already_running event is journalled when journal is provided."""
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] task\n")
    journal = _make_journal(tmp_path)
    lock = Lock(todo)
    _write_lock(lock.lock_path, pid=os.getpid(), run_id="live-run")

    try:
        with pytest.raises(AlreadyRunning):
            lock.acquire(journal=journal)

        state = journal.replay()
        events = [e["event"] for e in state.entries]
        assert ALREADY_RUNNING in events

        # Verify the payload contains the expected fields.
        ar_entry = next(e for e in state.entries if e["event"] == ALREADY_RUNNING)
        assert ar_entry["payload"]["lock_pid"] == os.getpid()
        assert ar_entry["payload"]["lock_run_id"] == "live-run"
    finally:
        lock.lock_path.unlink(missing_ok=True)


def test_live_lock_does_not_overwrite_lock_file(tmp_path: Path) -> None:
    """AlreadyRunning must not remove or replace the live lock file."""
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] task\n")
    lock = Lock(todo)
    _write_lock(lock.lock_path, pid=os.getpid(), run_id="live-run")

    try:
        with pytest.raises(AlreadyRunning):
            lock.acquire()
        # The original lock file is still intact.
        data = json.loads(lock.lock_path.read_text())
        assert data["pid"] == os.getpid()
        assert data["run_id"] == "live-run"
    finally:
        lock.lock_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# AC10.6 — stale lock is reclaimed
# ---------------------------------------------------------------------------


def test_stale_lock_is_reclaimed(tmp_path: Path) -> None:
    """AC10.6: a lock file with a dead PID is reclaimed and the run proceeds."""
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] task\n")
    lock = Lock(todo)
    _write_lock(lock.lock_path, pid=_dead_pid(), run_id="dead-run")

    lock.acquire()
    try:
        # New lock file must have our PID.
        data = json.loads(lock.lock_path.read_text())
        assert data["pid"] == os.getpid()
    finally:
        lock.release()


def test_stale_lock_journals_reclaim(tmp_path: Path) -> None:
    """AC10.6: the lock_reclaimed event is journalled when journal is provided."""
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] task\n")
    journal = _make_journal(tmp_path)
    dead = _dead_pid()
    lock = Lock(todo)
    _write_lock(lock.lock_path, pid=dead, run_id="dead-run")

    lock.acquire(journal=journal)
    try:
        state = journal.replay()
        events = [e["event"] for e in state.entries]
        assert LOCK_RECLAIMED in events

        reclaim_entry = next(e for e in state.entries if e["event"] == LOCK_RECLAIMED)
        assert reclaim_entry["payload"]["stale_pid"] == dead
        assert reclaim_entry["payload"]["stale_run_id"] == "dead-run"
    finally:
        lock.release()


def test_stale_lock_sets_reclaimed_info(tmp_path: Path) -> None:
    """reclaimed_info is set after a stale lock is reclaimed."""
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] task\n")
    dead = _dead_pid()
    lock = Lock(todo)
    _write_lock(lock.lock_path, pid=dead, run_id="dead-run")

    lock.acquire()
    try:
        assert lock.reclaimed_info is not None
        assert lock.reclaimed_info["stale_pid"] == dead
    finally:
        lock.release()


def test_no_stale_lock_has_none_reclaimed_info(tmp_path: Path) -> None:
    """reclaimed_info is None when no stale lock was present."""
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] task\n")
    lock = Lock(todo)
    lock.acquire()
    try:
        assert lock.reclaimed_info is None
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# Corrupt / missing lock file edge cases
# ---------------------------------------------------------------------------


def test_corrupt_lock_file_treated_as_stale(tmp_path: Path) -> None:
    """A corrupt (non-JSON) lock file is treated as stale and overwritten."""
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] task\n")
    lock = Lock(todo)
    lock.lock_path.write_text("not valid json!!!", encoding="utf-8")

    lock.acquire()
    try:
        data = json.loads(lock.lock_path.read_text())
        assert data["pid"] == os.getpid()
    finally:
        lock.release()


def test_no_existing_lock_acquires_cleanly(tmp_path: Path) -> None:
    """Acquiring with no pre-existing lock file succeeds without error."""
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] task\n")
    lock = Lock(todo)
    assert not lock.lock_path.exists()
    lock.acquire()
    try:
        assert lock.lock_path.exists()
    finally:
        lock.release()


def test_run_id_recorded_in_lock_file(tmp_path: Path) -> None:
    """The run_id passed to acquire() is stored in the lock file."""
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] task\n")
    lock = Lock(todo)
    lock.acquire(run_id="my-run-123")
    try:
        data = json.loads(lock.lock_path.read_text())
        assert data["run_id"] == "my-run-123"
    finally:
        lock.release()
