# SPDX-License-Identifier: Apache-2.0
"""Stage 10 — Run scheduling.

Installs, lists, and removes a recurring ``gsd run`` invocation in the
operating system's own scheduler (cron or launchd).

Public API
----------
ScheduleEntry       – data shape for one installed schedule.
CronExprError       – raised for invalid cron expressions (names the field).
FakeBackend         – in-memory backend for tests.
CronBackend         – reads/writes via ``crontab -l`` / ``crontab -``.
LaunchdBackend      – reads/writes ``~/Library/LaunchAgents/`` plist files.
default_backend()   – platform-appropriate backend.
add_schedule()      – validate, print, and (unless dry-run) install.
list_schedules()    – return all gsd-owned entries from the backend.
remove_schedule()   – delete the gsd block for one schedule id.
show_entry()        – format the entry string without writing it.
"""

from __future__ import annotations

import contextlib
import json
import plistlib
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

# ---------------------------------------------------------------------------
# Data shape
# ---------------------------------------------------------------------------


@dataclass
class ScheduleEntry:
    """One installed schedule entry."""

    schedule_id: str
    cron_expr: str
    todo_path: str
    gsd_path: str
    config_path: str | None
    timezone: str


# ---------------------------------------------------------------------------
# Cron expression validation
# ---------------------------------------------------------------------------

_CRON_FIELD_NAMES = ["minute", "hour", "day-of-month", "month", "day-of-week"]
_CRON_FIELD_RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]

_DOW_ALIASES: dict[str, int] = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}
_MON_ALIASES: dict[str, int] = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


class CronExprError(ValueError):
    """Invalid cron expression; ``field`` names the offending part."""

    def __init__(self, message: str, *, field: str) -> None:
        super().__init__(message)
        self.field = field


def _cron_parse_int(val: str, fname: str, lo: int, hi: int) -> int:
    lower = val.lower()
    if fname == "day-of-week" and lower in _DOW_ALIASES:
        return _DOW_ALIASES[lower]
    if fname == "month" and lower in _MON_ALIASES:
        return _MON_ALIASES[lower]
    try:
        n = int(val)
    except ValueError as exc:
        raise CronExprError(
            f"{fname}: {val!r} is not a valid integer or name", field=fname
        ) from exc
    if not (lo <= n <= hi):
        raise CronExprError(f"{fname}: value {n} out of range [{lo}, {hi}]", field=fname)
    return n


def _validate_cron_field(value: str, fname: str, lo: int, hi: int) -> None:
    if value == "*":
        return
    for atom in value.split(","):
        if "/" in atom:
            base, step_s = atom.split("/", 1)
            try:
                step = int(step_s)
            except ValueError as exc:
                raise CronExprError(
                    f"{fname}: step {step_s!r} is not an integer", field=fname
                ) from exc
            if step < 1:
                raise CronExprError(f"{fname}: step must be >= 1, got {step}", field=fname)
            if base != "*":
                if "-" in base:
                    a, b = base.split("-", 1)
                    _cron_parse_int(a, fname, lo, hi)
                    _cron_parse_int(b, fname, lo, hi)
                else:
                    _cron_parse_int(base, fname, lo, hi)
        elif "-" in atom:
            a, b = atom.split("-", 1)
            lo_n = _cron_parse_int(a, fname, lo, hi)
            hi_n = _cron_parse_int(b, fname, lo, hi)
            if lo_n > hi_n:
                raise CronExprError(f"{fname}: range start {lo_n} > end {hi_n}", field=fname)
        else:
            _cron_parse_int(atom, fname, lo, hi)


def validate_cron(expr: str) -> None:
    """Validate a 5-field cron expression. Raises CronExprError on failure."""
    parts = expr.split()
    if len(parts) != 5:
        raise CronExprError(
            f"expression must have 5 fields, got {len(parts)}: {expr!r}",
            field="expression",
        )
    for i, (fname, (lo, hi)) in enumerate(zip(_CRON_FIELD_NAMES, _CRON_FIELD_RANGES, strict=True)):
        _validate_cron_field(parts[i], fname, lo, hi)


# ---------------------------------------------------------------------------
# Cron text block manipulation (pure functions; no I/O)
# ---------------------------------------------------------------------------

_META_PREFIX = "# gsd-meta: "
_OPEN_PAT = re.compile(r"^# >>> gsd (\S+) >>>$")


def _open_marker(sid: str) -> str:
    return f"# >>> gsd {sid} >>>"


def _close_marker(sid: str) -> str:
    return f"# <<< gsd {sid} <<<"


