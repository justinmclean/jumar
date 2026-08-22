# SPDX-License-Identifier: Apache-2.0
"""Config loading: jumar.toml / [tool.jumar] in pyproject.toml, merged with CLI flags.

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
from pathlib import Path, PurePath
from typing import Any, cast

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Capability(StrEnum):
    """Coarse-grained capability declaration — what a subtask claims to need.

    A Capability value is a *declared intent*, not a runtime sandbox. The gate
    checks that ``subtask.capabilities ⊆ item.capabilities`` (AC4.3); that is the
    whole of the enforcement at this layer. Granting ``network`` does not prevent
    a subprocess from opening a socket; not granting it does not block one.
    Fine-grained allow/deny lists live in ``CommandPolicy``; the actual boundary
    is the container or VM the operator runs jumar inside.
    """

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


# `network` is granted by default: accuracy depends on reading authoritative
# primary sources, and an agent that cannot fetch them fabricates them instead
# — the worse failure. The boundary this policy expresses is therefore *send*,
# not *fetch*. Read the honesty note below before treating it as a control.
_DEFAULT_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.read_fs,
        Capability.write_fs,
        Capability.run_commands,
        Capability.network,
    }
)

# Allow list for dispatched argv. Since `is_allowed()` is now actually called
# by verify/command.py, this list is load-bearing: anything absent makes a
# legitimate check return `inconclusive`. It therefore has to cover the
# read-only primitives a verification check is normally written with, plus the
# project's own toolchain and the fetchers.
_DEFAULT_ALLOW: tuple[str, ...] = (
    # toolchain
    "python3",
    "python",
    "pytest",
    "ruff",
    "mypy",
    "make",
    "git",
    # read-only verification primitives
    "test",
    "grep",
    "egrep",
    "fgrep",
    "ls",
    "cat",
    "head",
    "tail",
    "wc",
    "awk",
    "sed",
    "diff",
    "find",
    "stat",
    "file",
    "sha256sum",
    "shasum",
    "cmp",
    "sort",
    "uniq",
    "tr",
    "cut",
    "jq",
    # fetchers — network is a default capability
    "curl",
    "wget",
)

# Deny wins over allow. These are the *send* vectors: the boundary is outbound
# transmission, not outbound reading.
_DEFAULT_DENY: tuple[str, ...] = (
    "mail",
    "mailx",
    "sendmail",
    "ssmtp",
    "msmtp",
    "ssh",
    "scp",
    "sftp",
    "rsync",
)

# HONESTY NOTE — read before relying on the lists above.
#
# `python3` is on the allow list and `network` is granted, so
# `python3 -c "import smtplib; ..."` sends mail and `urllib` posts anywhere.
# An argv[0] allow list cannot stop a determined agent; it is defence in depth,
# not a boundary. The actual control is the execution environment: run jumar
# inside a container or VM whose egress is restricted to the hosts the work
# needs. Any unattended or scheduled run without that container is unbounded,
# whatever these tuples say.
#
# What the list DOES buy, now that it is enforced: a check cannot quietly shell
# out to something nobody expected, and `models.Check` refuses `bash -c`
# wrappers outright so the list cannot be sidestepped with one argv element.

# argv prefixes that are unconditionally refused, even if present in the allow list.
_HARD_DENY: tuple[tuple[str, ...], ...] = (
    ("git", "push"),
    ("gh",),
)


# Stage names that may appear as sub-tables under [harness] in jumar.toml.
# An unrecognised sub-table key is a startup error — not a silent no-op —
# so that a typo in jumar.toml does not silently fail to apply the override.
_VALID_HARNESS_STAGES: frozenset[str] = frozenset({"decompose", "execute", "judge"})

# ---------------------------------------------------------------------------
# Sub-configs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HarnessConfig:
    """Agent and model selection for the decompose / execute / judge stages.

    The top-level ``agent`` and ``model`` are the defaults for every stage.
    Per-stage overrides (``decompose_model``, ``judge_model``, …) let high-
    leverage stages (decompose, judge) use a stronger model while the bulk
    of execution uses a cheaper one.  Call ``for_stage("decompose")`` to get
    the resolved pair for that stage.

    ``base_url`` and ``api_key_env`` only matter when ``agent`` names an
    in-process harness (``"openai"`` — see ``harness.IN_PROCESS_HARNESSES``):
    the OpenAI-compatible chat-completions endpoint to call, and the
    environment variable holding its API key (``None`` if the endpoint needs
    none, e.g. a local LM Studio server). Every subprocess harness ignores
    both.

    ``None`` for a per-stage field means "inherit the top-level value".
    """

    agent: str = "claude"
    model: str = "sonnet"
    base_url: str | None = None
    api_key_env: str | None = None
    # Per-stage overrides — None inherits the top-level value.
    decompose_agent: str | None = None
    decompose_model: str | None = None
    decompose_base_url: str | None = None
    decompose_api_key_env: str | None = None
    execute_agent: str | None = None
    execute_model: str | None = None
    execute_base_url: str | None = None
    execute_api_key_env: str | None = None
    judge_agent: str | None = None
    judge_model: str | None = None
    judge_base_url: str | None = None
    judge_api_key_env: str | None = None

    def for_stage(self, stage: str) -> HarnessConfig:
        """Return the resolved ``HarnessConfig`` for *stage*.

        Resolution order: stage override → top-level default.
        The returned config has all per-stage fields cleared (no nesting).
        Raises ``ValueError`` for unrecognised stage names.
        """
        if stage not in _VALID_HARNESS_STAGES:
            raise ValueError(
                f"Unknown harness stage {stage!r}; valid stages: {sorted(_VALID_HARNESS_STAGES)}"
            )
        a: str | None = getattr(self, f"{stage}_agent")
        m: str | None = getattr(self, f"{stage}_model")
        b: str | None = getattr(self, f"{stage}_base_url")
        k: str | None = getattr(self, f"{stage}_api_key_env")
        return HarnessConfig(
            agent=a if a is not None else self.agent,
            model=m if m is not None else self.model,
            base_url=b if b is not None else self.base_url,
            api_key_env=k if k is not None else self.api_key_env,
        )


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
    """Resolved configuration for one jumar run."""

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
    # Opt-in required to use a harness that cannot express tool-level denials
    # (codex, cursor, gemini, kiro, opencode). Default False because those
    # harnesses cannot enforce the git push / gh boundary at the tool-call layer.
    allow_unrestricted_harness: bool = False


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _load_raw(root: Path) -> dict[str, Any]:
    """Return the [jumar] section from jumar.toml, or [tool.jumar] from pyproject.toml."""
    jumar_toml = root / "jumar.toml"
    if jumar_toml.is_file():
        with jumar_toml.open("rb") as fh:
            data: dict[str, Any] = tomllib.load(fh)
        return cast(dict[str, Any], data.get("jumar", {}))

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        with pyproject.open("rb") as fh:
            data = tomllib.load(fh)
        tool: dict[str, Any] = cast(dict[str, Any], data.get("tool", {}))
        return cast(dict[str, Any], tool.get("jumar", {}))

    return {}


def load_config(
    root: Path | None = None,
    *,
    cli_overrides: dict[str, object] | None = None,
) -> Config:
    """Load config from file then apply CLI overrides.

    Priority (highest wins): CLI overrides > jumar.toml / [tool.jumar] > built-in defaults.
    jumar.toml is preferred over pyproject.toml when both are present.
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
    harness_agent = str(harness_raw.get("agent", "claude"))
    harness_model = str(harness_raw.get("model", "sonnet"))
    harness_base_url = (
        str(harness_raw["base_url"]) if harness_raw.get("base_url") is not None else None
    )
    harness_api_key_env = (
        str(harness_raw["api_key_env"]) if harness_raw.get("api_key_env") is not None else None
    )

    # Validate: every key under [harness] must be a scalar key or a stage name.
    _harness_scalar_keys: frozenset[str] = frozenset({"agent", "model", "base_url", "api_key_env"})
    unknown_harness_keys = {
        k for k in harness_raw if k not in _harness_scalar_keys | _VALID_HARNESS_STAGES
    }
    if unknown_harness_keys:
        raise ValueError(
            f"Unknown key(s) under [harness] in jumar.toml: {sorted(unknown_harness_keys)}. "
            f"Valid stage names: {sorted(_VALID_HARNESS_STAGES)}."
        )

    # Parse per-stage overrides.
    _stage_keys: frozenset[str] = frozenset({"agent", "model", "base_url", "api_key_env"})
    stage_kwargs: dict[str, str | None] = {}
    for _stage in sorted(_VALID_HARNESS_STAGES):
        stage_raw: Any = harness_raw.get(_stage)
        if stage_raw is None:
            stage_kwargs[f"{_stage}_agent"] = None
            stage_kwargs[f"{_stage}_model"] = None
            stage_kwargs[f"{_stage}_base_url"] = None
            stage_kwargs[f"{_stage}_api_key_env"] = None
            continue
        if not isinstance(stage_raw, dict):
            raise ValueError(
                f"[harness.{_stage}] must be a TOML table, got {type(stage_raw).__name__!r}"
            )
        unknown_stage_keys = {k for k in stage_raw if k not in _stage_keys}
        if unknown_stage_keys:
            raise ValueError(
                f"Unknown key(s) under [harness.{_stage}]: {sorted(unknown_stage_keys)}; "
                f"only {sorted(_stage_keys)} are valid inside a stage table."
            )
        stage_kwargs[f"{_stage}_agent"] = str(stage_raw["agent"]) if "agent" in stage_raw else None
        stage_kwargs[f"{_stage}_model"] = str(stage_raw["model"]) if "model" in stage_raw else None
        stage_kwargs[f"{_stage}_base_url"] = (
            str(stage_raw["base_url"]) if stage_raw.get("base_url") is not None else None
        )
        stage_kwargs[f"{_stage}_api_key_env"] = (
            str(stage_raw["api_key_env"]) if stage_raw.get("api_key_env") is not None else None
        )

    harness = HarnessConfig(
        agent=harness_agent,
        model=harness_model,
        base_url=harness_base_url,
        api_key_env=harness_api_key_env,
        **stage_kwargs,
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
        allow_unrestricted_harness=bool(_get("allow_unrestricted_harness", False)),
    )


