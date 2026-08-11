# SPDX-License-Identifier: Apache-2.0
"""Failure backoff: advance @failed= count and park after max_consecutive_failures.

Public API
----------
BackoffError           – raised when the todo file cannot be read or modified.
advance_failure_count() – increment @failed= on an item's line; append
                          @paused=auto-failures when the threshold is reached.
                          Journal-before-write: the FAILURE_COUNT_ADVANCED event
                          is appended before any file modification.
clear_failure_count()  – remove @failed= from an item's line on success.
                          Journal-before-write: the FAILURE_COUNT_CLEARED event
                          is appended before any file modification.

Rules
-----
- Only the target line changes; every other byte in the file is preserved
  (whole-file diff is exactly one line).
- @paused=auto-failures is appended once, when the count reaches the
  configured max_consecutive_failures threshold.  The human resumes by
  deleting the @paused= token — no new command.
- A successful completion removes @failed= only; @paused= is left for the
  human to remove deliberately.
- --dry-run (dry_run=True) journals the intent but leaves the file byte-identical.
"""

from __future__ import annotations

import re
from pathlib import Path

from .config import Config
from .journal import FAILURE_COUNT_ADVANCED, FAILURE_COUNT_CLEARED, Journal
from .models import TodoItem

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BackoffError(Exception):
    """Raised when the todo file cannot be read or modified."""


# ---------------------------------------------------------------------------
# Line token helpers (byte-preserving)
# ---------------------------------------------------------------------------


def _set_token(line: str, token: str, value: str) -> str:
    """Replace @token=<old> with @token=<value>, or append if absent.

    The line's character content outside the token is byte-identical.
    """
    pattern = re.compile(r"(@" + re.escape(token) + r"=)\S+")
    if pattern.search(line):
        return pattern.sub(r"\g<1>" + value, line)
    # Not present — append before the line ending.
    stripped = line.rstrip("\r\n")
    ending = line[len(stripped) :]
    return stripped + f" @{token}={value}" + ending


def _remove_token(line: str, token: str) -> str:
    """Remove @token=<value> (and any leading whitespace) from *line*.

    If the token is not present the line is returned unchanged.
    """
    return re.sub(r"\s*@" + re.escape(token) + r"=\S+", "", line)


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------


def _read_lines(todo_path: Path) -> list[bytes]:
    try:
        return todo_path.read_bytes().splitlines(keepends=True)
    except OSError as exc:
        raise BackoffError(f"Cannot read {todo_path}: {exc}") from exc


def _write_lines(todo_path: Path, lines: list[bytes]) -> None:
    try:
        todo_path.write_bytes(b"".join(lines))
    except OSError as exc:
        raise BackoffError(f"Cannot write {todo_path}: {exc}") from exc


def _get_line(lines: list[bytes], item: TodoItem) -> tuple[int, str]:
    idx = item.line_no - 1
    if idx < 0 or idx >= len(lines):
        raise BackoffError(
            f"Item line_no={item.line_no} is out of range (file has {len(lines)} lines)"
        )
    return idx, lines[idx].decode("utf-8")


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def advance_failure_count(
    todo_path: Path,
    item: TodoItem,
    config: Config,
    journal: Journal,
    *,
    dry_run: bool = False,
) -> None:
    """Increment the @failed= count on *item*'s todo line.

    When the count reaches ``config.max_consecutive_failures``, also appends
    ``@paused=auto-failures`` to the line so that ``select.py`` excludes the
    item from future runs.

    Parameters
    ----------
    todo_path:
        Path to the todo file containing *item*.
    item:
        The item whose failure count should be incremented.  Its ``meta``
        dict (from ingest) is used to read the current ``@failed=`` value
        without re-parsing the file.
    config:
        Resolved run config — provides ``max_consecutive_failures``.
    journal:
        Append-only run journal.  The ``failure_count_advanced`` event is
        written *before* the file is modified (write-ahead rule).
    dry_run:
        When ``True``, the event is journalled but the file is not touched.
    """
    current_count = int(item.meta.get("failed", "0"))
    new_count = current_count + 1
    threshold_reached = new_count >= config.max_consecutive_failures
    already_paused = "paused" in item.meta

    # Write-ahead: journal before touching the file.
    journal.append(
        FAILURE_COUNT_ADVANCED,
        item_id=item.item_id,
        payload={
            "previous_count": current_count,
            "new_count": new_count,
            "threshold": config.max_consecutive_failures,
            "parked": threshold_reached and not already_paused,
        },
    )

    if dry_run:
        return

    lines = _read_lines(todo_path)
    idx, line = _get_line(lines, item)

    # Increment (or add) the @failed= token.
    line = _set_token(line, "failed", str(new_count))

    # Append @paused= when the threshold is first reached.
    if threshold_reached and not already_paused:
        line = _set_token(line, "paused", "auto-failures")

    lines[idx] = line.encode("utf-8")
    _write_lines(todo_path, lines)


def clear_failure_count(
    todo_path: Path,
    item: TodoItem,
    journal: Journal,
    *,
    dry_run: bool = False,
) -> None:
    """Remove the @failed= token from *item*'s todo line on successful completion.

    This is a no-op when the item has no ``@failed=`` token.  The
    ``@paused=`` token (if any) is left in place — un-parking is a
    deliberate human action (delete the token from the file).

    Parameters
    ----------
    todo_path:
        Path to the todo file containing *item*.
    item:
        The item whose failure count should be cleared.
    journal:
        Append-only run journal.  The ``failure_count_cleared`` event is
        written *before* the file is modified (write-ahead rule).
    dry_run:
        When ``True``, the event is journalled but the file is not touched.
    """
    if "failed" not in item.meta:
        return

    cleared_count = int(item.meta.get("failed", "0"))

    # Write-ahead: journal before touching the file.
    journal.append(
        FAILURE_COUNT_CLEARED,
        item_id=item.item_id,
        payload={"cleared_count": cleared_count},
    )

    if dry_run:
        return

    lines = _read_lines(todo_path)
    idx, line = _get_line(lines, item)

    line = _remove_token(line, "failed")

    lines[idx] = line.encode("utf-8")
    _write_lines(todo_path, lines)