def _build_command(entry: ScheduleEntry) -> list[str]:
    """Return the argv for the installed gsd run command."""
    cmd = [entry.gsd_path, "run", "--todo", entry.todo_path, "--non-interactive"]
    if entry.config_path:
        cmd += ["--config", entry.config_path]
    return cmd


def _format_cron_block(entry: ScheduleEntry) -> str:
    """Return the full crontab block for one entry, including markers."""
    meta = {
        "schedule_id": entry.schedule_id,
        "cron_expr": entry.cron_expr,
        "tz": entry.timezone,
        "todo_path": entry.todo_path,
    }
    meta_line = _META_PREFIX + json.dumps(meta, separators=(",", ":"))
    cmd = " ".join(_build_command(entry))
    cron_line = f"{entry.cron_expr} {cmd}"
    return "\n".join(
        [
            _open_marker(entry.schedule_id),
            meta_line,
            cron_line,
            _close_marker(entry.schedule_id),
        ]
    )


def _insert_block(current_text: str, entry: ScheduleEntry) -> str:
    """Insert (or replace) the gsd block in crontab text. Returns new text."""
    # Remove any existing block for this id first.
    text, _ = _remove_block(current_text, entry.schedule_id)
    if text and not text.endswith("\n"):
        text += "\n"
    text += _format_cron_block(entry) + "\n"
    return text


def _remove_block(current_text: str, schedule_id: str) -> tuple[str, bool]:
    """Remove the gsd block for schedule_id from crontab text.

    Returns ``(new_text, was_found)``.
    """
    open_m = _open_marker(schedule_id)
    close_m = _close_marker(schedule_id)
    lines = current_text.splitlines(keepends=True)
    out: list[str] = []
    inside = False
    found = False
    for line in lines:
        stripped = line.rstrip("\r\n")
        if stripped == open_m:
            inside = True
            found = True
            continue
        if stripped == close_m:
            inside = False
            continue
        if not inside:
            out.append(line)
    return "".join(out), found


def _parse_blocks(text: str) -> list[ScheduleEntry]:
    """Extract all gsd-owned entries from crontab text."""
    entries: list[ScheduleEntry] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = _OPEN_PAT.match(lines[i])
        if m:
            sid = m.group(1)
            meta_data: dict[str, Any] = {}
            cron_expr = ""
            gsd_path = ""
            close = _close_marker(sid)
            j = i + 1
            while j < len(lines) and lines[j] != close:
                if lines[j].startswith(_META_PREFIX):
                    raw = lines[j][len(_META_PREFIX) :]
                    with contextlib.suppress(json.JSONDecodeError):
                        meta_data = json.loads(raw)
                elif not lines[j].startswith("#") and lines[j].strip():
                    parts = lines[j].split()
                    if len(parts) >= 6:
                        cron_expr = " ".join(parts[:5])
                        gsd_path = parts[5]
                j += 1
            entries.append(
                ScheduleEntry(
                    schedule_id=str(meta_data.get("schedule_id", sid)),
                    cron_expr=str(meta_data.get("cron_expr", cron_expr)),
                    todo_path=str(meta_data.get("todo_path", "")),
                    gsd_path=gsd_path,
                    config_path=None,
                    timezone=str(meta_data.get("tz", "UTC")),
                )
            )
            i = j + 1  # skip the close marker line
        else:
            i += 1
    return entries


# ---------------------------------------------------------------------------
# Backend protocol
# ---------------------------------------------------------------------------


class Backend(Protocol):
    """Protocol for schedule backends."""

    def name(self) -> str: ...

    def add_entry(self, entry: ScheduleEntry) -> None: ...

    def list_entries(self) -> list[ScheduleEntry]: ...

    def remove_entry(self, schedule_id: str) -> bool: ...


# ---------------------------------------------------------------------------
# Fake backend (for tests; exported)
# ---------------------------------------------------------------------------


class FakeBackend:
    """In-memory backend for tests.

    Starts with an optional crontab-style ``initial_text``.  If
    ``fail_on_write`` is True, ``add_entry`` raises ``AssertionError``, which
    lets AC10.1 assert that dry-run never writes.
    """

    def __init__(self, initial_text: str = "", *, fail_on_write: bool = False) -> None:
        self._text = initial_text
        self._fail_on_write = fail_on_write
        self.write_calls: int = 0

    def name(self) -> str:
        return "fake"

    def add_entry(self, entry: ScheduleEntry) -> None:
        if self._fail_on_write:
            raise AssertionError("add_entry must not be called in dry-run mode")
        self._text = _insert_block(self._text, entry)
        self.write_calls += 1

    def list_entries(self) -> list[ScheduleEntry]:
        return _parse_blocks(self._text)

    def remove_entry(self, schedule_id: str) -> bool:
        new_text, found = _remove_block(self._text, schedule_id)
        if found:
            self._text = new_text
        return found

    @property
    def current_text(self) -> str:
        return self._text


