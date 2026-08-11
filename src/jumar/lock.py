# SPDX-License-Identifier: Apache-2.0
"""Single-flight run lock for a todo path.

A PID-stamped JSON lock file is written alongside the todo file before ingest
begins. If a second ``jumar run`` finds a live lock for the same todo path it
exits 0 immediately with ``already_running`` (AC10.5). A stale lock — one
whose recorded PID is no longer running — is reclaimed, the reclaim is
journalled, and the current run proceeds (AC10.6).

Public API
----------
AlreadyRunning    – raised by Lock.acquire() when a live lock exists.
Lock              – acquire / release around a single run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .journal import Journal

_LOCK_FILENAME = ".jumar.lock"


class AlreadyRunning(Exception):
    """Raised when a live lock file exists for the same todo path (AC10.5)."""

    def __init__(self, *, pid: int, run_id: str | None) -> None:
        self.pid = pid
        self.run_id = run_id
        super().__init__(f"already running (pid={pid}, run_id={run_id})")


def _pid_alive(pid: int) -> bool:
    """Return True if *pid* names a running process."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we cannot send signals to it — it is alive.
        return True
    except OSError:
        return False


class Lock:
    """PID-stamped lock file scoped to one todo-file path.

    The lock file lives in the same directory as the todo file and is named
    ``.jumar.lock``.  It contains a single JSON object::

        {"pid": <int>, "run_id": "<str>", "todo": "<abs-path>"}

    Usage::

        lock = Lock(todo_path)
        try:
            lock.acquire(run_id=run_id, journal=journal)
        except AlreadyRunning as exc:
            print(f"already_running (pid={exc.pid})")
            return 0
        try:
            # do work
        finally:
            lock.release()

    Or as a context manager (no journal support in __enter__)::

        with Lock(todo_path) as lock:
            # do work
    """

    def __init__(self, todo_path: Path) -> None:
        self._todo_path: Path = todo_path.resolve()
        self._lock_path: Path = self._todo_path.parent / _LOCK_FILENAME
        self._acquired: bool = False
        self._reclaimed: dict[str, object] | None = None

    @property
    def lock_path(self) -> Path:
        """Absolute path of the lock file."""
        return self._lock_path

    @property
    def reclaimed_info(self) -> dict[str, object] | None:
        """Non-None if a stale lock was reclaimed during the last acquire().

        Contains ``stale_pid`` and ``stale_run_id`` so the caller can journal
        the event *after* the main journal has been initialised.
        """
        return self._reclaimed

    def acquire(
        self,
        *,
        run_id: str | None = None,
        journal: Journal | None = None,
    ) -> None:
        """Acquire the lock.

        Raises :exc:`AlreadyRunning` if a live lock exists (AC10.5).  A stale
        lock (recorded PID gone) is reclaimed; if *journal* is provided the
        ``lock_reclaimed`` event is appended immediately (AC10.6).  The
        ``already_running`` event is likewise journalled before the exception
        is raised when *journal* is provided.
        """
        self._reclaimed = None

        if self._lock_path.exists():
            existing_pid: int = 0
            existing_run_id: str | None = None
            try:
                data: dict[str, Any] = json.loads(self._lock_path.read_text(encoding="utf-8"))
                existing_pid = int(data.get("pid") or 0)
                existing_run_id = data.get("run_id") or None
            except (json.JSONDecodeError, ValueError, OSError):
                # Corrupt lock file — treat as stale.
                pass

            if existing_pid > 0 and _pid_alive(existing_pid):
                # Live lock — AC10.5.
                if journal is not None:
                    from .journal import ALREADY_RUNNING

                    journal.append(
                        ALREADY_RUNNING,
                        payload={
                            "lock_pid": existing_pid,
                            "lock_run_id": existing_run_id,
                        },
                    )
                raise AlreadyRunning(pid=existing_pid, run_id=existing_run_id)

            # Stale lock — AC10.6.
            stale_info: dict[str, object] = {
                "stale_pid": existing_pid,
                "stale_run_id": existing_run_id,
            }
            self._reclaimed = stale_info
            if journal is not None:
                from .journal import LOCK_RECLAIMED

                journal.append(LOCK_RECLAIMED, payload=stale_info)

        # Write our own lock record.
        lock_data: dict[str, object] = {
            "pid": os.getpid(),
            "run_id": run_id,
            "todo": str(self._todo_path),
        }
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path.write_text(json.dumps(lock_data) + "\n", encoding="utf-8")
        self._acquired = True

    def release(self) -> None:
        """Release (delete) the lock file.  Safe to call multiple times."""
        if not self._acquired:
            return
        self._acquired = False
        try:
            raw = self._lock_path.read_text(encoding="utf-8")
            data: dict[str, Any] = json.loads(raw)
            if int(data.get("pid") or 0) == os.getpid():
                self._lock_path.unlink()
        except (json.JSONDecodeError, ValueError, OSError):
            # Best-effort removal — ignore errors on cleanup.
            self._lock_path.unlink(missing_ok=True)

    def __enter__(self) -> Lock:
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()
