# SPDX-License-Identifier: Apache-2.0
"""Canonical data shapes for Jumar.

All shapes are frozen dataclasses. Every field is a primitive, tuple, frozenset,
dict, or another shape defined here, so journal records are JSON-round-trippable.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ItemStatus(enum.StrEnum):
    pending = "pending"
    in_progress = "in_progress"
    done = "done"
    failed = "failed"
    blocked = "blocked"
    skipped_by_human = "skipped_by_human"
    inconclusive = "inconclusive"
    deferred = "deferred"


class SubtaskStatus(enum.StrEnum):
    pending = "pending"
    running = "running"
    passed = "passed"
    failed = "failed"
    inconclusive = "inconclusive"
    skipped = "skipped"


class CheckKind(enum.StrEnum):
    command = "command"
    file = "file"
    absence = "absence"
    judge = "judge"
    manual = "manual"


class Verdict(enum.StrEnum):
    passed = "passed"
    failed = "failed"
    inconclusive = "inconclusive"


class Capability(enum.StrEnum):
    read_fs = "read_fs"
    write_fs = "write_fs"
    run_commands = "run_commands"
    network = "network"
    git_commit = "git_commit"


class FailureCode(enum.StrEnum):
    unverifiable_plan = "unverifiable_plan"
    plan_too_long = "plan_too_long"
    invalid_plan = "invalid_plan"
    capability_denied = "capability_denied"
    timed_out = "timed_out"
    check_failed = "check_failed"
    repairs_exhausted = "repairs_exhausted"
    harness_error = "harness_error"
    dependency_blocked = "dependency_blocked"
    bad_schedule = "bad_schedule"
    already_running = "already_running"


class RecurUnit(enum.StrEnum):
    day = "day"
    week = "week"
    month = "month"
    weekday = "weekday"
    dow = "dow"


class ScheduleBackend(enum.StrEnum):
    cron = "cron"
    launchd = "launchd"
    systemd = "systemd"


# Strings that must never appear as Check.statement — the anti-"trust me" guard.
_PLACEHOLDER_STATEMENTS: frozenset[str] = frozenset({"", "n/a", "none", "TODO", "verify manually"})

# Interpreters that turn an argv into a shell string. A check of the form
# ["bash", "-c", "grep … && grep …"] is argv-shaped and a shell command in
# substance, which makes the project's "argv only, never shell=True" rule
# decorative. Observed in a real run: 14 of 15 model-authored checks were
# `bash -c`, defeating both the shell ban and the argv[0] allow list at once.
_SHELL_WRAPPERS: frozenset[str] = frozenset(
    {"bash", "sh", "zsh", "dash", "ksh", "fish", "csh", "tcsh"}
)

# Flags that hand a wrapper a program to interpret rather than a file to run.
_SHELL_INLINE_FLAGS: frozenset[str] = frozenset({"-c", "-lc", "-ic", "--command"})

# Programs whose exit status does not depend on the work being checked. A check
# built on one of these cannot fail for any reason connected to the subtask, so
# it proves nothing — it is "trust me" wearing an argv. `ls` and `test` are here
# only in their operand-free forms, handled below.
_VACUOUS_PROGRAMS: frozenset[str] = frozenset(
    {
        "true",
        ":",
        "echo",
        "printf",
        "pwd",
        "date",
        "sleep",
        "yes",
        "whoami",
        "hostname",
        "uname",
        "id",
        "env",
        "clear",
    }
)

# Programs that succeed unconditionally when given no operands.
_VACUOUS_WITHOUT_OPERANDS: frozenset[str] = frozenset({"ls", "test", "["})


# ---------------------------------------------------------------------------
# Schedule shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Recurrence:
    """Recurrence rule parsed from @every= token."""

    unit: RecurUnit
    interval: int
    days: tuple[str, ...]  # non-empty only when unit=dow

    def __post_init__(self) -> None:
        if self.interval < 1:
            raise ValueError("Recurrence.interval must be >= 1")
        if self.unit is RecurUnit.dow and not self.days:
            raise ValueError("unit=dow requires a non-empty days list")


@dataclass(frozen=True)
class Schedule:
    """Item-level eligibility parsed from @not-before / @due / @every tokens."""

    not_before: str | None
    not_before_literal: str | None
    due: str | None
    due_literal: str | None
    recur: Recurrence | None
    tz: str  # IANA zone used to resolve bare dates

    def __post_init__(self) -> None:
        import datetime as _datetime

        for fname, val in [("not_before", self.not_before), ("due", self.due)]:
            if val is None:
                continue
            try:
                parsed = _datetime.datetime.fromisoformat(val)
            except ValueError:
                raise ValueError(
                    f"Schedule.{fname} is not a valid ISO-8601 datetime: {val!r}"
                ) from None
            if parsed.tzinfo is None:
                raise ValueError(
                    f"Schedule.{fname} must be a timezone-aware UTC instant, got naive: {val!r}"
                )


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HarnessInfo:
    """Identifies the agent/model that produced a plan, attempt, or judge verdict.

    ``base_url``, ``api_key_env`` and the ``commands_*`` tuples are only
    meaningful for an in-process harness (currently ``"openai"``, see
    ``harness.IN_PROCESS_HARNESSES``); every subprocess harness ignores them.
    They stay off the four-field shape ``specs/03-data-model.md`` documents as
    "recorded on every plan, attempt, and judge verdict" — callers that
    journal a HarnessInfo already hand-pick ``agent``/``model`` rather than
    serialising the dataclass wholesale, so adding fields here does not change
    what lands in the journal.
    """

    agent: str
    model: str
    harness: str
    invoked_as: str
    base_url: str | None = None
    api_key_env: str | None = None
    reasoning_effort: str | None = None
    max_tokens: int | None = None
    commands_allow: tuple[str, ...] = ()
    commands_deny: tuple[str, ...] = ()


@dataclass(frozen=True)
class Check:
    """Executable proof that a subtask succeeded.

    Invariants enforced at construction time:
    - kind=command  =>  command is non-empty
    - kind=command  =>  argv[0] is not a shell wrapper invoked with -c
    - kind=command  =>  the check is capable of failing (no vacuous argv)
    - kind=file or kind=absence  =>  path is non-empty
    - kind=judge  =>  rationale is non-empty
    - statement is non-empty and is not one of the known placeholder strings

    The shell-wrapper rule is the load-bearing one. Without it a model writes
    ``["bash", "-c", "grep -q foo out.txt"]`` and every guarantee downstream —
    ``shell=False``, the argv[0] allow list, the deny list — is bypassed by a
    single argv element.
    """

    kind: CheckKind
    statement: str
    command: tuple[str, ...] | None = None
    expect_status: int = 0
    expect_stdout: str | None = None
    path: str | None = None
    pattern: str | None = None
    rationale: str | None = None
    timeout_s: int = 300

    def __post_init__(self) -> None:
        if not self.statement or self.statement in _PLACEHOLDER_STATEMENTS:
            raise ValueError(f"Check.statement is empty or a placeholder: {self.statement!r}")
        if self.kind is CheckKind.command and not self.command:
            raise ValueError("kind=command requires a non-empty command argv")
        if self.kind is CheckKind.command and self.command:
            argv0 = Path(self.command[0]).name
            if argv0 in _SHELL_WRAPPERS and any(a in _SHELL_INLINE_FLAGS for a in self.command[1:]):
                raise ValueError(
                    f"kind=command refuses a shell wrapper: {' '.join(self.command[:2])!r}. "
                    "A check must be a real argv, not a shell string — otherwise the "
                    "argv[0] allow list and the no-shell rule are both bypassed. "
                    "Express the check as the program itself, e.g. "
                    '["grep", "-q", "pattern", "file"].'
                )
            if argv0 in _VACUOUS_PROGRAMS:
                raise ValueError(
                    f"kind=command refuses a check that cannot fail: {argv0!r} "
                    "exits 0 regardless of whether the subtask succeeded. "
                    "A check must be able to return non-zero when the work was "
                    "not done."
                )
            operands = [a for a in self.command[1:] if not a.startswith("-")]
            if argv0 in _VACUOUS_WITHOUT_OPERANDS and not operands:
                raise ValueError(
                    f"kind=command refuses a check that cannot fail: {argv0!r} "
                    "with no operands always exits 0. Name the path or "
                    "condition being asserted."
                )
        if self.kind in (CheckKind.file, CheckKind.absence) and not self.path:
            raise ValueError(f"kind={self.kind.value} requires a non-empty path")
        if self.kind is CheckKind.judge and not self.rationale:
            raise ValueError("kind=judge requires a non-empty rationale")


@dataclass(frozen=True)
class TodoItem:
    """One task list entry produced by the ingest stage."""

    item_id: str
    text: str
    raw_line: str
    line_no: int
    status: ItemStatus
    context: tuple[str, ...]
    authored_subtasks: tuple[str, ...]
    meta: dict[str, str]
    priority: int | None
    depends: tuple[str, ...]
    capabilities: frozenset[Capability]
    schedule: Schedule | None = None


@dataclass(frozen=True)
class Attempt:
    """One execution of one subtask (initial or repair)."""

    attempt_no: int
    started_at: str
    finished_at: str
    harness: HarnessInfo
    agent_claim: str | None
    transcript_path: str
    exit_status: int | None
    files_touched: tuple[str, ...]
    error: str | None


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of running a Check against the world the subtask left behind."""

    subtask_id: str
    attempt_no: int
    verdict: Verdict
    kind: CheckKind
    evidence: dict[str, Any]
    summary: str
    evidence_path: str | None
    checked_at: str


