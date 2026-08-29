# SPDX-License-Identifier: Apache-2.0
"""Config loading: jumar.toml / [tool.jumar] in pyproject.toml, merged with CLI flags.

Public API
----------
Capability      – coarse-grained authority enum.
HarnessConfig   – nested agent/model settings.
CommandPolicy   – argv[0] allow/deny policy; deny wins.
Config          – frozen resolved configuration.
ConfigError     – raised when an explicit --config path is missing/unreadable.
load_config()   – locate, parse, and merge config from file + CLI overrides.
is_allowed()    – True iff an argv list may be dispatched.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePath
from typing import Any, cast

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ConfigError(ValueError):
    """Raised when config loading fails — e.g. an explicit ``--config`` path
    does not exist. Distinct from a missing file falling back to defaults,
    which is not an error."""


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
    "pwd",
    "date",
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

# Values accepted for `reasoning_effort`. Sent verbatim to the chat-completions
# endpoint, which passes it to the model's chat template; a model that does not
# understand the key ignores it. Validated rather than free-form so a typo is a
# startup error instead of a silently ignored setting that looks applied.
# "none" is included because several thinking models spell "off" that way.
_VALID_REASONING_EFFORTS: frozenset[str] = frozenset({"none", "low", "medium", "high", "xhigh"})

# Scalar keys valid at [harness], inside a stage table, and inside a profile.
_HARNESS_SCALAR_KEYS: frozenset[str] = frozenset(
    {"agent", "model", "base_url", "api_key_env", "reasoning_effort"}
)

# Sub-table under [harness] holding named alternative harnesses:
# [harness.profiles.<name>] with the same shape as [harness] itself. One is
# selected per run with --harness-profile; without it the base [harness]
# applies. This exists so a second model line-up (a slower, heavier pass) is
# one table in the same file rather than a duplicate jumar.toml that drifts
# out of step on todo_path, capabilities, and everything else.
_HARNESS_PROFILES_KEY = "profiles"

# `Config.config_source` value when neither jumar.toml nor a populated
# [tool.jumar] table in pyproject.toml was found — i.e. every field on the
# returned Config is a built-in default. `doctor.py` treats this as a warn:
# silently running on defaults (wrong todo file, wrong harness) looks
# identical to a deliberately-configured run unless something says so.
DEFAULT_CONFIG_SOURCE = "built-in defaults (no config file found)"

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

    ``reasoning_effort`` is forwarded to an in-process harness's chat-
    completions request when set. It is per-stage for the same reason the
    model is: decompose and judge are single bounded calls, while execute
    runs a tool loop where the cost of deliberation is paid on every turn.

    ``None`` for a per-stage field means "inherit the top-level value".

    A ``HarnessConfig`` is always fully resolved by the time it reaches this
    class: ``load_config`` has already layered the selected
    ``[harness.profiles.<name>]`` table (if any) over ``[harness]``, so
    nothing downstream needs to know which profile is active.
    """

    agent: str = "claude"
    model: str = "sonnet"
    base_url: str | None = None
    api_key_env: str | None = None
    reasoning_effort: str | None = None
    # Per-stage overrides — None inherits the top-level value.
    decompose_agent: str | None = None
    decompose_model: str | None = None
    decompose_base_url: str | None = None
    decompose_api_key_env: str | None = None
    decompose_reasoning_effort: str | None = None
    execute_agent: str | None = None
    execute_model: str | None = None
    execute_base_url: str | None = None
    execute_api_key_env: str | None = None
    execute_reasoning_effort: str | None = None
    judge_agent: str | None = None
    judge_model: str | None = None
    judge_base_url: str | None = None
    judge_api_key_env: str | None = None
    judge_reasoning_effort: str | None = None

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
        r: str | None = getattr(self, f"{stage}_reasoning_effort")
        return HarnessConfig(
            agent=a if a is not None else self.agent,
            model=m if m is not None else self.model,
            base_url=b if b is not None else self.base_url,
            api_key_env=k if k is not None else self.api_key_env,
            reasoning_effort=r if r is not None else self.reasoning_effort,
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
    schedule_backend: str | None = None  # None = platform default (cron/launchd/systemd)
    # Opt-in required to use a harness that cannot express tool-level denials
    # (codex, cursor, gemini, kiro, opencode). Default False because those
    # harnesses cannot enforce the git push / gh boundary at the tool-call layer.
    allow_unrestricted_harness: bool = False
    # Name of the [harness.profiles.<name>] table layered over [harness] for
    # this run (--harness-profile), or None when the base [harness] applies.
    # Recorded so `doctor` and the run journal can say which line-up ran; the
    # resolved models themselves live in `harness`.
    harness_profile: str | None = None
    # Every declared [harness.profiles.<name>], each already layered over
    # [harness] exactly as `harness` above would be. Kept so an individual
    # todo item can select one with @harness=<name> after config load: which
    # model an item wants is a property of the item, not of the invocation.
    harness_profiles: Mapping[str, HarnessConfig] = field(default_factory=dict)
    # Where this Config came from: an absolute jumar.toml path, a
    # "<pyproject.toml path> [tool.jumar]" description, or DEFAULT_CONFIG_SOURCE
    # when no file supplied any field. Set by load_config(); a Config built
    # directly (as most tests do) keeps the default, which reads as "no file".
    config_source: str = DEFAULT_CONFIG_SOURCE


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def resolve_harness(config: Config, item_profile: str | None) -> HarnessConfig:
    """Return the HarnessConfig for an item declaring ``@harness=item_profile``.

    An explicit ``--harness-profile`` wins: it is a deliberate instruction to
    run this pass on one line-up, which is what makes an A/B comparison
    across items possible. Otherwise the item's own token applies, and with
    neither the base [harness] does.

    Raises ConfigError for a name with no matching profile — a typo must not
    silently fall back to the default model.
    """
    if config.harness_profile is not None:
        return config.harness
    if not item_profile:
        return config.harness
    if item_profile not in config.harness_profiles:
        known = ", ".join(sorted(config.harness_profiles)) or "(none defined)"
        raise ConfigError(
            f"Unknown harness profile {item_profile!r} on a todo item. "
            f"Profiles defined in {config.config_source}: {known}."
        )
    return config.harness_profiles[item_profile]


def _load_raw(root: Path, config_path: Path | None = None) -> tuple[dict[str, Any], str]:
    """Return the [jumar]/[tool.jumar] table plus a description of its source.

    When ``config_path`` is given, it names an explicit file to read — the
    file ``--config PATH`` resolves to — instead of searching ``root``. The
    format ([jumar] vs [tool.jumar]) is inferred from the filename. This is
    the seam a scheduled run uses so config resolution does not depend on the
    scheduler's own invocation cwd (W8).

    The source string is DEFAULT_CONFIG_SOURCE when no file supplied any field —
    including a pyproject.toml present but with no [tool.jumar] table, which is
    indistinguishable from "no file" as far as the resolved Config is concerned.
    """
    if config_path is not None:
        if not config_path.is_file():
            raise ConfigError(f"config file not found: {config_path}")
        with config_path.open("rb") as fh:
            explicit_data: dict[str, Any] = tomllib.load(fh)
        if config_path.name == "pyproject.toml":
            explicit_tool: dict[str, Any] = cast(dict[str, Any], explicit_data.get("tool", {}))
            jumar_table = cast(dict[str, Any], explicit_tool.get("jumar", {}))
            source = (
                f"{config_path.resolve()} [tool.jumar]" if jumar_table else DEFAULT_CONFIG_SOURCE
            )
            return jumar_table, source
        raw = cast(dict[str, Any], explicit_data.get("jumar", {}))
        source = str(config_path.resolve()) if raw else DEFAULT_CONFIG_SOURCE
        return raw, source

    jumar_toml = root / "jumar.toml"
    if jumar_toml.is_file():
        with jumar_toml.open("rb") as fh:
            data: dict[str, Any] = tomllib.load(fh)
        return cast(dict[str, Any], data.get("jumar", {})), str(jumar_toml.resolve())

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        with pyproject.open("rb") as fh:
            data = tomllib.load(fh)
        tool: dict[str, Any] = cast(dict[str, Any], data.get("tool", {}))
        jumar_table = cast(dict[str, Any], tool.get("jumar", {}))
        if jumar_table:
            return jumar_table, f"{pyproject.resolve()} [tool.jumar]"

    return {}, DEFAULT_CONFIG_SOURCE


def load_config(
    root: Path | None = None,
    *,
    cli_overrides: dict[str, object] | None = None,
    config_path: Path | str | None = None,
    harness_profile: str | None = None,
) -> Config:
    """Load config from file then apply CLI overrides.

    Priority (highest wins): CLI overrides > jumar.toml / [tool.jumar] > built-in defaults.
    jumar.toml is preferred over pyproject.toml when both are present.

    ``config_path``, when given, names an explicit config file (e.g. from
    ``--config``) that is read directly instead of searching ``root``.
    Raises :class:`ConfigError` if it does not exist.

    ``harness_profile``, when given, names a ``[harness.profiles.<name>]``
    table to layer over ``[harness]`` (from ``--harness-profile``). The
    returned ``Config.harness`` is already resolved, so no caller downstream
    of this function needs to know a profile was involved. Raises
    :class:`ConfigError` if the named profile is not defined.
    """
    if root is None:
        root = Path.cwd()

    resolved_config_path = Path(config_path) if config_path is not None else None
    raw: dict[str, Any]
    config_source: str
    raw, config_source = _load_raw(root, resolved_config_path)
    overrides: dict[str, object] = cli_overrides or {}

    def _get(key: str, default: Any) -> Any:
        return overrides[key] if key in overrides else raw.get(key, default)

    # Capabilities list
    raw_caps: list[str] = _get("capabilities", [c.value for c in _DEFAULT_CAPABILITIES])
    capabilities = frozenset(Capability(c) for c in raw_caps)

    # Nested sections come from the file only (no CLI flag counterparts) —
    # except which harness profile to layer on top, which is a per-invocation
    # choice (--harness-profile) rather than a property of the file.
    harness_raw: dict[str, Any] = raw.get("harness", {})

    # Validate: every key under [harness] must be a scalar key, a stage name,
    # or the profiles table.
    unknown_harness_keys = {
        k
        for k in harness_raw
        if k not in _HARNESS_SCALAR_KEYS | _VALID_HARNESS_STAGES | {_HARNESS_PROFILES_KEY}
    }
    if unknown_harness_keys:
        raise ValueError(
            f"Unknown key(s) under [harness] in jumar.toml: {sorted(unknown_harness_keys)}. "
            f"Valid stage names: {sorted(_VALID_HARNESS_STAGES)}; named profiles go under "
            f"[harness.{_HARNESS_PROFILES_KEY}.<name>]."
        )

    def _stage_table(table: dict[str, Any], stage: str, label: str) -> dict[str, Any]:
        """Return the validated stage sub-table of *table*, or {} if absent."""
        stage_raw: Any = table.get(stage)
        if stage_raw is None:
            return {}
        if not isinstance(stage_raw, dict):
            raise ValueError(f"[{label}] must be a TOML table, got {type(stage_raw).__name__!r}")
        unknown_stage_keys = {k for k in stage_raw if k not in _HARNESS_SCALAR_KEYS}
        if unknown_stage_keys:
            raise ValueError(
                f"Unknown key(s) under [{label}]: {sorted(unknown_stage_keys)}; "
                f"only {sorted(_HARNESS_SCALAR_KEYS)} are valid inside a stage table."
            )
        return cast(dict[str, Any], stage_raw)

    def _scalar(table: dict[str, Any], key: str) -> str | None:
        """Return ``table[key]`` as a string, or None when absent/null."""
        return str(table[key]) if table.get(key) is not None else None

    def _first(*values: str | None) -> str | None:
        """First non-None value — the resolution chain, in priority order."""
        for value in values:
            if value is not None:
                return value
        return None

    # Validate EVERY profile, not just the selected one. A typo in a profile
    # you are not running today is still a config error; discovering it only
    # on the run that finally selects it is the silent-misconfiguration
    # failure the [harness] key check above exists to prevent.
    profiles_raw: Any = harness_raw.get(_HARNESS_PROFILES_KEY, {})
    if not isinstance(profiles_raw, dict):
        raise ValueError(
            f"[harness.{_HARNESS_PROFILES_KEY}] must be a table of named profiles, "
            f"got {type(profiles_raw).__name__!r}"
        )
    for _name in sorted(profiles_raw):
        _profile: Any = profiles_raw[_name]
        _label = f"harness.{_HARNESS_PROFILES_KEY}.{_name}"
        if not isinstance(_profile, dict):
            raise ValueError(f"[{_label}] must be a TOML table, got {type(_profile).__name__!r}")
        _unknown = {k for k in _profile if k not in _HARNESS_SCALAR_KEYS | _VALID_HARNESS_STAGES}
        if _unknown:
            raise ValueError(
                f"Unknown key(s) under [{_label}]: {sorted(_unknown)}. Valid keys: "
                f"{sorted(_HARNESS_SCALAR_KEYS)}; valid stage tables: "
                f"{sorted(_VALID_HARNESS_STAGES)}."
            )
        for _stage in sorted(_VALID_HARNESS_STAGES):
            _stage_table(_profile, _stage, f"{_label}.{_stage}")

    # Select the profile, if one was asked for. An unknown name is an error,
    # never a silent fall-back to the base harness: a scheduled run that
    # quietly used the wrong model line-up would be indistinguishable from
    # one that used the right one.
    profile_raw: dict[str, Any] = {}
    if harness_profile is not None:
        if harness_profile not in profiles_raw:
            known = ", ".join(sorted(profiles_raw)) if profiles_raw else "(none defined)"
            raise ConfigError(
                f"Unknown harness profile {harness_profile!r}. "
                f"Profiles defined in {config_source}: {known}."
            )
        profile_raw = cast(dict[str, Any], profiles_raw[harness_profile])

    def _validate_reasoning_efforts(resolved: HarnessConfig, label: str) -> None:
        """Reject an unrecognised reasoning_effort at load, not at request time.

        A bad value would otherwise be forwarded verbatim and silently ignored
        by the endpoint, leaving a run that looks configured and is not. Every
        profile is checked, not just the selected one, so a typo in an unused
        profile still fails the next load rather than the next scheduled run
        that selects it.
        """
        stage_attrs = (f"{s}_reasoning_effort" for s in sorted(_VALID_HARNESS_STAGES))
        for attr in ("reasoning_effort", *stage_attrs):
            value = getattr(resolved, attr)
            if value is not None and value not in _VALID_REASONING_EFFORTS:
                where = label if attr == "reasoning_effort" else f"{label}.{attr.split('_')[0]}"
                raise ConfigError(
                    f"Invalid reasoning_effort {value!r} under [{where}]. "
                    f"Valid values: {sorted(_VALID_REASONING_EFFORTS)}."
                )

    # Resolution order, highest first:
    #   [harness.profiles.<name>.<stage>]  →  [harness.profiles.<name>]
    #   →  [harness.<stage>]               →  [harness]
    # A profile's top-level therefore outranks the base file's per-stage
    # overrides: a profile that says model = "qwen" means every stage runs
    # qwen, not "qwen except where the base file named something else", which
    # would make the profile's effect depend on what it is layered over.
    def _build(profile_table: dict[str, Any], profile_name: str | None) -> HarnessConfig:
        """Layer *profile_table* over [harness] into one flat HarnessConfig."""
        label = f"harness.{_HARNESS_PROFILES_KEY}.{profile_name}"
        stage_kwargs: dict[str, str | None] = {}
        for stage in sorted(_VALID_HARNESS_STAGES):
            base_stage = _stage_table(harness_raw, stage, f"harness.{stage}")
            profile_stage = (
                _stage_table(profile_table, stage, f"{label}.{stage}") if profile_table else {}
            )
            for key in sorted(_HARNESS_SCALAR_KEYS):
                # No final fall-back to the base top-level here: a None leaves
                # HarnessConfig.for_stage() to inherit the resolved top-level
                # below, which already accounts for the profile.
                stage_kwargs[f"{stage}_{key}"] = _first(
                    _scalar(profile_stage, key),
                    _scalar(profile_table, key),
                    _scalar(base_stage, key),
                )
        agent = _first(_scalar(profile_table, "agent"), _scalar(harness_raw, "agent"))
        model = _first(_scalar(profile_table, "model"), _scalar(harness_raw, "model"))
        return HarnessConfig(
            agent=agent or "claude",
            model=model or "sonnet",
            base_url=_first(_scalar(profile_table, "base_url"), _scalar(harness_raw, "base_url")),
            api_key_env=_first(
                _scalar(profile_table, "api_key_env"), _scalar(harness_raw, "api_key_env")
            ),
            reasoning_effort=_first(
                _scalar(profile_table, "reasoning_effort"),
                _scalar(harness_raw, "reasoning_effort"),
            ),
            **stage_kwargs,
        )

    harness = _build(profile_raw, harness_profile)
    _validate_reasoning_efforts(harness, "harness")
    # Every profile is resolved, not just the selected one, so an item can
    # name one with @harness= after load without re-reading the file.
    harness_profiles: dict[str, HarnessConfig] = {
        name: _build(cast(dict[str, Any], profiles_raw[name]), name)
        for name in sorted(profiles_raw)
    }
    for _name, _resolved in harness_profiles.items():
        _validate_reasoning_efforts(_resolved, f"harness.{_HARNESS_PROFILES_KEY}.{_name}")

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
        harness_profile=harness_profile,
        harness_profiles=harness_profiles,
        config_source=config_source,
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
    if re.fullmatch(r"python3\.\d+", cmd) and "python3" in config.commands.deny:
        return False

    return cmd in config.commands.allow or (
        re.fullmatch(r"python3\.\d+", cmd) is not None and "python3" in config.commands.allow
    )
