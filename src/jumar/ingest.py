# SPDX-License-Identifier: Apache-2.0
"""Stage 1 — Ingest: parse GFM task lists from a Markdown todo file.

Public API
----------
IngestError   – raised for unrecoverable failures (missing / unreadable file).
ParseWarning  – (line_no, message) for non-fatal issues; collected, not raised.
IngestResult  – (items, warnings) returned by ingest().
ingest()      – parse a todo file and return IngestResult.
"""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import re
import zoneinfo
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from jumar.config import Config
from jumar.models import (
    Capability,
    ItemStatus,
    Recurrence,
    RecurUnit,
    Schedule,
    TodoItem,
)

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# GFM task list item: optional indent, dash, checkbox, optional space, text
_TASK_RE = re.compile(r"^(\s*)-\s+\[([ xX])\]\s*(.*?)\s*$")

# A line that *attempts* GFM task-list syntax (a list marker followed by a
# checkbox-shaped bracket) but fails _TASK_RE — e.g. a non-"-" marker, a
# multi-char or empty checkbox. Distinguishes a botched task line from ordinary
# prose/heading context (AC1.6). The bracket must be checkbox-sized and must not
# be followed by "(": an ordinary markdown link bullet — `- [the spec](url)` —
# also fails _TASK_RE, and todo files routinely carry reference links, so
# flagging those as malformed would be noise on legitimate content.
_TASK_ATTEMPT_RE = re.compile(r"^\s*[-*+]\s+\[[^\]]{0,3}\](?!\()")

# @key=value metadata token (key may contain hyphens)
_META_RE = re.compile(r"@([\w-]+)=(\S+)")

# @every= interval+unit: "2w", "3d", "1m"
_INTERVAL_UNIT_RE = re.compile(r"^(\d+)([dwm])$")

# Named weekday abbreviations accepted by @every=
_DOW_ABBREVS: frozenset[str] = frozenset({"mon", "tue", "wed", "thu", "fri", "sat", "sun"})

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class IngestError(Exception):
    """Raised for unrecoverable ingest failures (missing file, unreadable file)."""


class ParseWarning(NamedTuple):
    line_no: int
    message: str


@dataclass
class IngestResult:
    items: list[TodoItem]
    warnings: list[ParseWarning]


# ---------------------------------------------------------------------------
# Internal state for incremental item building
# ---------------------------------------------------------------------------


@dataclass
class _PartialItem:
    line_no: int
    raw_line: str
    indent: int
    checked: bool
    raw_text: str
    context: list[str]
    subtasks: list[str]
    subtask_mode: bool


# ---------------------------------------------------------------------------
# Errors raised internally during schedule parsing
# ---------------------------------------------------------------------------


class _BadSchedule(ValueError):
    def __init__(self, token: str, value: str, reason: str) -> None:
        self.token = token
        self.value = value
        self.reason = reason
        super().__init__(f"@{token}={value!r}: {reason}")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def ingest(path: Path, config: Config) -> IngestResult:
    """Parse *path* as a GFM task list and return items + warnings.

    Raises IngestError for missing or unreadable files.  Non-fatal issues
    (bad metadata, bad schedule tokens, empty task items) are collected in
    IngestResult.warnings and never raise.
    """
    if not path.exists():
        raise IngestError(f"Todo file not found: {path}")

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise IngestError(f"Cannot read todo file {path}: {exc}") from exc

    try:
        tz = zoneinfo.ZoneInfo(config.timezone)
    except zoneinfo.ZoneInfoNotFoundError:
        raise IngestError(f"Unknown timezone in config: {config.timezone!r}") from None

    lines = text.splitlines(keepends=True)
    items: list[TodoItem] = []
    warnings: list[ParseWarning] = []
    _parse_lines(lines, tz, config, items, warnings)
    _validate_depends(items)
    # Fail before any model call on an @harness= naming a profile the config
    # does not define. Mid-run is too late: the item would already have spent
    # a decompose call, and a typo must never fall back to the default model.
    for item in items:
        wanted = item.meta.get("harness")
        if wanted and wanted not in config.harness_profiles:
            known = ", ".join(sorted(config.harness_profiles)) or "(none defined)"
            raise IngestError(
                f"Line {item.line_no}: @harness={wanted!r} on {item.item_id!r} names no "
                f"profile in {config.config_source}. Defined: {known}."
            )

    return IngestResult(items=items, warnings=warnings)


# ---------------------------------------------------------------------------
# Line-by-line parser
# ---------------------------------------------------------------------------