# ---------------------------------------------------------------------------
# Dispatch policy
# ---------------------------------------------------------------------------


def is_allowed(argv: list[str], config: Config) -> bool:
    """Return True iff argv may be dispatched.

    argv[0] is matched on its basename, so "/usr/bin/grep" and "grep" resolve
    the same way.

    Rules applied in order (first match wins):
    1. Empty argv → denied.
    2. Matches a hard-deny prefix (git push, gh) → denied unconditionally.
    3. argv[0] in the deny list → denied.
    4. argv[0] not in the allow list → denied.
    5. Otherwise → allowed.
    """
    if not argv:
        return False

    # Compare on the BASENAME. `sys.executable` is an absolute path, and a
    # model-authored check may equally write "/usr/bin/grep"; matching the raw
    # string refused both. Note the trade-off this makes explicit: a binary
    # named `git` anywhere on disk is treated as `git`. That is consistent with
    # the honesty note above — the list is defence in depth, not a boundary.
    cmd = PurePath(argv[0]).name or argv[0]
    normalised = [cmd, *argv[1:]]

    # Hard deny: refuse unconditionally regardless of allow/deny lists.
    for pattern in _HARD_DENY:
        if tuple(normalised[: len(pattern)]) == pattern:
            return False

    # git push with interleaved flags: `git -C /path push` bypasses the
    # simple prefix check above. Scan the full argv for "push" as a token;
    # no legitimate git flag value is the string "push", so this is safe.
    if cmd == "git" and "push" in normalised[1:]:
        return False

    if cmd in config.commands.deny:
        return False

    return cmd in config.commands.allow
