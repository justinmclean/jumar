# SPDX-License-Identifier: Apache-2.0
"""Stage 3 — Decompose: turn a TodoItem into a Plan of Subtasks.

Public API
----------
DecomposeError  – raised when decomposition fails irrecoverably.
decompose()     – call the agent harness, validate the response, return a Plan.

Hard rules enforced here (not by the prompt):
- A subtask with no ``check``, or with an empty/placeholder ``check``, is
  rejected and retried once, then the item fails as ``unverifiable_plan``.
- A ``judge`` check without ``rationale`` is also retried once.
- A plan longer than ``max_subtasks`` is rejected immediately (``plan_too_long``).
- A cyclic ``depends_on`` is rejected immediately (``invalid_plan``).
- If the item has ``authored_subtasks``, those descriptions are used verbatim
  and the agent only supplies the checks.
- The full plan is journalled before returning (AC3.6).
"""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path
from typing import Any

from .clock import make_session_id, stamp
from .config import Config
from .harness import AgentResult, detect_harness_error
from .harness import run_agent as _default_run_agent
from .journal import HARNESS_ERROR, PLAN_CREATED, PLAN_REJECTED, Journal
from .models import (
    Capability,
    Check,
    CheckKind,
    FailureCode,
    HarnessInfo,
    Plan,
    Subtask,
    SubtaskStatus,
    TodoItem,
)

# ---------------------------------------------------------------------------
# Rejection reason codes (internal)
# ---------------------------------------------------------------------------

# Retriable: retry once before failing as unverifiable_plan.
_RETRIABLE: frozenset[str] = frozenset({"missing_check", "parse_error"})

# Non-retriable: fail immediately with the mapped FailureCode.
_FAILURE_CODE: dict[str, FailureCode] = {
    "plan_too_long": FailureCode.plan_too_long,
    "invalid_plan": FailureCode.invalid_plan,
}

# Placeholder statements that are never accepted (the anti-"trust me" guard).
_PLACEHOLDER_STMTS: frozenset[str] = frozenset({"", "n/a", "none", "TODO", "verify manually"})


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------


class DecomposeError(Exception):
    """Raised when a TodoItem cannot be decomposed into a valid Plan."""

    def __init__(self, failure_code: FailureCode, message: str) -> None:
        super().__init__(message)
        self.failure_code = failure_code


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SCHEMA_EXAMPLE = json.dumps(
    {
        "subtasks": [
            {
                "description": "<imperative step>",
                "check": {
                    "kind": "command",
                    "statement": "<acceptance sentence>",
                    "command": ["argv", "..."],
                    "expect_status": 0,
                },
                "capabilities": ["read_fs"],
                "depends_on": [],
            }
        ]
    },
    indent=2,
)

_RULES = [
    "- Every subtask MUST have a `check` with an executable proof of success.",
    "- `check.statement` must be specific and testable"
    " — never empty, 'n/a', 'none', 'TODO', or 'verify manually'.",
    "- Use `kind=judge` ONLY when no executable check is possible;"
    " it MUST include a non-empty `rationale` explaining why.",
    "- `kind=command` requires `command` (a JSON argv list, never a shell string).",
    "- `kind=command` MUST NOT be a shell wrapper:"
    ' ["bash", "-c", "..."] (or sh/zsh/dash -c) is REJECTED.'
    " Express the check as the program itself,"
    ' e.g. ["grep", "-q", "Article 50", "sources/ai-act.html"].',
    "- The check MUST be able to FAIL. An argv that exits 0 regardless of the"
    " work — `true`, `echo`, `date`, bare `ls` — is REJECTED. Ask yourself:"
    " what would make this check return non-zero? If nothing would, it is not a check.",
    "- A `pattern` is a REGEX and must tolerate surface variation. Do not"
    " demand one exact spelling of something you will also be writing:"
    ' prefer "Art(icle)?\\s*2\\(12\\)" over "Art 2(12)". Anchor on the fact,'
    " not on your own phrasing of it — a brittle pattern fails a correct"
    " document and wastes the repair budget on rewording.",
    "- `kind=file` FAILS on a zero-byte file. A download that returned no body"
    " leaves the path present and empty, so assert content, not existence:"
    " give `pattern` with a phrase that can only appear in the real document.",
    "- Check the OUTCOME, not the artefact of your own effort. `grep -q draft"
    " notes.md` proves you typed a word; it does not prove the work is right.",
    "- `kind=file` or `kind=absence` requires `path`.",
    "- `depends_on` must list 0-based subtask indices and must form a DAG (no cycles).",
    "- `capabilities` must be a subset of the granted capabilities.",
    "- Do NOT use tools, read files, run commands, or browse. Answer from this"
    " prompt alone. You are writing a plan, not doing the work.",
    "- Return ONLY valid JSON — no prose, no markdown fences.",
]