def _parse_lines(
    lines: list[str],
    tz: zoneinfo.ZoneInfo,
    config: Config,
    items: list[TodoItem],
    warnings: list[ParseWarning],
) -> None:
    context: list[str] = []  # non-task lines accumulating for the next item
    current: _PartialItem | None = None

    for lineno, line in enumerate(lines, 1):
        m = _TASK_RE.match(line)

        if m is None:
            # Non-task line: accumulates as context for the next top-level item.
            # If we were inside an item's block, this line also ends subtask mode.
            if current is not None:
                current.subtask_mode = False
            if _TASK_ATTEMPT_RE.match(line):
                # Looks like a botched task line (wrong marker, bad checkbox), not
                # ordinary prose/heading context — record it as a parse warning
                # (AC1.6) rather than silently absorbing it.
                content = line.rstrip("\n")
                warnings.append(
                    ParseWarning(
                        lineno,
                        f"Line {lineno}: malformed task line, ignored: {content!r}",
                    )
                )
            context.append(line.rstrip("\n"))
            continue

        indent = len(m.group(1))
        checked = m.group(2).lower() == "x"
        raw_text = m.group(3)

        if current is None or indent <= current.indent:
            # New top-level (or same-level) item.
            if current is not None:
                _finalize(current, tz, config, items, warnings)
            current = _PartialItem(
                line_no=lineno,
                raw_line=line,
                indent=indent,
                checked=checked,
                raw_text=raw_text,
                context=list(context),
                subtasks=[],
                subtask_mode=True,
            )
            context = []
        elif current.subtask_mode:
            # Indented task while in subtask mode: record as authored subtask.
            subtask_text, _ = _parse_meta(raw_text)
            current.subtasks.append(subtask_text or raw_text.strip())
        else:
            # Indented task after subtask mode ended (a non-task line intervened).
            # Treat as a new independent item.
            if current is not None:
                _finalize(current, tz, config, items, warnings)
            current = _PartialItem(
                line_no=lineno,
                raw_line=line,
                indent=indent,
                checked=checked,
                raw_text=raw_text,
                context=list(context),
                subtasks=[],
                subtask_mode=True,
            )
            context = []

    if current is not None:
        _finalize(current, tz, config, items, warnings)


# ---------------------------------------------------------------------------
# Dependency validation
# ---------------------------------------------------------------------------


def _closest_id(target: str, known_ids: set[str]) -> str | None:
    matches = difflib.get_close_matches(target, known_ids, n=1, cutoff=0.6)
    return matches[0] if matches else None


def _validate_depends(items: list[TodoItem]) -> None:
    """Raise IngestError if any @depends= target names an item not in this file."""
    by_id: set[str] = {item.item_id for item in items}
    for item in items:
        for dep_id in item.depends:
            if dep_id not in by_id:
                msg = (
                    f"Line {item.line_no}: {item.item_id!r} depends on "
                    f"{dep_id!r} which does not exist in this file"
                )
                suggestion = _closest_id(dep_id, by_id)
                if suggestion:
                    msg += f" (did you mean {suggestion!r}?)"
                raise IngestError(msg)


# ---------------------------------------------------------------------------
# Item finalisation
# ---------------------------------------------------------------------------


def _finalize(
    partial: _PartialItem,
    tz: zoneinfo.ZoneInfo,
    config: Config,
    items: list[TodoItem],
    warnings: list[ParseWarning],
) -> None:
    clean_text, meta = _parse_meta(partial.raw_text)

    if not clean_text and not meta:
        warnings.append(
            ParseWarning(partial.line_no, f"Line {partial.line_no}: empty task item, skipped")
        )
        return

    status = ItemStatus.done if partial.checked else ItemStatus.pending
    item_id = _make_item_id(clean_text, meta)

    # @priority=
    priority: int | None = None
    if "priority" in meta:
        try:
            priority = int(meta["priority"])
        except ValueError:
            bad_val = meta["priority"]
            warnings.append(
                ParseWarning(
                    partial.line_no,
                    f"Line {partial.line_no}: @priority={bad_val!r} not an integer, ignored",
                )
            )

    # @depends= (comma-separated)
    depends: tuple[str, ...]
    if "depends" in meta:
        depends = tuple(d.strip() for d in meta["depends"].split(",") if d.strip())
    else:
        depends = ()

    # @capability= merged with config defaults
    capabilities = _resolve_capabilities(meta, config.capabilities)

    # Schedule tokens
    schedule: Schedule | None = None
    try:
        schedule = _build_schedule(meta, tz)
    except _BadSchedule as exc:
        warnings.append(
            ParseWarning(partial.line_no, f"Line {partial.line_no}: bad schedule token: {exc}")
        )
        status = ItemStatus.blocked
        meta = dict(meta)
        meta["_bad_schedule"] = str(exc)

    items.append(
        TodoItem(
            item_id=item_id,
            text=clean_text,
            raw_line=partial.raw_line,
            line_no=partial.line_no,
            status=status,
            context=tuple(partial.context),
            authored_subtasks=tuple(partial.subtasks),
            meta=meta,
            priority=priority,
            depends=depends,
            capabilities=capabilities,
            schedule=schedule,
        )
    )


# ---------------------------------------------------------------------------
# Metadata parsing
# ---------------------------------------------------------------------------


def _parse_meta(raw_text: str) -> tuple[str, dict[str, str]]:
    """Strip @key=value tokens from *raw_text*; return (clean_text, meta)."""
    meta: dict[str, str] = {}

    def _capture(m: re.Match[str]) -> str:
        meta[m.group(1)] = m.group(2)
        return ""

    clean = _META_RE.sub(_capture, raw_text)
    clean = re.sub(r" {2,}", " ", clean).strip()
    return clean, meta