# ---------------------------------------------------------------------------
# Cron backend (real; Linux / BSD)
# ---------------------------------------------------------------------------


class CronBackend:
    """Reads and writes the user crontab via ``crontab -l`` / ``crontab -``."""

    def name(self) -> str:
        return "cron"

    def _read(self) -> str:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, shell=False)
        if result.returncode != 0:
            return ""
        return result.stdout

    def _write(self, text: str) -> None:
        subprocess.run(["crontab", "-"], input=text, text=True, check=True, shell=False)

    def add_entry(self, entry: ScheduleEntry) -> None:
        self._write(_insert_block(self._read(), entry))

    def list_entries(self) -> list[ScheduleEntry]:
        return _parse_blocks(self._read())

    def remove_entry(self, schedule_id: str) -> bool:
        text = self._read()
        new_text, found = _remove_block(text, schedule_id)
        if found:
            self._write(new_text)
        return found


# ---------------------------------------------------------------------------
# Launchd backend (real; macOS)
# ---------------------------------------------------------------------------


def _expand_cron_field(field: str, lo: int, hi: int, fname: str) -> list[int | None]:
    """Expand one cron field to a list of integer values (None = any)."""
    if field == "*":
        return [None]
    values: list[int] = []
    for atom in field.split(","):
        if "/" in atom:
            base, step_s = atom.split("/", 1)
            step = int(step_s)
            if base == "*":
                start, end = lo, hi
            elif "-" in base:
                a, b = base.split("-", 1)
                start, end = int(a), int(b)
            else:
                start = end = int(base)
            values.extend(v for v in range(start, end + 1) if (v - start) % step == 0)
        elif "-" in atom:
            a, b = atom.split("-", 1)
            values.extend(range(int(a), int(b) + 1))
        else:
            lower = atom.lower()
            if fname == "day-of-week" and lower in _DOW_ALIASES:
                values.append(_DOW_ALIASES[lower])
            elif fname == "month" and lower in _MON_ALIASES:
                values.append(_MON_ALIASES[lower])
            else:
                values.append(int(atom))
    return list(set(values)) if values else [None]


def _cron_to_launchd_sci(cron_expr: str) -> list[dict[str, int]]:
    """Convert a 5-field cron expression to a launchd StartCalendarInterval list."""
    parts = cron_expr.split()
    if len(parts) != 5:
        return [{}]
    minute_s, hour_s, dom_s, month_s, dow_s = parts

    minutes = _expand_cron_field(minute_s, 0, 59, "minute")
    hours = _expand_cron_field(hour_s, 0, 23, "hour")
    doms = _expand_cron_field(dom_s, 1, 31, "day-of-month")
    months = _expand_cron_field(month_s, 1, 12, "month")
    dows = _expand_cron_field(dow_s, 0, 7, "day-of-week")

    sci: list[dict[str, int]] = []
    for minute in minutes:
        for hour in hours:
            base: dict[str, int] = {}
            if minute is not None:
                base["Minute"] = minute
            if hour is not None:
                base["Hour"] = hour
            for month in months:
                m_entry = dict(base)
                if month is not None:
                    m_entry["Month"] = month
                if dow_s != "*":
                    for dow in dows:
                        e = dict(m_entry)
                        if dow is not None:
                            e["Weekday"] = dow % 7
                        sci.append(e)
                else:
                    for dom in doms:
                        e = dict(m_entry)
                        if dom is not None:
                            e["Day"] = dom
                        sci.append(e)
    return sci if sci else [{}]


