# SPDX-License-Identifier: Apache-2.0
"""``jumar prune`` — retire old run folders under ``runs/``.

Every ``jumar plan --dry-run``, ``jumar run`` and ``jumar resume`` leaves a
``runs/<run-id>/`` folder, so a list run on a schedule grows by one folder per
firing. Nothing removed them. This module does, by age or by count, and only
when asked: with no selector the default keeps everything.

What is never removed
---------------------
- A run with no ``run_finished`` event. It may be in progress, or it may be
  one ``jumar resume`` could continue. ``--include-unfinished`` lifts this.
- The latest run of every item that is still open in the todo file. That run
  is what ``jumar status`` reports as the item's last attempt, and what a
  retry would resume from.
- A folder with no readable ``run_started`` event. Its age is unknown, and
  unknown is not old.

Dry runs
--------
A ``jumar plan --dry-run`` or ``jumar run --dry-run`` folder records a choice
or a plan, never work, so it is the first thing to go. ``--keep N`` keeps the
newest N *real* runs and removes every dry run; ``--dry-runs`` removes only
dry runs. ``--keep-dry-runs`` makes ``--keep`` treat them like any other run.

Public API
----------
RunInfo        – what prune knows about one run folder.
PruneDecision  – one folder, kept or removed, with the reason.
PruneResult    – the full outcome, serialisable for ``--json``.
scan_runs()    – read every run folder's journal head.
protected_run_ids() – the runs the todo file still points at.
plan_prune()   – decide, without touching anything.
apply_prune()  – remove the folders and rewrite ``runs/index.tsv``.
parse_age()    – ``30d`` / ``2w`` / ``12h`` to a timedelta.
"""

from __future__ import annotations

import dataclasses
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .index import FILENAME as INDEX_FILENAME

DRY_RUN_MODE = "dry-run"

_AGE_RE = re.compile(r"^(\d+)([hdw])$")
_AGE_UNITS = {"h": timedelta(hours=1), "d": timedelta(days=1), "w": timedelta(weeks=1)}


class PruneError(Exception):
    """A usage error: nothing selected, or a selector that cannot be read."""


@dataclass
class RunInfo:
    """What prune knows about one ``runs/<run-id>/`` folder."""

    run_id: str
    path: Path
    started: datetime | None  # None when run_started is missing or unreadable
    finished: bool
    mode: str | None

    @property
    def is_dry_run(self) -> bool:
        return self.mode == DRY_RUN_MODE


@dataclass
class PruneDecision:
    run_id: str
    action: str  # remove | keep
    reason: str
    started: str | None = None
    mode: str | None = None


@dataclass
class PruneResult:
    runs_dir: str
    listed_only: bool
    removed: list[PruneDecision] = field(default_factory=list)
    kept: list[PruneDecision] = field(default_factory=list)


def parse_age(text: str) -> timedelta:
    """``30d``, ``2w`` or ``12h`` as a timedelta; anything else is a PruneError."""
    m = _AGE_RE.match(text.strip())
    if m is None or int(m.group(1)) == 0:
        raise PruneError(f"cannot read age {text!r}: use a positive count of h, d or w, e.g. 30d")
    return int(m.group(1)) * _AGE_UNITS[m.group(2)]


