# SPDX-License-Identifier: Apache-2.0
"""Tests for journal.py — append-only journal and replay/resume.

Acceptance criteria exercised here:
- AC-S1  A resumed run does not re-execute any subtask that already has a
         ``passed`` verdict (``subtask_verdicts`` in ReplayState).
- AC-S2  The journal is append-only; a resumed run never rewrites prior lines.
- AC-S3  A corrupt trailing journal line is tolerated (truncated replay) and
         the run resumes from the last intact entry.
- AC-S4  ``now`` and ``tz`` from ``run_started`` are exposed by ReplayState
         so a resumed run can reuse the original eligibility instant.
"""

from __future__ import annotations

import json
from pathlib import Path

from jumar.journal import (
    ATTEMPT_FINISHED,
    ATTEMPT_STARTED,
    ITEM_COMPLETED,
    ITEM_FAILED,
    RUN_STARTED,
    VERIFICATION,
    Journal,
    ReplayState,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RUN_STARTED_PAYLOAD = {"now": "2026-01-01T00:00:00Z", "tz": "UTC"}


def _make_journal(tmp_path: Path, run_id: str = "run-abc123") -> Journal:
    journal_path = tmp_path / "runs" / run_id / "journal.jsonl"
    return Journal(journal_path, run_id)


def _read_lines(path: Path) -> list[str]:
    return [ln for ln in path.read_text().splitlines() if ln.strip()]


# ---------------------------------------------------------------------------
# Basic append
# ---------------------------------------------------------------------------


def test_append_creates_file_and_directory(tmp_path: Path) -> None:
    j = _make_journal(tmp_path)
    j.append(RUN_STARTED, payload={"now": "2026-01-01T00:00:00Z", "tz": "UTC"})
    assert j._path.exists()


def test_append_writes_valid_json(tmp_path: Path) -> None:
    j = _make_journal(tmp_path)
    j.append(RUN_STARTED, payload={"now": "2026-01-01T00:00:00Z", "tz": "UTC"})
    lines = _read_lines(j._path)
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event"] == RUN_STARTED
    assert record["run_id"] == "run-abc123"
    assert "ts" in record
    assert record["seq"] == 1


def test_append_includes_extra_fields(tmp_path: Path) -> None:
    j = _make_journal(tmp_path)
    j.append(ITEM_COMPLETED, item_id="write-tests-a1b2c3d4")
    record = json.loads(_read_lines(j._path)[0])
    assert record["item_id"] == "write-tests-a1b2c3d4"


def test_append_injectable_timestamp(tmp_path: Path) -> None:
    j = _make_journal(tmp_path)
    j.append(RUN_STARTED, ts="2026-01-01T09:00:00Z", payload={})
    record = json.loads(_read_lines(j._path)[0])
    assert record["ts"] == "2026-01-01T09:00:00Z"


# ---------------------------------------------------------------------------
# seq strictly increases
# ---------------------------------------------------------------------------


def test_seq_strictly_increases(tmp_path: Path) -> None:
    j = _make_journal(tmp_path)
    for i in range(5):
        j.append("run_finished", ts=f"2026-01-01T00:0{i}:00Z")
    lines = _read_lines(j._path)
    seqs = [json.loads(ln)["seq"] for ln in lines]
    assert seqs == list(range(1, 6))


def test_seq_resumes_from_existing_file(tmp_path: Path) -> None:
    """A new Journal instance for the same path continues the seq (AC-S2)."""
    j1 = _make_journal(tmp_path)
    j1.append(RUN_STARTED, ts="2026-01-01T00:00:00Z", payload=_RUN_STARTED_PAYLOAD)
    j1.append(ITEM_COMPLETED, ts="2026-01-01T00:01:00Z", item_id="item-a")

    # Open a second instance on the same file.
    j2 = Journal(j1._path, "run-abc123")
    j2.append(RUN_STARTED, ts="2026-01-01T01:00:00Z", payload=_RUN_STARTED_PAYLOAD)

    lines = _read_lines(j1._path)
    assert len(lines) == 3
    seqs = [json.loads(ln)["seq"] for ln in lines]
    assert seqs == [1, 2, 3]


# ---------------------------------------------------------------------------
# Replay — happy path
# ---------------------------------------------------------------------------


def test_replay_empty_file(tmp_path: Path) -> None:
    j = _make_journal(tmp_path)
    # File does not exist yet.
    state = j.replay()
    assert isinstance(state, ReplayState)
    assert state.run_id == "run-abc123"
    assert state.now is None
    assert state.tz is None
    assert state.items_done == frozenset()
    assert state.items_failed == frozenset()
    assert state.subtask_verdicts == {}
    assert state.entries == []
    assert not state.truncated


def test_replay_extracts_now_and_tz(tmp_path: Path) -> None:
    """AC-S4: ReplayState.now and tz come from run_started payload."""
    j = _make_journal(tmp_path)
    j.append(
        RUN_STARTED,
        ts="2026-08-07T09:00:00Z",
        payload={
            "now": "2026-08-07T09:00:00Z",
            "tz": "Australia/Sydney",
            "trigger": "human",
            "interactive": True,
        },
    )
    state = j.replay()
    assert state.now == "2026-08-07T09:00:00Z"
    assert state.tz == "Australia/Sydney"


def test_replay_tracks_items_done(tmp_path: Path) -> None:
    j = _make_journal(tmp_path)
    j.append(RUN_STARTED, ts="2026-01-01T00:00:00Z", payload=_RUN_STARTED_PAYLOAD)
    j.append(ITEM_COMPLETED, ts="2026-01-01T00:02:00Z", item_id="item-alpha")
    j.append(ITEM_COMPLETED, ts="2026-01-01T00:04:00Z", item_id="item-beta")
    state = j.replay()
    assert state.items_done == frozenset({"item-alpha", "item-beta"})
    assert state.items_failed == frozenset()


def test_replay_tracks_items_failed(tmp_path: Path) -> None:
    j = _make_journal(tmp_path)
    j.append(RUN_STARTED, ts="2026-01-01T00:00:00Z", payload=_RUN_STARTED_PAYLOAD)
    j.append(ITEM_FAILED, ts="2026-01-01T00:03:00Z", item_id="item-gamma")
    state = j.replay()
    assert state.items_failed == frozenset({"item-gamma"})
    assert "item-gamma" not in state.items_done


def test_replay_tracks_subtask_verdicts(tmp_path: Path) -> None:
    """AC-S1: passed subtask verdicts are recorded for resume."""
    j = _make_journal(tmp_path)
    j.append(RUN_STARTED, ts="2026-01-01T00:00:00Z", payload=_RUN_STARTED_PAYLOAD)
    j.append(
        VERIFICATION,
        ts="2026-01-01T00:01:00Z",
        subtask_id="item-1#0",
        payload={"verdict": "passed", "summary": "tests green"},
    )
    j.append(
        VERIFICATION,
        ts="2026-01-01T00:02:00Z",
        subtask_id="item-1#1",
        payload={"verdict": "failed", "summary": "exit 1"},
    )
    state = j.replay()
    assert state.subtask_verdicts["item-1#0"] == "passed"
    assert state.subtask_verdicts["item-1#1"] == "failed"


def test_replay_returns_all_entries(tmp_path: Path) -> None:
    j = _make_journal(tmp_path)
    j.append(RUN_STARTED, ts="2026-01-01T00:00:00Z", payload=_RUN_STARTED_PAYLOAD)
    j.append(ITEM_COMPLETED, ts="2026-01-01T00:01:00Z", item_id="item-a")
    state = j.replay()
    assert len(state.entries) == 2
    assert state.entries[0]["event"] == RUN_STARTED
    assert state.entries[1]["event"] == ITEM_COMPLETED


# ---------------------------------------------------------------------------
# AC-S1 — resume does not re-execute passed subtasks
# ---------------------------------------------------------------------------


def test_resume_passed_subtasks_identified(tmp_path: Path) -> None:
    """AC-S1: a caller can skip subtasks whose id is in subtask_verdicts with 'passed'."""
    j = _make_journal(tmp_path)
    j.append(RUN_STARTED, ts="2026-01-01T00:00:00Z", payload=_RUN_STARTED_PAYLOAD)
    j.append(ATTEMPT_STARTED, ts="2026-01-01T00:00:30Z", subtask_id="item-1#0")
    j.append(ATTEMPT_FINISHED, ts="2026-01-01T00:01:00Z", subtask_id="item-1#0")
    j.append(
        VERIFICATION,
        ts="2026-01-01T00:01:05Z",
        subtask_id="item-1#0",
        payload={"verdict": "passed", "summary": "all good"},
    )
    # Simulated kill — subtask 1 never started.

    state = j.replay()
    # Subtask 0 passed; subtask 1 is absent from verdicts → needs execution.
    assert state.subtask_verdicts.get("item-1#0") == "passed"
    assert "item-1#1" not in state.subtask_verdicts


# ---------------------------------------------------------------------------
# AC-S2 — append-only: resumed run never rewrites prior lines
# ---------------------------------------------------------------------------


def test_append_only_prior_lines_unchanged(tmp_path: Path) -> None:
    """AC-S2: re-opening a journal and appending does not alter existing lines."""
    j1 = _make_journal(tmp_path)
    j1.append(RUN_STARTED, ts="2026-01-01T00:00:00Z", payload=_RUN_STARTED_PAYLOAD)
    j1.append(ITEM_COMPLETED, ts="2026-01-01T00:01:00Z", item_id="item-a")

    original_lines = _read_lines(j1._path)

    j2 = Journal(j1._path, "run-abc123")
    j2.append(ITEM_FAILED, ts="2026-01-01T01:00:00Z", item_id="item-b")

    all_lines = _read_lines(j1._path)
    # The first two lines are byte-identical to the originals.
    assert all_lines[:2] == original_lines
    assert len(all_lines) == 3


# ---------------------------------------------------------------------------
# AC-S3 — corrupt trailing line is tolerated
# ---------------------------------------------------------------------------


def test_replay_corrupt_trailing_line_sets_truncated(tmp_path: Path) -> None:
    """AC-S3: replay stops at a corrupt line and sets truncated=True."""
    j = _make_journal(tmp_path)
    j.append(RUN_STARTED, ts="2026-01-01T00:00:00Z", payload=_RUN_STARTED_PAYLOAD)
    j.append(ITEM_COMPLETED, ts="2026-01-01T00:01:00Z", item_id="item-a")

    # Append a corrupt (truncated) JSON line simulating a crash mid-write.
    with j._path.open("a") as fh:
        fh.write('{"seq": 3, "event": "item_completed", "item_id": "item-b"')

    state = j.replay()
    assert state.truncated is True


def test_replay_corrupt_trailing_line_entries_before_preserved(tmp_path: Path) -> None:
    """AC-S3: entries before the corrupt line are still included in the replay."""
    j = _make_journal(tmp_path)
    j.append(RUN_STARTED, ts="2026-01-01T00:00:00Z", payload=_RUN_STARTED_PAYLOAD)
    j.append(ITEM_COMPLETED, ts="2026-01-01T00:01:00Z", item_id="item-a")
    with j._path.open("a") as fh:
        fh.write("{CORRUPT}")

    state = j.replay()
    assert len(state.entries) == 2
    assert state.entries[0]["event"] == RUN_STARTED
    assert state.entries[1]["event"] == ITEM_COMPLETED
    # item-a was completed before the corrupt line.
    assert "item-a" in state.items_done


def test_replay_corrupt_trailing_line_resume_continues(tmp_path: Path) -> None:
    """AC-S3: a Journal re-opened on a truncated file can safely append."""
    j = _make_journal(tmp_path)
    j.append(RUN_STARTED, ts="2026-01-01T00:00:00Z", payload=_RUN_STARTED_PAYLOAD)
    with j._path.open("a") as fh:
        fh.write("{CORRUPT}\n")

    # A new Journal instance seeds seq from the highest valid line.
    j2 = Journal(j._path, "run-abc123")
    j2.append(ITEM_COMPLETED, ts="2026-01-01T00:02:00Z", item_id="item-a")
    lines = _read_lines(j._path)
    last = json.loads(lines[-1])
    # seq must be > the last valid seq (1), regardless of the corrupt line.
    assert last["seq"] == 2


# ---------------------------------------------------------------------------
# AC-S4 — resumed run reuses original now
# ---------------------------------------------------------------------------


def test_resume_reuses_original_now(tmp_path: Path) -> None:
    """AC-S4: now and tz from run_started survive a simulated crash and resume."""
    original_now = "2026-08-07T09:00:00Z"
    original_tz = "Australia/Sydney"

    j = _make_journal(tmp_path)
    j.append(
        RUN_STARTED,
        ts=original_now,
        payload={
            "now": original_now,
            "tz": original_tz,
            "trigger": "human",
            "interactive": True,
        },
    )
    j.append(ATTEMPT_STARTED, ts="2026-08-07T09:00:01Z", subtask_id="item-1#0")
    # Simulated kill — no attempt_finished or verification.

    # Resume: a new Journal is opened on the same path.
    j2 = Journal(j._path, "run-abc123")
    state = j2.replay()

    assert state.now == original_now
    assert state.tz == original_tz
    # The subtask has no verdict — the caller knows to re-execute it.
    assert "item-1#0" not in state.subtask_verdicts