# ---------------------------------------------------------------------------
# item_id
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40]


def _make_item_id(text: str, meta: dict[str, str]) -> str:
    if "id" in meta:
        return meta["id"]
    slug = _slugify(text)
    hash8 = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{hash8}" if slug else hash8


# ---------------------------------------------------------------------------
# Capability resolution
# ---------------------------------------------------------------------------


def _resolve_capabilities(
    meta: dict[str, str],
    config_caps: frozenset[object],
) -> frozenset[Capability]:
    # config.Capability and models.Capability are both StrEnum with the same values;
    # convert via .value so we always produce models.Capability instances.
    caps: set[Capability] = {Capability(str(c)) for c in config_caps}
    if "capability" in meta:
        for name in meta["capability"].split(","):
            name = name.strip()
            with contextlib.suppress(ValueError):
                caps.add(Capability(name))
    return frozenset(caps)


# ---------------------------------------------------------------------------
# Schedule parsing
# ---------------------------------------------------------------------------


def _build_schedule(meta: dict[str, str], tz: zoneinfo.ZoneInfo) -> Schedule | None:
    """Build a Schedule from parsed meta tokens.

    Returns None when no schedule tokens are present.
    Raises _BadSchedule when a token value cannot be parsed.
    """
    has_not_before = "not-before" in meta
    has_due = "due" in meta
    has_every = "every" in meta

    if not (has_not_before or has_due or has_every):
        return None

    not_before: str | None = None
    not_before_literal: str | None = None
    due: str | None = None
    due_literal: str | None = None
    recur: Recurrence | None = None

    if has_not_before:
        val = meta["not-before"]
        try:
            not_before = _parse_datetime(val, tz)
            not_before_literal = val
        except ValueError as exc:
            raise _BadSchedule("not-before", val, str(exc)) from exc

    if has_due:
        val = meta["due"]
        try:
            due = _parse_datetime(val, tz)
            due_literal = val
        except ValueError as exc:
            raise _BadSchedule("due", val, str(exc)) from exc

    if has_every:
        val = meta["every"]
        try:
            recur = _parse_recurrence(val)
        except ValueError as exc:
            raise _BadSchedule("every", val, str(exc)) from exc

    return Schedule(
        not_before=not_before,
        not_before_literal=not_before_literal,
        due=due,
        due_literal=due_literal,
        recur=recur,
        tz=str(tz),
    )


def _parse_datetime(s: str, tz: zoneinfo.ZoneInfo) -> str:
    """Parse a date/datetime string to an ISO-8601 string with UTC offset (+00:00).

    Accepts:
      YYYY-MM-DD            → 00:00:00 in the configured local zone
      YYYY-MM-DDTHH:MM      → local zone
      YYYY-MM-DDTHH:MM:SS   → local zone
      YYYY-MM-DDTHH:MMZ     → UTC
      YYYY-MM-DDTHH:MM:SSZ  → UTC
      Any ISO-8601 with explicit offset (e.g. +10:00)
    """
    s = s.strip()

    # Explicit UTC (Z suffix)
    if s.endswith("Z"):
        base = s[:-1]
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
            try:
                dt = datetime.strptime(base, fmt).replace(tzinfo=UTC)
                return dt.isoformat()
            except ValueError:
                continue
        raise ValueError(f"Cannot parse UTC datetime: {s!r}")

    # Try fromisoformat — handles anything with an explicit offset (+HH:MM etc.)
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            return dt.astimezone(UTC).isoformat()
    except ValueError:
        pass

    # Bare date or naive datetime — attach the configured local zone
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            dt_naive = datetime.strptime(s, fmt)
            dt_local = dt_naive.replace(tzinfo=tz)
            return dt_local.astimezone(UTC).isoformat()
        except ValueError:
            continue

    raise ValueError(f"Cannot parse date/datetime: {s!r}")


def _parse_recurrence(val: str) -> Recurrence:
    """Parse an @every= value into a Recurrence. Raises ValueError on failure."""
    v = val.strip().lower()

    if v == "weekday":
        return Recurrence(unit=RecurUnit.weekday, interval=1, days=())

    # Single day abbreviation or comma-separated list: mon,thu
    parts = [p.strip() for p in v.split(",") if p.strip()]
    if parts and all(p in _DOW_ABBREVS for p in parts):
        return Recurrence(unit=RecurUnit.dow, interval=1, days=tuple(parts))

    # Interval + unit suffix: 2w, 3d, 1m
    m = _INTERVAL_UNIT_RE.match(v)
    if m:
        interval = int(m.group(1))
        unit_map: dict[str, RecurUnit] = {
            "d": RecurUnit.day,
            "w": RecurUnit.week,
            "m": RecurUnit.month,
        }
        return Recurrence(unit=unit_map[m.group(2)], interval=interval, days=())

    raise ValueError(f"Cannot parse recurrence rule: {val!r}")