class LaunchdBackend:
    """Reads/writes launchd user agents in ``~/Library/LaunchAgents/``."""

    def __init__(self, agents_dir: Path | None = None) -> None:
        self._dir = agents_dir or (Path.home() / "Library" / "LaunchAgents")

    def name(self) -> str:
        return "launchd"

    def _plist_path(self, schedule_id: str) -> Path:
        return self._dir / f"com.gsd.{schedule_id}.plist"

    def add_entry(self, entry: ScheduleEntry) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        sci = _cron_to_launchd_sci(entry.cron_expr)
        plist_data: dict[str, Any] = {
            "Label": f"com.gsd.{entry.schedule_id}",
            "ProgramArguments": _build_command(entry),
            "StartCalendarInterval": sci,
            # Metadata stored for list/remove/show.
            "EnvironmentVariables": {
                "GSD_SCHEDULE_ID": entry.schedule_id,
                "GSD_CRON_EXPR": entry.cron_expr,
                "GSD_TODO_PATH": entry.todo_path,
                "GSD_TIMEZONE": entry.timezone,
            },
        }
        with self._plist_path(entry.schedule_id).open("wb") as fh:
            plistlib.dump(plist_data, fh)

    def list_entries(self) -> list[ScheduleEntry]:
        if not self._dir.is_dir():
            return []
        entries: list[ScheduleEntry] = []
        for plist_path in sorted(self._dir.glob("com.gsd.*.plist")):
            try:
                with plist_path.open("rb") as fh:
                    data: dict[str, Any] = plistlib.load(fh)
                env: dict[str, str] = data.get("EnvironmentVariables", {})
                argv: list[str] = data.get("ProgramArguments", [])
                entries.append(
                    ScheduleEntry(
                        schedule_id=env.get("GSD_SCHEDULE_ID", ""),
                        cron_expr=env.get("GSD_CRON_EXPR", ""),
                        todo_path=env.get("GSD_TODO_PATH", ""),
                        gsd_path=argv[0] if argv else "",
                        config_path=None,
                        timezone=env.get("GSD_TIMEZONE", "UTC"),
                    )
                )
            except Exception:
                continue
        return entries

    def remove_entry(self, schedule_id: str) -> bool:
        path = self._plist_path(schedule_id)
        if not path.exists():
            return False
        path.unlink()
        return True


# ---------------------------------------------------------------------------
# Platform selection
# ---------------------------------------------------------------------------


def default_backend(override: str | None = None) -> CronBackend | LaunchdBackend:
    """Return the platform-appropriate backend (or honour ``override``)."""
    name = override or ("launchd" if sys.platform == "darwin" else "cron")
    if name == "launchd":
        return LaunchdBackend()
    return CronBackend()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _gsd_executable() -> str:
    """Return the absolute path to the gsd executable on PATH, or sys.argv[0]."""
    found = shutil.which("gsd")
    if found:
        return str(Path(found).resolve())
    return str(Path(sys.argv[0]).resolve())


def show_entry(entry: ScheduleEntry) -> str:
    """Return a human-readable preview of an entry (printed before install)."""
    cmd = " ".join(_build_command(entry))
    return "\n".join(
        [
            f"Schedule ID : {entry.schedule_id}",
            f"Timezone    : {entry.timezone}",
            f"Cron expr   : {entry.cron_expr}",
            f"Todo file   : {entry.todo_path}",
            f"Command     : {cmd}",
            "",
            "--- crontab entry (or equivalent) ---",
            _format_cron_block(entry),
            "--------------------------------------",
        ]
    )


def add_schedule(
    cron_expr: str,
    *,
    todo_path: str | Path,
    config_path: str | Path | None = None,
    timezone: str = "UTC",
    backend: Backend | None = None,
    gsd_path: str | None = None,
    dry_run: bool = False,
    schedule_id: str | None = None,
) -> ScheduleEntry:
    """Validate and (unless dry_run) install a schedule.

    Always prints the entry before any write (AC10.9).
    Raises CronExprError on an invalid cron expression (AC10.4).
    Returns the ScheduleEntry (including the generated schedule_id).
    """
    validate_cron(cron_expr)

    sid = schedule_id or uuid.uuid4().hex[:8]
    entry = ScheduleEntry(
        schedule_id=sid,
        cron_expr=cron_expr,
        todo_path=str(Path(todo_path).resolve()),
        gsd_path=gsd_path or _gsd_executable(),
        config_path=str(Path(config_path).resolve()) if config_path else None,
        timezone=timezone,
    )

    print(show_entry(entry))

    if not dry_run:
        b: Backend = backend or default_backend()
        b.add_entry(entry)

    return entry


def list_schedules(backend: Backend | None = None) -> list[ScheduleEntry]:
    """Return all gsd-owned schedule entries (AC10.7)."""
    b: Backend = backend or default_backend()
    return b.list_entries()


def remove_schedule(schedule_id: str, backend: Backend | None = None) -> bool:
    """Remove the schedule entry for schedule_id.

    Returns True if removed, False if not found (AC10.8).
    """
    b: Backend = backend or default_backend()
    return b.remove_entry(schedule_id)
