# SPDX-License-Identifier: Apache-2.0
"""Tests for complete.py — Stage 8: Complete item.

Acceptance criteria covered:
- AC8.1  An item with any non-passed subtask is never marked - [x].
- AC8.2  Flipping the checkbox changes only that line; a byte-diff of the
         rest of the file is empty.
- AC8.3  With commit_on_complete, the commit lands on a per-item branch and
         the base branch's HEAD is unchanged.
- AC8.4  No push and no PR command is ever invoked — asserted by a test that
         fails if "git push" or "gh" appears in the dispatched argv.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from getstuffdone.complete import complete
from getstuffdone.config import Config
from getstuffdone.journal import ITEM_COMPLETED, Journal
from getstuffdone.models import (
    Capability,
    Check,
    CheckKind,
    HarnessInfo,
    ItemStatus,
    Plan,
    Subtask,
    SubtaskStatus,
    TodoItem,
    Verdict,
    VerificationResult,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_harness() -> HarnessInfo:
    return HarnessInfo(agent="claude", model="sonnet", harness="claude", invoked_as="claude")


def _make_check() -> Check:
    return Check(kind=CheckKind.command, statement="Command exits zero", command=("true",))


def _make_subtask(index: int = 0, item_id: str = "write-tests-abc12345") -> Subtask:
    return Subtask(
        subtask_id=f"{item_id}#{index}",
        index=index,
        description=f"Step {index + 1}",
        check=_make_check(),
        capabilities=frozenset({Capability.run_commands}),
        depends_on=(),
        status=SubtaskStatus.passed,
        attempts=(),
    )


def _make_plan(
    item_id: str = "write-tests-abc12345",
    subtask_count: int = 1,
) -> Plan:
    subtasks = tuple(_make_subtask(i, item_id) for i in range(subtask_count))
    return Plan(
        item_id=item_id,
        subtasks=subtasks,
        source="model",
        created_at="2026-08-07T00:00:00Z",
        harness=_make_harness(),
    )


def _make_item(
    text: str = "Write tests",
    item_id: str = "write-tests-abc12345",
    line_no: int = 1,
) -> TodoItem:
    return TodoItem(
        item_id=item_id,
        text=text,
        raw_line=f"- [ ] {text}\n",
        line_no=line_no,
        status=ItemStatus.pending,
        context=(),
        authored_subtasks=(),
        meta={},
        priority=None,
        depends=(),
        capabilities=frozenset({Capability.run_commands}),
        schedule=None,
    )


def _make_verification(
    subtask_id: str = "write-tests-abc12345#0",
    verdict: Verdict = Verdict.passed,
) -> VerificationResult:
    return VerificationResult(
        subtask_id=subtask_id,
        attempt_no=0,
        verdict=verdict,
        kind=CheckKind.command,
        evidence={"exit_status": 0},
        summary="Command exited 0",
        evidence_path=None,
        checked_at="2026-08-07T00:00:01Z",
    )


def _make_passed_verifications(
    item_id: str = "write-tests-abc12345",
    count: int = 1,
) -> tuple[VerificationResult, ...]:
    return tuple(
        _make_verification(subtask_id=f"{item_id}#{i}", verdict=Verdict.passed)
        for i in range(count)
    )


@pytest.fixture
def journal(tmp_path: Path) -> Journal:
    return Journal(tmp_path / "journal.jsonl", "run-01")


@pytest.fixture
def cfg() -> Config:
    return Config(commit_on_complete=False)


@pytest.fixture
def cfg_commit() -> Config:
    return Config(commit_on_complete=True)


# ---------------------------------------------------------------------------
# Helpers for writing a simple todo file
# ---------------------------------------------------------------------------


def _write_todo(path: Path, lines: list[str]) -> bytes:
    """Write lines to *path* and return the raw bytes written."""
    content = "".join(lines)
    data = content.encode("utf-8")
    path.write_bytes(data)
    return data


# ---------------------------------------------------------------------------
# AC8.1 — item with any non-passed subtask is never marked done
# ---------------------------------------------------------------------------


def test_failed_subtask_not_marked_done(tmp_path: Path, journal: Journal, cfg: Config) -> None:
    """An item with a failed subtask is not marked done (AC8.1)."""
    todo_path = tmp_path / "todo.md"
    _write_todo(todo_path, ["- [ ] Write tests\n"])
    item = _make_item(line_no=1)
    plan = _make_plan()
    verifications = (_make_verification(verdict=Verdict.failed),)

    result = complete(
        item,
        plan,
        todo_path=todo_path,
        verifications=verifications,
        config=cfg,
        journal=journal,
        cwd=tmp_path,
    )

    assert result is False
    assert todo_path.read_bytes() == b"- [ ] Write tests\n"


def test_inconclusive_subtask_not_marked_done(
    tmp_path: Path, journal: Journal, cfg: Config
) -> None:
    """An item with an inconclusive subtask is not marked done (AC8.1)."""
    todo_path = tmp_path / "todo.md"
    _write_todo(todo_path, ["- [ ] Write tests\n"])
    item = _make_item(line_no=1)
    plan = _make_plan()
    verifications = (_make_verification(verdict=Verdict.inconclusive),)

    result = complete(
        item,
        plan,
        todo_path=todo_path,
        verifications=verifications,
        config=cfg,
        journal=journal,
        cwd=tmp_path,
    )

    assert result is False
    assert todo_path.read_bytes() == b"- [ ] Write tests\n"


def test_mixed_verdicts_not_marked_done(tmp_path: Path, journal: Journal, cfg: Config) -> None:
    """If any subtask is not passed, the item is not marked done (AC8.1)."""
    todo_path = tmp_path / "todo.md"
    _write_todo(todo_path, ["- [ ] Write tests\n"])
    item = _make_item(line_no=1)
    plan = _make_plan(subtask_count=2)
    verifications = (
        _make_verification(subtask_id="write-tests-abc12345#0", verdict=Verdict.passed),
        _make_verification(subtask_id="write-tests-abc12345#1", verdict=Verdict.failed),
    )

    result = complete(
        item,
        plan,
        todo_path=todo_path,
        verifications=verifications,
        config=cfg,
        journal=journal,
        cwd=tmp_path,
    )

    assert result is False
    assert todo_path.read_bytes() == b"- [ ] Write tests\n"


def test_no_verifications_not_marked_done(tmp_path: Path, journal: Journal, cfg: Config) -> None:
    """An item with no verifications (nothing ran) is not marked done (AC8.1)."""
    todo_path = tmp_path / "todo.md"
    _write_todo(todo_path, ["- [ ] Write tests\n"])
    item = _make_item(line_no=1)
    plan = _make_plan()

    result = complete(
        item,
        plan,
        todo_path=todo_path,
        verifications=(),
        config=cfg,
        journal=journal,
        cwd=tmp_path,
    )

    assert result is False
    assert todo_path.read_bytes() == b"- [ ] Write tests\n"


def test_all_passed_marks_done(tmp_path: Path, journal: Journal, cfg: Config) -> None:
    """An item with all subtasks passed is marked done (AC8.1 positive path)."""
    todo_path = tmp_path / "todo.md"
    _write_todo(todo_path, ["- [ ] Write tests\n"])
    item = _make_item(line_no=1)
    plan = _make_plan()
    verifications = _make_passed_verifications()

    result = complete(
        item,
        plan,
        todo_path=todo_path,
        verifications=verifications,
        config=cfg,
        journal=journal,
        cwd=tmp_path,
    )

    assert result is True
    assert todo_path.read_bytes() == b"- [x] Write tests\n"


# ---------------------------------------------------------------------------
# AC8.2 — checkbox flip is byte-preserving
# ---------------------------------------------------------------------------


def test_only_target_line_changes(tmp_path: Path, journal: Journal, cfg: Config) -> None:
    """Flipping the checkbox changes only that line; the rest is byte-identical (AC8.2)."""
    lines = [
        "# My Tasks\n",
        "\n",
        "Some context text.\n",
        "- [ ] Write tests\n",
        "- [ ] Deploy service\n",
        "\n",
        "Footer line.\n",
    ]
    todo_path = tmp_path / "todo.md"
    original = _write_todo(todo_path, lines)

    item = _make_item(line_no=4)  # "- [ ] Write tests\n" is line 4
    plan = _make_plan()
    verifications = _make_passed_verifications()

    complete(
        item,
        plan,
        todo_path=todo_path,
        verifications=verifications,
        config=cfg,
        journal=journal,
        cwd=tmp_path,
    )

    updated = todo_path.read_bytes()
    updated_lines = updated.splitlines(keepends=True)
    original_lines = original.splitlines(keepends=True)

    # Exactly one line changed — the target line.
    changed = [i for i in range(len(original_lines)) if original_lines[i] != updated_lines[i]]
    assert changed == [3], f"Expected only line index 3 to change, got: {changed}"

    # The changed line is the checkbox flip.
    assert updated_lines[3] == b"- [x] Write tests\n"

    # All other lines are byte-identical.
    for i, (orig, new) in enumerate(zip(original_lines, updated_lines, strict=True)):
        if i != 3:
            assert orig == new, f"Line {i + 1} changed unexpectedly"


def test_indented_checkbox_flipped(tmp_path: Path, journal: Journal, cfg: Config) -> None:
    """An indented checkbox is flipped correctly (AC8.2)."""
    todo_path = tmp_path / "todo.md"
    _write_todo(todo_path, ["  - [ ] Indented item\n"])
    item = _make_item(text="Indented item", line_no=1)
    plan = _make_plan()
    verifications = _make_passed_verifications()

    complete(
        item,
        plan,
        todo_path=todo_path,
        verifications=verifications,
        config=cfg,
        journal=journal,
        cwd=tmp_path,
    )

    assert todo_path.read_bytes() == b"  - [x] Indented item\n"


def test_completion_journalled(tmp_path: Path, journal: Journal, cfg: Config) -> None:
    """ITEM_COMPLETED is written to the journal on successful completion."""
    todo_path = tmp_path / "todo.md"
    _write_todo(todo_path, ["- [ ] Write tests\n"])
    item = _make_item(line_no=1)
    plan = _make_plan()
    verifications = _make_passed_verifications()

    complete(
        item,
        plan,
        todo_path=todo_path,
        verifications=verifications,
        config=cfg,
        journal=journal,
        cwd=tmp_path,
    )

    state = journal.replay()
    events = [e["event"] for e in state.entries]
    assert ITEM_COMPLETED in events

    completed_entry = next(e for e in state.entries if e["event"] == ITEM_COMPLETED)
    assert completed_entry["item_id"] == item.item_id


def test_failed_item_not_journalled_as_complete(
    tmp_path: Path, journal: Journal, cfg: Config
) -> None:
    """ITEM_COMPLETED is NOT written when the item has a non-passed subtask."""
    todo_path = tmp_path / "todo.md"
    _write_todo(todo_path, ["- [ ] Write tests\n"])
    item = _make_item(line_no=1)
    plan = _make_plan()
    verifications = (_make_verification(verdict=Verdict.failed),)

    complete(
        item,
        plan,
        todo_path=todo_path,
        verifications=verifications,
        config=cfg,
        journal=journal,
        cwd=tmp_path,
    )

    state = journal.replay()
    events = [e["event"] for e in state.entries]
    assert ITEM_COMPLETED not in events


# ---------------------------------------------------------------------------
# AC8.3 — commit_on_complete lands on a per-item branch
# ---------------------------------------------------------------------------


def _init_git_repo(path: Path) -> None:
    """Initialise a bare-minimum git repo in *path* with a single commit."""
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@test.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@test.com",
        "HOME": str(path),
    }

    def run(*args: str) -> None:
        subprocess.run(  # noqa: S603
            list(args),
            cwd=path,
            env=env,
            capture_output=True,
            check=True,
            shell=False,
        )

    run("git", "init", "-b", "main")
    run("git", "config", "user.email", "test@test.com")
    run("git", "config", "user.name", "Test")

    # Create an initial commit so we have a base HEAD.
    readme = path / "README.md"
    readme.write_text("# Test\n")
    run("git", "add", "README.md")
    run("git", "commit", "-m", "initial")


def _git_head(path: Path) -> str:
    """Return the current HEAD commit SHA in *path*."""
    result = subprocess.run(  # noqa: S603
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
        shell=False,
    )
    return result.stdout.strip()


def _git_branches(path: Path) -> list[str]:
    """Return a list of local branch names in *path*."""
    result = subprocess.run(  # noqa: S603
        ["git", "branch", "--list", "--format=%(refname:short)"],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
        shell=False,
    )
    return [b for b in result.stdout.splitlines() if b]


def _git_file_on_branch(path: Path, branch: str, filename: str) -> bytes:
    """Return the content of *filename* on *branch*."""
    result = subprocess.run(  # noqa: S603
        ["git", "show", f"{branch}:{filename}"],
        cwd=path,
        capture_output=True,
        check=True,
        shell=False,
    )
    return result.stdout


def test_commit_on_item_branch(tmp_path: Path, journal: Journal, cfg_commit: Config) -> None:
    """commit_on_complete puts the commit on a per-item branch (AC8.3)."""
    _init_git_repo(tmp_path)

    todo_path = tmp_path / "todo.md"
    todo_path.write_bytes(b"- [ ] Write tests\n")

    # Note the base HEAD before calling complete().
    base_head = _git_head(tmp_path)

    item = _make_item(item_id="write-tests-abc12345", line_no=1)
    plan = _make_plan(item_id="write-tests-abc12345")
    verifications = _make_passed_verifications(item_id="write-tests-abc12345")

    result = complete(
        item,
        plan,
        todo_path=todo_path,
        verifications=verifications,
        config=cfg_commit,
        journal=journal,
        cwd=tmp_path,
    )

    assert result is True

    # The item branch must exist.
    branches = _git_branches(tmp_path)
    assert "item/write-tests-abc12345" in branches, f"branches: {branches}"

    # The checkbox on the item branch must be flipped.
    content_on_branch = _git_file_on_branch(tmp_path, "item/write-tests-abc12345", "todo.md")
    assert b"- [x] Write tests" in content_on_branch

    # The base branch HEAD is unchanged (AC8.3).
    assert _git_head(tmp_path) == base_head


def test_base_head_unchanged_after_commit(
    tmp_path: Path, journal: Journal, cfg_commit: Config
) -> None:
    """The main branch's HEAD commit is unchanged after commit_on_complete (AC8.3)."""
    _init_git_repo(tmp_path)
    todo_path = tmp_path / "todo.md"
    todo_path.write_bytes(b"- [ ] Deploy service\n")

    base_head = _git_head(tmp_path)
    item = _make_item(text="Deploy service", item_id="deploy-service-def67890", line_no=1)
    plan = _make_plan(item_id="deploy-service-def67890")
    verifications = _make_passed_verifications(item_id="deploy-service-def67890")

    complete(
        item,
        plan,
        todo_path=todo_path,
        verifications=verifications,
        config=cfg_commit,
        journal=journal,
        cwd=tmp_path,
    )

    assert _git_head(tmp_path) == base_head


