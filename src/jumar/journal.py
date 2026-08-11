# SPDX-License-Identifier: Apache-2.0
"""Append-only run journal for Jumar.

Public API
----------
Journal       – appends and replays ``runs/<run-id>/journal.jsonl``.
ReplayState   – reconstructed run state produced by :meth:`Journal.replay`.

Event name constants are exported so callers can use them without string literals.

Journal record schema (one JSON object per line):

    {"ts": "<ISO-8601 UTC>", "seq": <int>, "event": "<name>",
     "run_id": "<id>", [<extra top-level fields>,] ["payload": {…}]}

``seq`` strictly increases within a run.  A corrupt or partial trailing line
is tolerated by replay (AC-S3): the replay stops at the first line that does
not parse and sets ``ReplayState.truncated = True``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Event name constants
# ---------------------------------------------------------------------------

RUN_STARTED = "run_started"
ITEM_SELECTED = "item_selected"
ITEM_DEFERRED = "item_deferred"
PLAN_CREATED = "plan_created"
PLAN_REJECTED = "plan_rejected"
GATE_DECISION = "gate_decision"
ATTEMPT_STARTED = "attempt_started"
ATTEMPT_FINISHED = "attempt_finished"
VERIFICATION = "verification"
REPAIR_STARTED = "repair_started"
ITEM_COMPLETED = "item_completed"
ITEM_FAILED = "item_failed"
SCHEDULE_ADVANCED = "schedule_advanced"
LOCK_RECLAIMED = "lock_reclaimed"
ALREADY_RUNNING = "already_running"
RUN_FINISHED = "run_finished"
FAILURE_COUNT_ADVANCED = "failure_count_advanced"
FAILURE_COUNT_CLEARED = "failure_count_cleared"
ITEM_PARKED = "item_parked"


# ---------------------------------------------------------------------------
# ReplayState
# ---------------------------------------------------------------------------


@dataclass
class ReplayState:
    """State reconstructed from a journal file for resume.

    ``now`` and ``tz`` come from the ``run_started`` payload — a resumed run
    must reuse them rather than re-reading the clock (AC-S4), so that
    eligibility decisions are identical to the original run.

    ``subtask_verdicts`` maps ``subtask_id`` to the latest ``verdict`` string
    (``"passed"`` / ``"failed"`` / ``"inconclusive"``) so the executor can
    skip subtasks that already passed (AC-S1).

    ``truncated`` is ``True`` when replay stopped at a corrupt trailing line
    (AC-S3); the entries before it are still usable.
    """

    run_id: str
    now: str | None
    tz: str | None
    items_done: frozenset[str]
    items_failed: frozenset[str]
    subtask_verdicts: dict[str, str]
    entries: list[dict[str, Any]]
    truncated: bool


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------


class Journal:
    """Append-only journal for one jumar run.

    Each :meth:`append` call adds exactly one JSON line to the file.
    ``seq`` strictly increases within a run: the counter is seeded from the
    highest ``seq`` found in the file on construction, so a ``Journal``
    reopened for a resumed run continues from where it left off without
    ever overwriting existing lines (AC-S2).

    The ``ts`` parameter to :meth:`append` is injectable for tests.  When
    omitted it defaults to the current UTC time.  Journal entry timestamps
    are distinct from the eligibility ``now`` managed by ``clock.py``; this
    is the one module other than ``clock.py`` that is permitted to read the
    wall clock, and only for audit-log purposes.
    """

    def __init__(self, path: Path, run_id: str) -> None:
        self._path = path
        self._run_id = run_id
        self._seq: int = self._read_last_seq()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _read_last_seq(self) -> int:
        """Return the highest ``seq`` found in the file, or 0 if absent/empty."""
        if not self._path.exists():
            return 0
        last = 0
        try:
            with self._path.open(encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        entry = json.loads(raw)
                        seq = entry.get("seq")
                        if isinstance(seq, int) and seq > last:
                            last = seq
                    except (json.JSONDecodeError, AttributeError):
                        pass  # tolerate corrupt lines when seeding the counter
        except OSError:
            pass
        return last

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append(
        self,
        event: str,
        *,
        ts: str | None = None,
        payload: dict[str, Any] | None = None,
        **extra: Any,
    ) -> None:
        """Append one event record to the journal.

        Parameters
        ----------
        event:
            One of the event name constants defined in this module.
        ts:
            ISO-8601 UTC timestamp.  Defaults to ``datetime.now(UTC)``
            when not provided (injectable for tests).
        payload:
            Event-specific data dict, written under the ``"payload"`` key.
        **extra:
            Additional top-level fields (e.g. ``item_id``, ``subtask_id``).
        """
        if ts is None:
            ts = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        self._seq += 1
        record: dict[str, Any] = {
            "ts": ts,
            "seq": self._seq,
            "event": event,
            "run_id": self._run_id,
            **extra,
        }
        if payload is not None:
            record["payload"] = payload
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")

    def replay(self) -> ReplayState:
        """Replay the journal and return the reconstructed run state.

        Stops at the first unparseable line and sets ``truncated=True``
        (AC-S3).  Lines before the corrupt one are included in ``entries``.
        """
        entries: list[dict[str, Any]] = []
        truncated = False
        run_now: str | None = None
        run_tz: str | None = None
        items_done: set[str] = set()
        items_failed: set[str] = set()
        subtask_verdicts: dict[str, str] = {}

        if not self._path.exists():
            return ReplayState(
                run_id=self._run_id,
                now=None,
                tz=None,
                items_done=frozenset(),
                items_failed=frozenset(),
                subtask_verdicts={},
                entries=[],
                truncated=False,
            )

        with self._path.open(encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.rstrip("\n")
                if not raw:
                    continue
                try:
                    entry: dict[str, Any] = json.loads(raw)
                except json.JSONDecodeError:
                    # Corrupt line — stop here (AC-S3).
                    truncated = True
                    break

                entries.append(entry)
                event = entry.get("event")
                payload: dict[str, Any] = entry.get("payload") or {}

                if event == RUN_STARTED:
                    run_now = payload.get("now")
                    run_tz = payload.get("tz")
                elif event == ITEM_COMPLETED:
                    item_id = entry.get("item_id")
                    if isinstance(item_id, str):
                        items_done.add(item_id)
                elif event == ITEM_FAILED:
                    item_id = entry.get("item_id")
                    if isinstance(item_id, str):
                        items_failed.add(item_id)
                elif event == VERIFICATION:
                    subtask_id = entry.get("subtask_id")
                    verdict = payload.get("verdict")
                    if isinstance(subtask_id, str) and isinstance(verdict, str):
                        subtask_verdicts[subtask_id] = verdict

        return ReplayState(
            run_id=self._run_id,
            now=run_now,
            tz=run_tz,
            items_done=frozenset(items_done),
            items_failed=frozenset(items_failed),
            subtask_verdicts=subtask_verdicts,
            entries=entries,
            truncated=truncated,
        )
