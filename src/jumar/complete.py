# SPDX-License-Identifier: Apache-2.0
"""Stage 8 — Complete: flip the checkbox and optionally commit on a per-item branch.

Public API
----------
CompleteError  – raised when the todo file cannot be read or modified.
complete()     – mark an item done (all subtasks must have passed), flip the
                 checkbox in the todo file (non-recurring) or advance the
                 @not-before= token (recurring), and optionally commit on a
                 per-item git branch. Returns True when the item is marked
                 done, False when any subtask has a non-passed verdict (AC8.1).
"""

from __future__ import annotations

import re
import subprocess
import zoneinfo
from datetime import datetime
from datetime import time as dt_time
from pathlib import Path

from .clock import capture_now, stamp
from .config import Config
from .journal import ITEM_COMPLETED, SCHEDULE_ADVANCED, Journal
from .models import Plan, TodoItem, Verdict, VerificationResult  # noqa: TCH001
from .recurrence import next_occurrence

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class CompleteError(Exception):
    """Raised when the todo file cannot be read or modified."""


# ---------------------------------------------------------------------------
# Checkbox flip (byte-preserving, AC8.2)
# ---------------------------------------------------------------------------

# Matches the unchecked checkbox inside a GFM task list line:
#   optional indent, dash, whitespace+, "[", space, "]"
# Capture groups: everything up to and including "[ " becomes \1, the "]" is \2.
_CHECKBOX_RE = re.compile(rb"(\s*-\s+\[) (\])")


def _flip_line(line: bytes) -> bytes:
    """Return *line* with the first unchecked checkbox flipped to checked.

    Only the space inside ``[ ]`` is changed; nothing else on the line moves.
    If no unchecked checkbox is found the line is returned unchanged.
    """
    return _CHECKBOX_RE.sub(rb"\1x\2", line, count=1)


