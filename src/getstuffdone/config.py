# SPDX-License-Identifier: Apache-2.0
"""Config loading: gsd.toml / [tool.gsd] in pyproject.toml, merged with CLI flags.

Public API
----------
Capability      – coarse-grained authority enum.
HarnessConfig   – nested agent/model settings.
CommandPolicy   – argv[0] allow/deny policy; deny wins.
Config          – frozen resolved configuration.
load_config()   – locate, parse, and merge config from file + CLI overrides.
is_allowed()    – True iff an argv list may be dispatched.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Capability(StrEnum):
    """Coarse-grained authority over what a subtask may do."""

    read_fs = "read_fs"
    write_fs = "write_fs"
    run_commands = "run_commands"
    network = "network"
    git_commit = "git_commit"


# ---------------------------------------------------------------------------
# Private defaults
# ---------------------------------------------------------------------------


def _system_timezone() -> str:
    """Return the system's IANA timezone name, falling back to UTC."""
    tz_env = os.environ.get("TZ")
    if tz_env:
        return tz_env
    localtime = Path("/etc/localtime")
    if localtime.is_symlink():
        resolved = str(localtime.resolve())
        if "/zoneinfo/" in resolved:
            return resolved.split("/zoneinfo/")[-1]
    return "UTC"


_DEFAULT_CAPABILITIES: frozenset[Capability] = frozenset(
    {Capability.read_fs, Capability.write_fs, Capability.run_commands}
)

_DEFAULT_ALLOW: tuple[str, ...] = ("python3", "pytest", "ruff", "git", "make")
_DEFAULT_DENY: tuple[str, ...] = ("curl", "wget", "ssh", "scp")

# argv prefixes that are unconditionally refused, even if present in the allow list.
_HARD_DENY: tuple[tuple[str, ...], ...] = (
    ("git", "push"),
    ("gh",),
)


# ---------------------------------------------------------------------------
# Sub-configs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HarnessConfig:
    """Agent and model selection for the decompose / judge harness."""

    agent: str = "claude"
    model: str = "sonnet"


@dataclass(frozen=True)
class CommandPolicy:
    """argv[0] allow/deny policy. deny wins over allow."""

    allow: tuple[str, ...] = _DEFAULT_ALLOW
    deny: tuple[str, ...] = _DEFAULT_DENY


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    """Resolved configuration for one gsd run."""

    todo_path: str = "todo.md"
    timezone: str = field(default_factory=_system_timezone)
    max_subtasks: int = 12
    max_repairs: int = 2
    subtask_timeout_s: int = 900
    halt_on_fail: bool = False
    commit_on_complete: bool = False
    max_consecutive_failures: int = 3
    capabilities: frozenset[Capability] = field(
        default_factory=lambda: _DEFAULT_CAPABILITIES,
    )
    evidence_head_bytes: int = 4000
    harness: HarnessConfig = field(default_factory=HarnessConfig)
    commands: CommandPolicy = field(default_factory=CommandPolicy)
    schedule_backend: str | None = None  # None = platform default (cron/launchd)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _load_raw(root: Path) -> dict[str, Any]:
    """Return the [gsd] section from gsd.toml, or [tool.gsd] from pyproject.toml."""
    gsd_toml = root / "gsd.toml"
    if gsd_toml.is_file():
        with gsd_toml.open("rb") as fh:
            data: dict[str, Any] = tomllib.load(fh)
        return cast(dict[str, Any], data.get("gsd", {}))

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        with pyproject.open("rb") as fh:
            data = tomllib.load(fh)
        tool: dict[str, Any] = cast(dict[str, Any], data.get("tool", {}))
        return cast(dict[str, Any], tool.get("gsd", {}))

    return {}


def load_config(
    root: Path | None = None,
    *,
    cli_overrides: dict[str, object] | None = None,
) -> Config:
    """Load config from file then apply CLI overrides.

    Priority (highest wins): CLI overrides > gsd.toml / [tool.gsd] > built-in defaults.
    gsd.toml is preferred over pyproject.toml when both are present.
    """
    if root is None:
        root = Path.cwd()

    raw: dict[str, Any] = _load_raw(root)
    overrides: dict[str, object] = cli_overrides or {}

    def _get(key: str, default: Any) -> Any:
        return overrides[key] if key in overrides else raw.get(key, default)

    # Capabilities list
    raw_caps: list[str] = _get("capabilities", [c.value for c in _DEFAULT_CAPABILITIES])
    capabilities = frozenset(Capability(c) for c in raw_caps)

    # Nested sections come from the file only (no CLI flag counterparts).
    harness_raw: dict[str, Any] = raw.get("harness", {})
    harness = HarnessConfig(
        agent=str(harness_raw.get("agent", "claude")),
        model=str(harness_raw.get("model", "sonnet")),
    )

    commands_raw: dict[str, Any] = raw.get("commands", {})
    commands = CommandPolicy(
        allow=tuple(str(x) for x in commands_raw.get("allow", list(_DEFAULT_ALLOW))),
        deny=tuple(str(x) for x in commands_raw.get("deny", list(_DEFAULT_DENY))),
    )

    raw_sched_backend = _get("schedule_backend", None)
    schedule_backend = str(raw_sched_backend) if raw_sched_backend is not None else None

    return Config(
        todo_path=str(_get("todo_path", "todo.md")),
        timezone=str(_get("timezone", _system_timezone())),
        max_subtasks=int(_get("max_subtasks", 12)),
        max_repairs=int(_get("max_repairs", 2)),
        subtask_timeout_s=int(_get("subtask_timeout_s", 900)),
        halt_on_fail=bool(_get("halt_on_fail", False)),
        commit_on_complete=bool(_get("commit_on_complete", False)),
        max_consecutive_failures=int(_get("max_consecutive_failures", 3)),
        capabilities=capabilities,
        evidence_head_bytes=int(_get("evidence_head_bytes", 4000)),
        harness=harness,
        commands=commands,
        schedule_backend=schedule_backend,
    )


# ---------------------------------------------------------------------------
# Dispatch policy
# ---------------------------------------------------------------------------


def is_allowed(argv: list[str], config: Config) -> bool:
    """Return True iff argv may be dispatched.

    Rules applied in order (first match wins):
    1. Empty argv → denied.
    2. Matches a hard-deny prefix (git push, gh) → denied unconditionally.
    3. argv[0] in the deny list → denied.
    4. argv[0] not in the allow list → denied.
    5. Otherwise → allowed.
    """
    if not argv:
        return False

    # Hard deny: refuse unconditionally regardless of allow/deny lists.
    for pattern in _HARD_DENY:
        if tuple(argv[: len(pattern)]) == pattern:
            return False

    cmd = argv[0]

    if cmd in config.commands.deny:
        return False

    return cmd in config.commands.allow
