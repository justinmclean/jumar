# SPDX-License-Identifier: Apache-2.0
"""Run index: ``runs/index.tsv`` — a fast lookup cache for run metadata.

The index holds one line per run:

    run_id \\t started \\t item_id \\t status

Appended at ``run_started``; the ``status`` (and ``item_id``) columns are
updated in-place at ``run_finished``.  Journals remain the authoritative
record; the index is a cache only.  A missing or corrupt index falls back to
the journal scan without error.
"""

from __future__ import annotations

from pathlib import Path

FILENAME = "index.tsv"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

_SEP = "\t"
_NCOLS = 4  # run_id, started, item_id, status


def _path(runs_dir: Path) -> Path:
    return runs_dir / FILENAME


def append_run_started(runs_dir: Path, run_id: str, started: str) -> None:
    """Append a new row at ``run_started`` (item_id empty, status=in_progress).

    Silently no-ops on any I/O error — the index is a cache, never the record.
    """
    path = _path(runs_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{run_id}{_SEP}{started}{_SEP}{_SEP}{STATUS_IN_PROGRESS}\n")
    except OSError:
        pass


def update_run_finished(
    runs_dir: Path,
    run_id: str,
    item_id: str,
    status: str,
) -> None:
    """Update the ``item_id`` and ``status`` columns for an existing row.

    Silently no-ops when the row is absent (legacy UUID runs that predate the
    index) or on any I/O error.
    """
    path = _path(runs_dir)
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        new_lines: list[str] = []
        updated = False
        for line in lines:
            parts = line.rstrip("\n").split(_SEP)
            if len(parts) == _NCOLS and parts[0] == run_id:
                new_lines.append(f"{run_id}{_SEP}{parts[1]}{_SEP}{item_id}{_SEP}{status}\n")
                updated = True
            else:
                new_lines.append(line)
        if updated:
            path.write_text("".join(new_lines), encoding="utf-8")
    except OSError:
        pass


def resolve_latest_from_index(runs_dir: Path) -> str | None:
    """Return the run_id with the lexicographically highest ``started`` value.

    Returns ``None`` on any error (missing file, corrupt content, no rows) so
    the caller can fall back to the full journal scan.
    """
    path = _path(runs_dir)
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    best_id: str | None = None
    best_started = ""
    for line in text.splitlines():
        parts = line.split(_SEP)
        if len(parts) != _NCOLS:
            continue
        rid, started, _iid, _st = parts
        if rid and started and started > best_started:
            best_started = started
            best_id = rid
    return best_id