def _model_prompt(item: TodoItem, config: Config) -> str:
    caps = ", ".join(sorted(c.value for c in item.capabilities)) or "none"
    ctx_block = ("\n\nContext:\n" + "\n".join(item.context)) if item.context else ""
    rules = "\n".join(_RULES)
    return (
        f"Decompose the following todo item into an ordered sequence of subtasks.\n\n"
        f"Item: {item.text}{ctx_block}\n\n"
        f"Granted capabilities: {caps}\n"
        f"Maximum subtasks: {config.max_subtasks}\n\n"
        f"Rules:\n{rules}\n\n"
        f"Schema:\n{_SCHEMA_EXAMPLE}"
    )


def _authored_prompt(item: TodoItem, config: Config) -> str:
    caps = ", ".join(sorted(c.value for c in item.capabilities)) or "none"
    numbered = "\n".join(f"{i}. {text}" for i, text in enumerate(item.authored_subtasks))
    rules = "\n".join(_RULES)
    return (
        "The following subtasks are pre-defined. "
        "Do not change their descriptions or order. "
        "Supply a `check` for each.\n\n"
        f"Subtasks:\n{numbered}\n\n"
        f"Granted capabilities: {caps}\n\n"
        f"Rules:\n{rules}\n\n"
        f"Schema (one entry per subtask above):\n{_SCHEMA_EXAMPLE}"
    )


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from agent output."""
    text = text.strip()
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass
    for m in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", text):
        try:
            result = json.loads(m.group(1).strip())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    if start >= 0:
        try:
            result = json.loads(text[start:])
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# Check + Subtask construction
# ---------------------------------------------------------------------------


def _build_check(raw: Any) -> Check | str:
    """Construct a Check from a raw dict; return an error string if invalid.

    Returns a Check on success, or a human-readable error string describing
    the specific rule violation — so the caller can include it verbatim in the
    retry prompt rather than reporting a generic "missing check".
    """
    if not isinstance(raw, dict):
        return "check is not an object"
    kind_str = raw.get("kind", "")
    stmt = str(raw.get("statement", "")).strip()
    if not kind_str:
        return "check.kind is missing"
    if not stmt or stmt in _PLACEHOLDER_STMTS:
        return f"check.statement is empty or a placeholder: {stmt!r}"
    try:
        kind = CheckKind(kind_str)
    except ValueError:
        return f"unrecognised check.kind: {kind_str!r}"

    command: tuple[str, ...] | None = None
    path: str | None = raw.get("path") or None
    pattern: str | None = raw.get("pattern") or None
    rationale: str | None = raw.get("rationale") or None
    expect_status: int = int(raw.get("expect_status", 0))
    expect_stdout: str | None = raw.get("expect_stdout") or None
    timeout_s: int = int(raw.get("timeout_s", 300))

    if kind is CheckKind.command:
        cmd_raw = raw.get("command")
        if not cmd_raw or not isinstance(cmd_raw, list):
            return "kind=command requires a non-empty command list"
        command = tuple(str(a) for a in cmd_raw)

    try:
        return Check(
            kind=kind,
            statement=stmt,
            command=command,
            expect_status=expect_status,
            expect_stdout=expect_stdout,
            path=path,
            pattern=pattern,
            rationale=rationale,
            timeout_s=timeout_s,
        )
    except ValueError as exc:
        return str(exc)


def _has_cycle(n: int, depends_on_map: dict[int, tuple[int, ...]]) -> bool:
    """Return True if the depends_on graph contains a cycle (DFS)."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[int, int] = {i: WHITE for i in range(n)}

    def dfs(node: int) -> bool:
        color[node] = GRAY
        for neighbor in depends_on_map.get(node, ()):
            if not (0 <= neighbor < n):
                continue
            if color[neighbor] == GRAY:
                return True
            if color[neighbor] == WHITE and dfs(neighbor):
                return True
        color[node] = BLACK
        return False

    return any(color[i] == WHITE and dfs(i) for i in range(n))


