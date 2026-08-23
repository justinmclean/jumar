# SPDX-License-Identifier: Apache-2.0
"""jumar doctor — runtime sanity checks with actionable messages.

Public API
----------
CheckStatus     — ok / warn / fail
DoctorCheck     — one named check with its status and message
DoctorReport    — collection of checks with an aggregate exit_status
run_doctor()    — execute all checks and return a DoctorReport
format_report() — render a DoctorReport as a human-readable string
"""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
import urllib.error
import urllib.request
import zoneinfo
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .config import DEFAULT_CONFIG_SOURCE, Config
from .harness import IN_PROCESS_HARNESSES


class CheckStatus(StrEnum):
    ok = "ok"
    warn = "warn"
    fail = "fail"


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: CheckStatus
    message: str


@dataclass(frozen=True)
class DoctorReport:
    checks: tuple[DoctorCheck, ...]

    @property
    def exit_status(self) -> int:
        """Return 1 if any check failed, 0 otherwise."""
        return 1 if any(c.status == CheckStatus.fail for c in self.checks) else 0


def run_doctor(config: Config) -> DoctorReport:
    """Run all doctor checks and return the aggregate report."""
    checks: list[DoctorCheck] = []
    checks.extend(_check_config(config))
    checks.append(_check_config_source(config))
    checks.append(_check_harness(config))
    checks.extend(_check_allowlist(config))
    checks.append(_check_todo(config))
    checks.append(_check_schedule(config))
    checks.append(_check_legacy_schedule_markers())
    return DoctorReport(checks=tuple(checks))