# ---------------------------------------------------------------------------
# AC8.4 — no git push or gh is ever dispatched
# ---------------------------------------------------------------------------


def test_no_push_or_gh_dispatched(tmp_path: Path, journal: Journal) -> None:
    """No git push or gh command appears in any dispatched argv (AC8.4)."""
    _init_git_repo(tmp_path)
    todo_path = tmp_path / "todo.md"
    todo_path.write_bytes(b"- [ ] Write tests\n")

    item = _make_item(line_no=1)
    plan = _make_plan()
    verifications = _make_passed_verifications()
    cfg = Config(commit_on_complete=True)

    dispatched: list[list[str]] = []
    real_run = subprocess.run

    def recording_run(argv: Any, **kwargs: Any) -> Any:
        if isinstance(argv, list):
            dispatched.append(list(argv))
        return real_run(argv, **kwargs)

    with patch("getstuffdone.complete.subprocess.run", side_effect=recording_run):
        complete(
            item,
            plan,
            todo_path=todo_path,
            verifications=verifications,
            config=cfg,
            journal=journal,
            cwd=tmp_path,
        )

    for argv in dispatched:
        # git push must never appear (AC8.4)
        assert not (len(argv) >= 2 and argv[0] == "git" and argv[1] == "push"), (
            f"git push found in dispatched argv: {argv}"
        )
        # gh must never appear (AC8.4)
        assert argv[0] != "gh", f"gh found in dispatched argv: {argv}"