def _parse_and_validate(
    data: dict[str, Any],
    item_id: str,
    item_capabilities: frozenset[Capability],
    max_subtasks: int,
    authored_texts: tuple[str, ...],
) -> tuple[tuple[Subtask, ...], str | None, str | None]:
    """Build and validate Subtask objects from raw JSON.

    Returns ``(subtasks, rejection_code, rejection_detail)`` where:
    - On success: ``(subtasks, None, None)``
    - On failure: ``((), code, detail)`` — code is one of:
      - ``"parse_error"``   — malformed structure (retriable)
      - ``"missing_check"`` — a subtask lacks an acceptable check (retriable)
      - ``"plan_too_long"`` — exceeded max_subtasks (non-retriable)
      - ``"invalid_plan"``  — cycle in depends_on (non-retriable)
      detail is a human-readable explanation suitable for including in the
      retry prompt so the model knows which rule it broke.
    """
    raw_subtasks = data.get("subtasks")
    if not isinstance(raw_subtasks, list) or not raw_subtasks:
        return (), "missing_check", "response contains no subtasks list"

    if len(raw_subtasks) > max_subtasks:
        return (
            (),
            "plan_too_long",
            f"plan has {len(raw_subtasks)} subtasks; maximum is {max_subtasks}",
        )

    # For authored items the response must match length exactly.
    if authored_texts and len(raw_subtasks) != len(authored_texts):
        return (
            (),
            "parse_error",
            f"expected {len(authored_texts)} subtasks for authored list, got {len(raw_subtasks)}",
        )

    subtasks: list[Subtask] = []
    for i, raw in enumerate(raw_subtasks):
        if not isinstance(raw, dict):
            return (), "parse_error", f"subtask {i} is not an object"

        description: str
        if authored_texts:
            description = authored_texts[i]
        else:
            description = str(raw.get("description", "")).strip()
        if not description:
            return (), "missing_check", f"subtask {i} has no description"

        raw_check = raw.get("check")
        if not raw_check:
            return (), "missing_check", f"subtask {i} ({description!r}) has no check field"

        check_result = _build_check(raw_check)
        if isinstance(check_result, str):
            return (), "missing_check", f"subtask {i} ({description!r}): {check_result}"

        check = check_result

        raw_caps = raw.get("capabilities", [])
        subtask_caps: frozenset[Capability] = frozenset()
        if isinstance(raw_caps, list):
            valid_caps: set[Capability] = set()
            for c in raw_caps:
                with contextlib.suppress(ValueError):
                    valid_caps.add(Capability(c))
            subtask_caps = frozenset(valid_caps) & item_capabilities

        raw_deps = raw.get("depends_on", [])
        depends_on: tuple[int, ...] = tuple(
            int(d)
            for d in (raw_deps if isinstance(raw_deps, list) else [])
            if isinstance(d, (int, float)) and int(d) >= 0
        )

        subtasks.append(
            Subtask(
                subtask_id=f"{item_id}#{i}",
                index=i,
                description=description,
                check=check,
                capabilities=subtask_caps,
                depends_on=depends_on,
                status=SubtaskStatus.pending,
                attempts=(),
            )
        )

    # Cycle detection over depends_on.
    dep_map: dict[int, tuple[int, ...]] = {i: s.depends_on for i, s in enumerate(subtasks)}
    if _has_cycle(len(subtasks), dep_map):
        return (), "invalid_plan", "depends_on graph contains a cycle"

    return tuple(subtasks), None, None


# ---------------------------------------------------------------------------
# Journal serialisation helper
# ---------------------------------------------------------------------------


