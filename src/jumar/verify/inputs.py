# SPDX-License-Identifier: Apache-2.0
"""Check-input snapshots: notice when a repair "fixes" a check by changing the world.

A repair is supposed to finish the work. It can instead make the check pass by
rewriting what the check reads: replacing the artefact under test wholesale, or
editing a test file or verification script a ``command`` check runs. The check
still re-runs unchanged (AC7.3), but nothing said the world it reads had been
swapped out underneath it.

So the inputs of every check are hashed when the plan is accepted, and again at
every verification. The verdict's evidence reports, against the previous
attempt (or the plan, for the first one), which inputs were created, removed,
changed, or **replaced wholesale** -- fewer than a fifth of the previous
version's non-blank lines survive. It reports; it does not change the verdict.

What counts as an input:

- ``check.path`` (``file``, ``absence``, ``judge``), unless it is a glob;
- each argv token of a ``command`` check, after the program name or as the
  program itself, that names an existing regular file inside the working
  directory (``tests/test_x.py``, ``./verify.sh``, ``data/out.json``).

Line digests live in a sidecar file per snapshot under ``runs/<id>/artifacts/``
so the journal carries only the SHA-256 map.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..models import Check

# A previous version is "replaced wholesale" when fewer than this share of its
# distinct non-blank lines appear in the new version.
WHOLESALE_SURVIVAL = 0.2
# Line digests are kept for at most this many bytes of a file; the SHA-256
# always covers the whole file.
_LINE_DIGEST_BYTES = 1_000_000
_GLOB_CHARS = frozenset("*?[")

Snapshot = dict[str, dict[str, Any]]  # path -> {"sha256": str | None, "lines": [digest, ...]}


def input_paths(check: Check, cwd: Path) -> list[str]:
    """The files a check reads, as given (relative paths stay relative)."""
    paths: list[str] = []
    if check.path and not (_GLOB_CHARS & set(check.path)):
        paths.append(check.path)
    for token in check.command or ():
        if token.startswith("-") or "=" in token or _GLOB_CHARS & set(token):
            continue
        candidate = Path(token) if Path(token).is_absolute() else cwd / token
        try:
            resolved = candidate.resolve()
            inside = resolved.is_relative_to(cwd.resolve())
        except (OSError, ValueError):
            continue
        if inside and resolved.is_file() and token not in paths:
            paths.append(token)
    return paths


def _digest(path: Path) -> dict[str, Any]:
    try:
        data = path.read_bytes()
    except OSError:
        return {"sha256": None, "lines": []}
    lines = {
        hashlib.sha256(line.strip()).hexdigest()[:16]
        for line in data[:_LINE_DIGEST_BYTES].splitlines()
        if line.strip()
    }
    return {"sha256": hashlib.sha256(data).hexdigest(), "lines": sorted(lines)}


def take_snapshot(paths: list[str], cwd: Path) -> Snapshot:
    return {p: _digest(Path(p) if Path(p).is_absolute() else cwd / p) for p in paths}


def _sidecar(run_dir: Path, subtask_id: str, label: str) -> Path:
    safe_id = subtask_id.replace("#", "-")
    return run_dir / "artifacts" / f"{safe_id}-inputs-{label}.json"


def _write(path: Path, snapshot: Snapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=1, sort_keys=True), encoding="utf-8")


def _read(path: Path) -> Snapshot | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def snapshot_plan(check: Check, *, subtask_id: str, cwd: Path, run_dir: Path) -> dict[str, Any]:
    """Record the plan-time baseline for one subtask; return its SHA-256 map.

    Written once: a resumed run keeps the baseline of the run it resumes.
    """
    sidecar = _sidecar(run_dir, subtask_id, "plan")
    existing = _read(sidecar)
    if existing is None:
        existing = take_snapshot(input_paths(check, cwd), cwd)
        _write(sidecar, existing)
    return {path: entry.get("sha256") for path, entry in existing.items()}


def _baseline(run_dir: Path, subtask_id: str, attempt_no: int) -> tuple[str, Snapshot] | None:
    for n in range(attempt_no - 1, -1, -1):
        found = _read(_sidecar(run_dir, subtask_id, f"attempt-{n}"))
        if found is not None:
            return f"attempt {n}", found
    found = _read(_sidecar(run_dir, subtask_id, "plan"))
    return ("plan", found) if found is not None else None


def compare(before: Snapshot, after: Snapshot) -> dict[str, list[str]]:
    report: dict[str, list[str]] = {
        "created": [],
        "removed": [],
        "changed": [],
        "replaced_wholesale": [],
    }
    for path in sorted(set(before) | set(after)):
        old = before.get(path, {}).get("sha256")
        new = after.get(path, {}).get("sha256")
        if old == new:
            continue
        if old is None:
            report["created"].append(path)
        elif new is None:
            report["removed"].append(path)
        else:
            report["changed"].append(path)
            old_lines = set(before[path].get("lines") or [])
            new_lines = set(after[path].get("lines") or [])
            if old_lines and len(old_lines & new_lines) < WHOLESALE_SURVIVAL * len(old_lines):
                report["replaced_wholesale"].append(path)
    return report


def check_inputs_evidence(
    check: Check, *, subtask_id: str, attempt_no: int, cwd: Path, run_dir: Path
) -> dict[str, Any]:
    """Snapshot the check's inputs for this attempt and report what changed.

    Returns the ``check_inputs`` evidence block: ``since`` names the baseline
    (``plan`` or ``attempt N``), then the created / removed / changed /
    replaced_wholesale path lists. With no baseline on record, ``since`` is
    None and nothing is compared.
    """
    baseline = _baseline(run_dir, subtask_id, attempt_no)
    paths = input_paths(check, cwd)
    if baseline is not None:
        paths += [p for p in baseline[1] if p not in paths]
    current = take_snapshot(paths, cwd)
    _write(_sidecar(run_dir, subtask_id, f"attempt-{attempt_no}"), current)
    if baseline is None:
        return {"since": None, "inputs": sorted(current)}
    since, before = baseline
    return {"since": since, "inputs": sorted(current), **compare(before, current)}