@dataclass(frozen=True)
class Subtask:
    """One step in a Plan, with its executable proof."""

    subtask_id: str
    index: int
    description: str
    check: Check
    capabilities: frozenset[Capability]
    depends_on: tuple[int, ...]
    status: SubtaskStatus
    attempts: tuple[Attempt, ...]


@dataclass(frozen=True)
class Plan:
    """Ordered sequence of subtasks produced by the decompose stage."""

    item_id: str
    subtasks: tuple[Subtask, ...]
    source: str  # "authored" | "model"
    created_at: str
    harness: HarnessInfo
    # Session id for harness that supports resumption (e.g. claude --resume).
    # None means no session tracking for this plan.
    session_id: str | None = None


@dataclass(frozen=True)
class ItemResult:
    """Per-item outcome recorded in the run report."""

    item_id: str
    status: ItemStatus
    plan: Plan | None
    failure_code: FailureCode | None
    failed_subtask_index: int | None
    verifications: tuple[VerificationResult, ...]


@dataclass(frozen=True)
class Run:
    """Top-level container for one jumar run."""

    run_id: str
    todo_path: str
    mode: str  # "auto" | "dry-run" | "approve"
    config: dict[str, Any]
    started_at: str
    finished_at: str | None
    items: tuple[ItemResult, ...]
    warnings: tuple[str, ...]