def _flip_checkbox(todo_path: Path, item: TodoItem) -> None:
    """Rewrite *todo_path* flipping only the checkbox on *item*'s line (AC8.2).

    All other bytes in the file are preserved exactly.
    """
    try:
        data = todo_path.read_bytes()
    except OSError as exc:
        raise CompleteError(f"Cannot read {todo_path}: {exc}") from exc

    lines = data.splitlines(keepends=True)
    idx = item.line_no - 1  # line_no is 1-indexed

    if idx < 0 or idx >= len(lines):
        raise CompleteError(
            f"Item line_no={item.line_no} is out of range for {todo_path} "
            f"(file has {len(lines)} lines)"
        )

    lines[idx] = _flip_line(lines[idx])

    try:
        todo_path.write_bytes(b"".join(lines))
    except OSError as exc:
        raise CompleteError(f"Cannot write {todo_path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Git helpers — per-item branch commit (AC8.3, AC8.4)
# ---------------------------------------------------------------------------


def _git_run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a git command with shell=False (AC8.4 — never git push or gh)."""
    return subprocess.run(  # noqa: S603
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
        shell=False,
        check=False,
    )


def _is_git_repo(cwd: Path) -> bool:
    """Return True iff *cwd* is inside a git repository."""
    try:
        result = _git_run(["git", "rev-parse", "--is-inside-work-tree"], cwd)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _current_branch(cwd: Path) -> str | None:
    """Return the name of the current branch, or None in detached-HEAD state."""
    try:
        result = _git_run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd)
        if result.returncode == 0:
            branch = result.stdout.strip()
            return branch if branch and branch != "HEAD" else None
        return None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _commit_on_item_branch(
    todo_path: Path,
    item: TodoItem,
    cwd: Path,
) -> None:
    """Stage and commit the checkbox flip on a per-item branch (AC8.3, AC8.4).

    Steps:
    1. Note the current branch (base).
    2. Create ``item/<item_id>`` from the current HEAD and switch to it.
    3. Stage the todo file (the checkbox has already been written to disk).
    4. Commit with the item text as the subject.
    5. Switch back to the base branch so the base HEAD is unchanged (AC8.3).

    Git and gh push commands are never invoked (AC8.4).
    Git failures are silently tolerated — the completion is already journalled.
    """
    base = _current_branch(cwd)
    branch = f"item/{item.item_id}"

    # Create a new branch from the current HEAD and switch to it.
    _git_run(["git", "checkout", "-b", branch], cwd)

    # Stage the todo file (the checkbox has already been written by _flip_checkbox).
    _git_run(["git", "add", str(todo_path.resolve())], cwd)

    # Commit with the item text as the subject.
    _git_run(["git", "commit", "-m", item.text], cwd)

    # Switch back to the original branch so the base HEAD is unchanged (AC8.3).
    if base:
        _git_run(["git", "checkout", base], cwd)


# ---------------------------------------------------------------------------
# @not-before= token rewrite helpers (AC8.5)
# ---------------------------------------------------------------------------

# Matches the @not-before=VALUE token within a line.
_NOT_BEFORE_RE = re.compile(r"(@not-before=)(\S+)")


def _extract_time_of_day(literal: str | None) -> dt_time | None:
    """Return the wall-clock time from a *not_before_literal*, or None for midnight.

    Accepts ``YYYY-MM-DD`` (returns None → midnight) and
    ``YYYY-MM-DDTHH:MM`` / ``YYYY-MM-DDTHH:MM:SS`` (returns the time part).
    """
    if literal is None or "T" not in literal:
        return None
    time_part = literal.split("T", 1)[1].rstrip("Z")
    parts = time_part.split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        s = int(parts[2]) if len(parts) > 2 else 0
        return dt_time(h, m, s)
    except (ValueError, IndexError):
        return None


def _format_not_before(next_utc: datetime, tz: zoneinfo.ZoneInfo, has_time: bool) -> str:
    """Format *next_utc* as a ``@not-before=`` literal in local time.

    Produces ``YYYY-MM-DDTHH:MM`` when *has_time* is True, else ``YYYY-MM-DD``.
    """
    local = next_utc.astimezone(tz)
    if has_time:
        return local.strftime("%Y-%m-%dT%H:%M")
    return local.strftime("%Y-%m-%d")


def _advance_not_before(todo_path: Path, item: TodoItem, new_literal: str) -> None:
    """Rewrite the ``@not-before=`` token on *item*'s line to *new_literal*.

    If the line already carries ``@not-before=``, only that token's value is
    changed.  If the line has no ``@not-before=``, the token is appended
    before the line ending.  All other bytes in the file are preserved
    exactly (AC8.5).
    """
    try:
        data = todo_path.read_bytes()
    except OSError as exc:
        raise CompleteError(f"Cannot read {todo_path}: {exc}") from exc

    lines = data.splitlines(keepends=True)
    idx = item.line_no - 1

    if idx < 0 or idx >= len(lines):
        raise CompleteError(
            f"Item line_no={item.line_no} is out of range for {todo_path} "
            f"(file has {len(lines)} lines)"
        )

    line_text = lines[idx].decode("utf-8")

    if _NOT_BEFORE_RE.search(line_text):
        # Replace only the value of the existing @not-before= token.
        new_token_value = new_literal

        def _replace(m: re.Match[str]) -> str:
            return m.group(1) + new_token_value

        updated_text = _NOT_BEFORE_RE.sub(_replace, line_text)
    else:
        # Append @not-before=VALUE before the line ending.
        stripped = line_text.rstrip("\r\n")
        ending = line_text[len(stripped) :]
        updated_text = stripped + " @not-before=" + new_literal + ending

    lines[idx] = updated_text.encode("utf-8")

    try:
        todo_path.write_bytes(b"".join(lines))
    except OSError as exc:
        raise CompleteError(f"Cannot write {todo_path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def complete(
    item: TodoItem,
    plan: Plan,
    *,
    todo_path: Path,
    verifications: tuple[VerificationResult, ...],
    config: Config,
    journal: Journal,
    cwd: Path,
    now: datetime | None = None,
) -> bool:
    """Mark *item* complete when every subtask has a passed verdict.

    Returns ``True`` when the completion is recorded (every verification
    passed).  Returns ``False`` immediately — without touching the file —
    when any subtask has a non-passed verdict or there are no verifications
    (AC8.1).

    **Non-recurring items** (no ``@every=``): the todo file's checkbox is
    flipped ``- [ ]`` → ``- [x]``, in place, preserving the rest of the file
    byte for byte (AC8.2).

    **Recurring items** (``@every=`` present): the checkbox stays unchecked
    and the line's ``@not-before=`` token is rewritten to the next occurrence,
    computed from *now* and the recurrence rule (AC8.5).  A missed occurrence
    advances to the single next occurrence after *now*, never a backlog
    (AC8.6).  A failed recurring item's schedule is left untouched — this
    function returns ``False`` before any file write (AC8.7).

    When ``config.commit_on_complete`` is ``True`` and *cwd* is inside a git
    repository the change is committed on a per-item branch named
    ``item/<item_id>``, with the item text as the commit subject (AC8.3).
    The base branch's HEAD is left unchanged.  No push and no PR command is
    ever invoked (AC8.4).

    Parameters
    ----------
    now:
        The run's eligibility instant (UTC-aware).  Required for recurring
        items to compute the next occurrence.  When ``None``, ``clock.py``
        is consulted (injecting this value in tests avoids real clock reads).
    """
    # AC8.1: all subtasks must have a passed verdict; empty means nothing ran.
    if not verifications or not all(v.verdict == Verdict.passed for v in verifications):
        return False

    schedule = item.schedule
    is_recurring = schedule is not None and schedule.recur is not None

    if is_recurring:
        # AC8.5: leave the checkbox unchecked; advance @not-before= instead.
        assert schedule is not None  # guarded above
        assert schedule.recur is not None

        effective_now: datetime = now if now is not None else capture_now(config)
        tz = zoneinfo.ZoneInfo(schedule.tz)
        time_of_day = _extract_time_of_day(schedule.not_before_literal)
        next_utc = next_occurrence(schedule.recur, effective_now, tz, time_of_day)

        has_time = schedule.not_before_literal is not None and "T" in schedule.not_before_literal
        new_literal = _format_not_before(next_utc, tz, has_time)

        _advance_not_before(todo_path, item, new_literal)

        journal.append(
            SCHEDULE_ADVANCED,
            item_id=item.item_id,
            payload={
                "advanced_at": stamp(),
                "next_not_before": new_literal,
                "recur_unit": schedule.recur.unit.value,
                "recur_interval": schedule.recur.interval,
            },
        )
    else:
        # Non-recurring: flip the checkbox in place (AC8.2).
        _flip_checkbox(todo_path, item)

    # Journal the completion before the optional git commit so the durable
    # record exists even if git operations fail.
    journal.append(
        ITEM_COMPLETED,
        item_id=item.item_id,
        payload={
            "completed_at": stamp(),
            "plan_source": plan.source,
            "subtask_count": len(plan.subtasks),
            "commit_on_complete": config.commit_on_complete,
            "recurring": is_recurring,
        },
    )

    # Optional per-item branch commit (AC8.3, AC8.4).
    if config.commit_on_complete and _is_git_repo(cwd):
        _commit_on_item_branch(todo_path, item, cwd)

    return True