def _plan_payload(plan: Plan) -> dict[str, Any]:
    return {
        "source": plan.source,
        "created_at": plan.created_at,
        "harness": {"agent": plan.harness.agent, "model": plan.harness.model},
        "subtask_count": len(plan.subtasks),
        "subtasks": [
            {
                "subtask_id": s.subtask_id,
                "index": s.index,
                "description": s.description,
                "check": {
                    "kind": s.check.kind.value,
                    "statement": s.check.statement,
                    **({"command": list(s.check.command)} if s.check.command else {}),
                    **({"path": s.check.path} if s.check.path else {}),
                    **({"pattern": s.check.pattern} if s.check.pattern else {}),
                    **({"rationale": s.check.rationale} if s.check.rationale else {}),
                    "expect_status": s.check.expect_status,
                    "timeout_s": s.check.timeout_s,
                },
                "capabilities": sorted(c.value for c in s.capabilities),
                "depends_on": list(s.depends_on),
            }
            for s in plan.subtasks
        ],
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def decompose(
    item: TodoItem,
    *,
    config: Config,
    journal: Journal,
    cwd: Path,
    _run_agent: Any | None = None,
    on_rejected: Any | None = None,
) -> Plan:
    """Decompose *item* into a validated Plan via the agent harness.

    Retries the agent call once if the response is retriable-bad (missing or
    placeholder check, unparseable JSON).  Non-retriable failures (plan too
    long, cyclic depends_on) are raised immediately.

    Raises DecomposeError on irrecoverable failure.
    Journals ``plan_rejected`` per bad attempt and ``plan_created`` on success.

    ``on_rejected`` is called with (reason, attempt_no, retrying) after each bad
    attempt. Display only — a progress reporter has no say in control flow and
    must never be able to fail a decomposition.
    """
    runner = _run_agent if _run_agent is not None else _default_run_agent

    resolved = config.harness.for_stage("decompose")
    harness_info = HarnessInfo(
        agent=resolved.agent,
        model=resolved.model,
        harness=resolved.agent,
        invoked_as=resolved.agent,
    )

    authored = bool(item.authored_subtasks)
    base_prompt = _authored_prompt(item, config) if authored else _model_prompt(item, config)
    source = "authored" if authored else "model"

    last_rejection: str = "parse_error"
    last_rejection_detail: str | None = None

    for attempt_no in range(2):  # initial + one retry (AC3.1)
        # On the retry, append the specific rule violation so the model knows
        # what it did wrong rather than receiving a generic "missing check".
        if attempt_no == 0 or last_rejection_detail is None:
            prompt = base_prompt
        else:
            prompt = base_prompt + f"\n\nRetry because: {last_rejection_detail}"

        result: AgentResult = runner(
            prompt,
            cwd=cwd,
            capabilities=item.capabilities,
            timeout_s=config.subtask_timeout_s,
            harness=harness_info,
            # Planning is a single structured completion. Do not let the
            # planner do the work it is supposed to be planning.
            allow_tools=False,
        )

        # A harness-level outage (usage limit, auth failure, missing binary)
        # is not a bad plan — the CLI never ran the prompt. Journal it so the
        # evidence is inspectable (nothing inspected it before: see run
        # 20260812-0525-c9f7), without changing today's parse/retry path.
        harness_error = detect_harness_error(result)
        if harness_error is not None:
            journal.append(
                HARNESS_ERROR,
                item_id=item.item_id,
                payload={
                    "stage": "decompose",
                    "attempt": attempt_no + 1,
                    "reason": harness_error,
                    "exit_status": result.exit_status,
                    "agent_stdout_head": result.stdout[:500],
                    "agent_stderr_head": result.stderr[:500],
                },
            )

        data: dict[str, Any] | None = None
        if result.exit_status == 0 and not result.timed_out:
            data = _extract_json(result.stdout)

        rejection: str | None
        detail: str | None
        subtasks: tuple[Subtask, ...]
        if data is None:
            subtasks, rejection, detail = (), "parse_error", "response is not valid JSON"
        else:
            subtasks, rejection, detail = _parse_and_validate(
                data,
                item.item_id,
                item.capabilities,
                config.max_subtasks,
                item.authored_subtasks,
            )

        if rejection is None:
            # Success — journal the full plan before returning (AC3.6).
            created_at = stamp()
            session_id = make_session_id()
            plan = Plan(
                item_id=item.item_id,
                subtasks=subtasks,
                source=source,
                created_at=created_at,
                harness=harness_info,
                session_id=session_id,
            )
            payload = _plan_payload(plan)
            payload["session_id"] = session_id
            journal.append(
                PLAN_CREATED,
                item_id=item.item_id,
                payload=payload,
            )
            return plan

        last_rejection = rejection
        last_rejection_detail = detail
        if on_rejected is not None:
            on_rejected(
                detail or rejection,
                attempt_no + 1,
                rejection in _RETRIABLE and attempt_no == 0,
            )
        journal_payload: dict[str, Any] = {
            "attempt": attempt_no + 1,
            "reason": rejection,
            "agent_stdout_head": result.stdout[:500],
        }
        if detail:
            journal_payload["rejection_detail"] = detail
        journal.append(
            PLAN_REJECTED,
            item_id=item.item_id,
            payload=journal_payload,
        )

        # Non-retriable failures abort immediately.
        if rejection not in _RETRIABLE:
            break

    failure_code = _FAILURE_CODE.get(last_rejection, FailureCode.unverifiable_plan)
    raise DecomposeError(
        failure_code,
        f"Decomposition of {item.item_id!r} failed: {last_rejection}",
    )