def format_report(report: DoctorReport) -> str:
    """Render a DoctorReport as a human-readable string."""
    _SYMBOL = {CheckStatus.ok: "ok  ", CheckStatus.warn: "warn", CheckStatus.fail: "FAIL"}
    lines = ["jumar doctor"]
    for check in report.checks:
        lines.append(f"  [{_SYMBOL[check.status]}] {check.name}: {check.message}")
    lines.append("")
    if report.exit_status == 0:
        lines.append("All checks passed (failures would be marked FAIL).")
    else:
        lines.append("One or more checks failed — see FAIL lines above.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_config(config: Config) -> list[DoctorCheck]:
    """Config field validity."""
    issues: list[DoctorCheck] = []

    if config.max_subtasks < 1:
        issues.append(
            DoctorCheck(
                "config.max_subtasks",
                CheckStatus.fail,
                f"max_subtasks is {config.max_subtasks}; must be >= 1. "
                "Set max_subtasks in jumar.toml.",
            )
        )

    if config.max_repairs < 0:
        issues.append(
            DoctorCheck(
                "config.max_repairs",
                CheckStatus.fail,
                f"max_repairs is {config.max_repairs}; must be >= 0. "
                "Set max_repairs in jumar.toml.",
            )
        )

    if config.subtask_timeout_s < 1:
        issues.append(
            DoctorCheck(
                "config.subtask_timeout_s",
                CheckStatus.fail,
                f"subtask_timeout_s is {config.subtask_timeout_s}; must be >= 1. "
                "Set subtask_timeout_s in jumar.toml.",
            )
        )

    try:
        zoneinfo.ZoneInfo(config.timezone)
    except (zoneinfo.ZoneInfoNotFoundError, KeyError):
        issues.append(
            DoctorCheck(
                "config.timezone",
                CheckStatus.fail,
                f"Unknown timezone {config.timezone!r}. "
                "Set timezone to a valid IANA name (e.g. 'America/New_York') in jumar.toml.",
            )
        )

    if not issues:
        issues.append(
            DoctorCheck(
                "config",
                CheckStatus.ok,
                "Config is valid.",
            )
        )

    return issues


def _check_config_source(config: Config) -> DoctorCheck:
    """Name where the loaded config came from — a file, or built-in defaults.

    `_check_config` above only validates field values, so it reports "valid"
    even when no jumar.toml or pyproject.toml [tool.jumar] table exists and
    every field is silently a built-in default. Observed in use: a doctor run
    in a directory with no config reported all-ok while running the default
    32-entry allow list and the default `claude` harness against the default
    `todo.md` — "valid" without "and here is the file it came from" is the
    wrong report for a tool whose scheduled runs depend on cwd-resolved config.
    """
    if config.config_source == DEFAULT_CONFIG_SOURCE:
        return DoctorCheck(
            "config.source",
            CheckStatus.warn,
            f"{DEFAULT_CONFIG_SOURCE} — running with todo_path='{config.todo_path}', "
            f"harness.agent='{config.harness.agent}', harness.model='{config.harness.model}', "
            f"{len(config.commands.allow)} allow-list entry(ies). "
            "Create jumar.toml (or a [tool.jumar] table in pyproject.toml) to pin these "
            "explicitly.",
        )
    return DoctorCheck(
        "config.source",
        CheckStatus.ok,
        f"Config loaded from {config.config_source}.",
    )


def _check_harness(config: Config) -> DoctorCheck:
    """Harness binary on PATH, or — for the in-process ``openai`` harness — the
    configured OpenAI-compatible endpoint reachable and serving the model."""
    agent_bin = config.harness.agent
    if agent_bin in IN_PROCESS_HARNESSES:
        return _check_openai_harness(config)

    if shutil.which(agent_bin) is not None:
        return DoctorCheck(
            "harness",
            CheckStatus.ok,
            f"Harness binary '{agent_bin}' found on PATH.",
        )
    return DoctorCheck(
        "harness",
        CheckStatus.fail,
        f"Harness binary '{agent_bin}' not found on PATH. "
        'Install it or set [harness] agent = "<name>" in jumar.toml.',
    )


def _check_openai_harness(config: Config) -> DoctorCheck:
    """GET ``{base_url}/models`` and report whether the endpoint is reachable
    and, when it is, whether the configured model id is among those served.

    ``shutil.which()`` is meaningless here — there is no binary, only an HTTP
    endpoint, so this is the in-process harness's equivalent check.
    """
    base_url = config.harness.base_url
    if not base_url:
        return DoctorCheck(
            "harness",
            CheckStatus.fail,
            '[harness] agent = "openai" but no base_url is configured. '
            'Set [harness] base_url = "http://<host>:<port>/v1" in jumar.toml.',
        )

    models_url = base_url.rstrip("/") + "/models"
    try:
        request = urllib.request.Request(models_url, method="GET")
        with urllib.request.urlopen(request, timeout=5) as resp:  # noqa: S310
            raw = resp.read()
        parsed = json.loads(raw)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return DoctorCheck(
            "harness",
            CheckStatus.fail,
            f"Could not reach OpenAI-compatible endpoint at '{models_url}': {exc}. "
            "Is the local model server running?",
        )

    served = parsed.get("data") if isinstance(parsed, dict) else None
    served_ids = (
        {str(entry["id"]) for entry in served if isinstance(entry, dict) and "id" in entry}
        if isinstance(served, list)
        else set()
    )

    model = config.harness.model
    if model and model not in served_ids:
        listed = ", ".join(sorted(served_ids)) if served_ids else "(none)"
        return DoctorCheck(
            "harness",
            CheckStatus.warn,
            f"Endpoint '{base_url}' is reachable but model '{model}' is not in its "
            f"served list: {listed}. Check the exact id with `curl {models_url}`.",
        )
    return DoctorCheck(
        "harness",
        CheckStatus.ok,
        f"OpenAI-compatible endpoint '{base_url}' reachable; model '{model}' is served.",
    )


def _check_allowlist(config: Config) -> list[DoctorCheck]:
    """Allow-list sanity: overlaps with deny list, empty allow list."""
    issues: list[DoctorCheck] = []
    allow_set = set(config.commands.allow)
    deny_set = set(config.commands.deny)

    overlap = allow_set & deny_set
    if overlap:
        names = ", ".join(sorted(overlap))
        issues.append(
            DoctorCheck(
                "allowlist.overlap",
                CheckStatus.warn,
                f"Commands in both allow and deny (deny wins, never dispatched): {names}. "
                "Remove them from one list in jumar.toml [commands].",
            )
        )

    if not allow_set:
        issues.append(
            DoctorCheck(
                "allowlist.empty",
                CheckStatus.fail,
                "Allow list is empty — no commands can be dispatched by subtasks. "
                "Add entries under [commands] allow in jumar.toml.",
            )
        )

    if not issues:
        issues.append(
            DoctorCheck(
                "allowlist",
                CheckStatus.ok,
                f"Allow list has {len(allow_set)} entry(ies) with no deny conflicts.",
            )
        )

    return issues


def _check_todo(config: Config) -> DoctorCheck:
    """Todo file exists and parses without errors."""
    from .ingest import IngestError, ingest

    todo_path = Path(config.todo_path)
    if not todo_path.exists():
        return DoctorCheck(
            "todo",
            CheckStatus.fail,
            f"Todo file not found at '{todo_path}'. Create it or set todo_path in jumar.toml.",
        )

    try:
        result = ingest(todo_path, config)
    except IngestError as exc:
        return DoctorCheck(
            "todo",
            CheckStatus.fail,
            f"Todo file failed to parse: {exc}",
        )

    if result.warnings:
        msgs = "; ".join(w.message for w in result.warnings[:3])
        suffix = f" (and {len(result.warnings) - 3} more)" if len(result.warnings) > 3 else ""
        return DoctorCheck(
            "todo",
            CheckStatus.warn,
            f"Todo file parsed with {len(result.warnings)} warning(s): {msgs}{suffix}",
        )

    return DoctorCheck(
        "todo",
        CheckStatus.ok,
        f"Todo file parsed: {len(result.items)} item(s).",
    )


def _check_schedule(config: Config) -> DoctorCheck:
    """Installed schedule entries readable (graceful when schedule module absent)."""
    try:
        from . import schedule as _sched

        entries = _sched.list_schedules()
    except ImportError:
        return DoctorCheck(
            "schedule",
            CheckStatus.warn,
            "Schedule module not available. Run 'jumar schedule add <cron>' to install one.",
        )
    except Exception as exc:  # noqa: BLE001
        return DoctorCheck(
            "schedule",
            CheckStatus.fail,
            f"Could not read installed schedule entries: {exc}. "
            "Check your crontab, launchd agents, or systemd --user timers.",
        )

    if not entries:
        return DoctorCheck(
            "schedule",
            CheckStatus.warn,
            "No jumar schedule entries installed. Run 'jumar schedule add <cron>' to install one.",
        )

    return DoctorCheck(
        "schedule",
        CheckStatus.ok,
        f"{len(entries)} schedule entry(ies) installed.",
    )


# ---------------------------------------------------------------------------
# Legacy (pre-rename) schedule markers
# ---------------------------------------------------------------------------

# Markers written by the tool before the rename to jumar. Entries carrying
# them are invisible to the new binary: it must never rewrite entries it did
# not author (AGENTS.md), so it can only detect and name them.
_LEGACY_CRON_MARKER = "# gsd-meta: "  # legacy-name-ok
_LEGACY_LAUNCHD_GLOB = "com.gsd.*.plist"  # legacy-name-ok


def _legacy_marker_findings(crontab_text: str, plist_names: list[str]) -> list[str]:
    """Return human-readable findings for pre-rename schedule entries."""
    findings: list[str] = []
    n_cron = sum(1 for line in crontab_text.splitlines() if line.startswith(_LEGACY_CRON_MARKER))
    if n_cron:
        findings.append(
            f"{n_cron} crontab entry(ies) with the pre-rename marker {_LEGACY_CRON_MARKER!r}"
        )
    if plist_names:
        findings.append(
            f"{len(plist_names)} launchd agent(s) with the pre-rename label: "
            + ", ".join(sorted(plist_names))
        )
    return findings


def _check_legacy_schedule_markers() -> DoctorCheck:
    """Warn about schedule entries installed under the pre-rename name.

    The new binary does not manage them (and must not rewrite entries it did
    not author). They keep firing the old command until removed by hand.
    """
    crontab_text = ""
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, shell=False, timeout=10
        )
        if result.returncode == 0:
            crontab_text = result.stdout

    plist_names: list[str] = []
    agents_dir = Path.home() / "Library" / "LaunchAgents"
    with contextlib.suppress(OSError):
        plist_names = [p.name for p in agents_dir.glob(_LEGACY_LAUNCHD_GLOB)]

    findings = _legacy_marker_findings(crontab_text, plist_names)
    if findings:
        return DoctorCheck(
            "legacy-schedule",
            CheckStatus.warn,
            "Pre-rename schedule entries found — the new binary does not manage them: "
            + "; ".join(findings)
            + ". Remove them by hand (crontab -e / rm the plist) and reinstall with "
            "'jumar schedule add'.",
        )
    return DoctorCheck("legacy-schedule", CheckStatus.ok, "No pre-rename schedule entries found.")
