# SPDX-License-Identifier: Apache-2.0
"""Stage 8 — Complete: flip the checkbox and optionally commit on a per-item branch.

Public API
----------
CompleteError  – raised when the todo file cannot be read or modified.
complete()     – mark an item done (all subtasks must have passed), flip the
                 checkbox in the todo file, and optionally commit on a per-item
                 git branch. Returns True when the item is marked done, False
                 when any subtask has a non-passed verdict (AC8.1).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .clock import stamp
from .config import Config
from .journal import ITEM_COMPLETED, Journal
from .models import Plan, TodoItem, Verdict, VerificationResult  # noqa: TCH001

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
) -> bool:
    """Mark *item* complete when every subtask has a passed verdict.

    Returns ``True`` when the checkbox is flipped (every verification passed).
    Returns ``False`` immediately — without touching the file — when any
    subtask has a non-passed verdict or there are no verifications (AC8.1).

    When ``config.commit_on_complete`` is ``True`` and *cwd* is inside a git
    repository the change is committed on a per-item branch named
    ``item/<item_id>``, with the item text as the commit subject (AC8.3).
    The base branch's HEAD is left unchanged. No push and no PR command is
    ever invoked (AC8.4).
    """
    # AC8.1: all subtasks must have a passed verdict; empty means nothing ran.
    if not verifications or not all(v.verdict == Verdict.passed for v in verifications):
        return False

    # Flip the checkbox in place (AC8.2).
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
        },
    )

    # Optional per-item branch commit (AC8.3, AC8.4).
    if config.commit_on_complete and _is_git_repo(cwd):
        _commit_on_item_branch(todo_path, item, cwd)

    return True