def _read_journal_head(path: Path) -> tuple[datetime | None, bool, str | None]:
    """(started, finished, mode) from a journal, tolerating corrupt lines."""
    started: datetime | None = None
    finished = False
    mode: str | None = None
    try:
        with path.open(encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                event = entry.get("event")
                if event == "run_started" and started is None:
                    payload = entry.get("payload") or {}
                    started = _parse_instant(payload.get("now"))
                    raw_mode = payload.get("mode")
                    mode = str(raw_mode) if raw_mode is not None else None
                elif event == "run_finished":
                    finished = True
    except OSError:
        pass
    return started, finished, mode


def _parse_instant(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def scan_runs(runs_dir: Path) -> list[RunInfo]:
    """Every run folder under *runs_dir*, oldest first; unknown age sorts first."""
    runs: list[RunInfo] = []
    if not runs_dir.is_dir():
        return runs
    for entry in sorted(runs_dir.iterdir()):
        if not entry.is_dir():
            continue
        journal = entry / "journal.jsonl"
        if journal.exists():
            started, finished, mode = _read_journal_head(journal)
        else:
            started, finished, mode = None, False, None
        runs.append(RunInfo(entry.name, entry, started, finished, mode))
    runs.sort(key=lambda r: (r.started is not None, r.started or datetime.min.replace(tzinfo=UTC)))
    return runs


def protected_run_ids(todo_path: Path, runs_dir: Path, config: Any, now: datetime) -> set[str]:
    """The latest run of every item that is not ticked in the todo file."""
    from .status import build_status

    report = build_status(todo_path, runs_dir, config, now)
    return {
        line.last_run_id
        for line in report.items
        if line.last_run_id is not None and line.state != "done"
    }


def plan_prune(
    runs: list[RunInfo],
    *,
    now: datetime,
    keep: int | None = None,
    older_than: timedelta | None = None,
    dry_runs_only: bool = False,
    keep_dry_runs: bool = False,
    include_unfinished: bool = False,
    protected: frozenset[str] | set[str] = frozenset(),
) -> list[PruneDecision]:
    """Decide what to remove. Touches nothing.

    Exactly the runs a selector names are removed, minus the protected ones.
    With no selector at all this is a PruneError: the default keeps everything.
    """
    if keep is None and older_than is None and not dry_runs_only:
        raise PruneError(
            "nothing selected: pass --keep N, --older-than AGE or --dry-runs "
            "(the default keeps every run)"
        )
    if keep is not None and keep < 0:
        raise PruneError("--keep must be zero or more")

    cutoff = now - older_than if older_than is not None else None
    decisions: list[PruneDecision] = []

    # Newest-first order of the real runs decides which --keep protects.
    real_runs_newest_first = sorted(
        (r for r in runs if r.started is not None and not (r.is_dry_run and not keep_dry_runs)),
        key=lambda r: r.started or now,
        reverse=True,
    )
    kept_by_count = (
        {r.run_id for r in real_runs_newest_first[: keep or 0]} if keep is not None else set()
    )

    for run in runs:
        started_text = run.started.isoformat() if run.started else None

        def decide(
            action: str, reason: str, run: RunInfo = run, when: str | None = started_text
        ) -> None:
            decisions.append(PruneDecision(run.run_id, action, reason, when, run.mode))

        if run.started is None:
            decide("keep", "no readable run_started event, age unknown")
            continue

        # First: does a selector name this run at all?
        selected = False
        why = ""
        if dry_runs_only:
            if run.is_dry_run:
                selected, why = True, "dry run"
        else:
            if run.is_dry_run and not keep_dry_runs and keep is not None:
                selected, why = True, "dry run (never counted by --keep)"
            if not selected and cutoff is not None and run.started < cutoff:
                selected, why = True, f"started before {cutoff.isoformat()}"
            if not selected and keep is not None and run.run_id not in kept_by_count:
                selected, why = True, f"older than the newest {keep}"
        if not selected:
            decide("keep", "not selected")
            continue

        # Then: is it protected anyway?
        if run.run_id in protected:
            decide("keep", "latest run of an open item")
        elif not run.finished and not include_unfinished:
            decide("keep", "no run_finished event (in progress or resumable)")
        else:
            decide("remove", why)
    return decisions


def apply_prune(runs_dir: Path, decisions: list[PruneDecision]) -> list[str]:
    """Remove every ``remove`` folder and drop its row from ``index.tsv``.

    Returns the ids actually removed. A folder that cannot be removed is left
    in place and left out of the returned list; its index row stays too.
    """
    removed: list[str] = []
    for decision in decisions:
        if decision.action != "remove":
            continue
        target = runs_dir / decision.run_id
        try:
            shutil.rmtree(target)
        except OSError:
            continue
        removed.append(decision.run_id)
    if removed:
        _drop_index_rows(runs_dir, set(removed))
    return removed


def _drop_index_rows(runs_dir: Path, run_ids: set[str]) -> None:
    path = runs_dir / INDEX_FILENAME
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        kept = [ln for ln in lines if ln.split("\t", 1)[0] not in run_ids]
        if len(kept) != len(lines):
            path.write_text("".join(kept), encoding="utf-8")
    except OSError:
        pass


def format_prune(result: PruneResult) -> str:
    verb = "Would remove" if result.listed_only else "Removed"
    total = len(result.removed) + len(result.kept)
    lines = [f"{verb} {len(result.removed)} of {total} runs in {result.runs_dir}"]
    for d in result.removed:
        when = f"  started {d.started}" if d.started else ""
        mode = f"  [{d.mode}]" if d.mode else ""
        lines.append(f"  - {d.run_id}{when}{mode}  ({d.reason})")
    protected = [d for d in result.kept if d.reason != "not selected"]
    if protected:
        plural = "" if len(protected) == 1 else "s"
        lines.append(f"Kept {len(protected)} run{plural} a selector would have removed:")
        for d in protected:
            lines.append(f"  - {d.run_id}  ({d.reason})")
    return "\n".join(lines)


def format_prune_json(result: PruneResult) -> str:
    return json.dumps(dataclasses.asdict(result), indent=2)