def test_no_push_or_gh_without_git_repo(tmp_path: Path, journal: Journal) -> None:
    """AC8.4 holds even when commit_on_complete=True but cwd is not a git repo."""
    # tmp_path is not a git repo — commit_on_complete should skip git ops.
    todo_path = tmp_path / "todo.md"
    todo_path.write_bytes(b"- [ ] Write tests\n")

    item = _make_item(line_no=1)
    plan = _make_plan()
    verifications = _make_passed_verifications()
    cfg = Config(commit_on_complete=True)

    dispatched: list[list[str]] = []
    # Capture the real subprocess.run before patching so the recording wrapper
    # can forward to it without recursing into the patch.
    _real_run = subprocess.run

    def recording_run(argv: Any, **kwargs: Any) -> Any:
        if isinstance(argv, list):
            dispatched.append(list(argv))
        return _real_run(argv, **kwargs)  # noqa: S603

    with patch("getstuffdone.complete.subprocess.run", side_effect=recording_run):
        result = complete(
            item,
            plan,
            todo_path=todo_path,
            verifications=verifications,
            config=cfg,
            journal=journal,
            cwd=tmp_path,
        )

    assert result is True

    for argv in dispatched:
        assert not (len(argv) >= 2 and argv[0] == "git" and argv[1] == "push"), (
            f"git push found: {argv}"
        )
        assert argv[0] != "gh", f"gh found: {argv}"
