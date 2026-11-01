# SPDX-License-Identifier: Apache-2.0
"""Stage 3 — Decompose: turn a TodoItem into a Plan of Subtasks.

Public API
----------
DecomposeError  – raised when decomposition fails irrecoverably.
decompose()     – call the agent harness, validate the response, return a Plan.

Hard rules enforced here (not by the prompt):
- A subtask with no ``check``, or with an empty/placeholder ``check``, is
  rejected and retried once, then the item fails as ``unverifiable_plan``.
- A ``judge`` check without ``rationale``, or without ``path``, is also
  retried once.
- A plan longer than the item's cap (``@max-subtasks=``, else config
  ``max_subtasks``; see :func:`item_max_subtasks`) is rejected immediately
  (``plan_too_long``).
- A cyclic ``depends_on`` is rejected immediately (``invalid_plan``).
- If the item has ``authored_subtasks``, those descriptions are used verbatim
  and the agent only supplies the checks.
- A subtask with an author-written check (``authored_checks``) keeps that
  check unmodified, and the agent is never asked for one. When every
  authored subtask carries a check, no agent call is made at all.
- The full plan is journalled before returning (AC3.6).
"""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path
from typing import Any

from .clock import make_session_id, stamp
from .config import Config, resolve_harness
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
_RETRIABLE: frozenset[str] = frozenset(
    {"missing_check", "parse_error", "timed_out", "empty_response", "harness_error"}
)

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
    # --- Enforced in code: a plan that breaks one of these is rejected and sent
    # back with the reason. Named briefly so the model avoids the round trip.
    "ENFORCED (a plan breaking any of these is rejected and returned to you):",
    "- Every subtask has a `check`; `check.statement` is specific, never empty,"
    " 'n/a', 'none', 'TODO' or 'verify manually'.",
    "- `kind=command` has `command` as a JSON argv list, never a shell string, and"
    ' never a shell wrapper (["bash", "-c", "..."], sh/zsh/dash -c).',
    "- A check must be able to FAIL: no `true`, `echo`, `date`, bare `ls`, and no"
    " `pattern` that matches the empty string (`.*`, `(x)?`).",
    "- No `grep -P` / `-z`: BSD grep on macOS lacks them. Use kind=file with a `pattern` instead.",
    "- No grep with several patterns (`-e A -e B`, `-f`): it passes if ANY matches."
    " Use kind=file with one pattern, e.g. (?s)First:.*Second:.",
    "- `pattern` is a valid Python regex; it is not one exact 5+ word phrase, and"
    " not just a word like 'done', 'draft', 'complete' or 'success'.",
    "- `kind=file` / `kind=absence` need `path`; `kind=judge` needs a `rationale`"
    " and a `path`. `depends_on` holds 0-based indices and forms a DAG.",
    "- `gh` is never run; use `jumar-gh` with the same arguments (read-only:"
    " `api` GETs, `api graphql` queries, `issue`/`pr` view, list, diff, checks,"
    " `search`; needs `network`).",
    "",
    "JUDGEMENT (no code can check these for you):",
    "- Use `kind=judge` ONLY when no executable check is possible. The judge"
    " cannot run commands or open files: it is shown `path` and nothing else, so"
    " name the ONE file the subtask leaves behind, or make the subtask write one.",
    "- Prefer `kind=file` with `path` and `pattern` for anything that reads a"
    " file: the pattern runs against the WHOLE file with Python's `re`, and a"
    " miss puts the file's content in the evidence. Use `kind=command` only when"
    " a program must RUN something.",
    "- A `pattern` must tolerate surface variation: anchor on the fact, not your"
    ' own phrasing of it — prefer "Art(icle)?\\s*2\\(12\\)" over "Art 2(12)".',
    "- `kind=file` FAILS on a zero-byte file, so assert content, not existence:"
    " a phrase that can only appear in the real document.",
    "- Check the OUTCOME, not the artefact of your own effort: that the facts are"
    " right, not that a word you typed is present.",
    "- `kind=absence` without `pattern` asserts the PATH does not exist; give"
    " `pattern` to assert a file does not CONTAIN something.",
    "- `capabilities` must be a subset of the granted capabilities.",
    "- Do NOT use tools, read files, run commands, or browse. Answer from this"
    " prompt alone. You are writing a plan, not doing the work.",
    "- Return ONLY valid JSON — no prose, no markdown fences.",
]


def item_max_subtasks(item: TodoItem, config: Config) -> int:
    """The plan-length cap for *item*: ``@max-subtasks=`` overrides config.

    A value that is not a positive integer falls back to the config cap;
    ingest has already warned about it, so the fallback is not silent.
    """
    raw = item.meta.get("max-subtasks")
    if raw is not None:
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if value >= 1:
            return value
    return config.max_subtasks


def _model_prompt(item: TodoItem, config: Config) -> str:
    caps = ", ".join(sorted(c.value for c in item.capabilities)) or "none"
    ctx_block = ("\n\nContext:\n" + "\n".join(item.context)) if item.context else ""
    rules = "\n".join(_RULES)
    return (
        f"Decompose the following todo item into an ordered sequence of subtasks.\n\n"
        f"Item: {item.text}{ctx_block}\n\n"
        f"Granted capabilities: {caps}\n"
        f"Maximum subtasks: {item_max_subtasks(item, config)}\n\n"
        f"Rules:\n{rules}\n\n"
        f"Schema:\n{_SCHEMA_EXAMPLE}"
    )


def _authored_prompt(item: TodoItem, texts: tuple[str, ...]) -> str:
    caps = ", ".join(sorted(c.value for c in item.capabilities)) or "none"
    numbered = "\n".join(f"{i}. {text}" for i, text in enumerate(texts))
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
    # Last resort: decode the first object and ignore whatever follows it.
    # `json.loads` on the whole remainder is strict about trailing content, so
    # a model that closes its object and then adds a sentence of commentary --
    # or an unclosed ``` fence, which defeats the regex above -- was reported
    # as "response is not valid JSON" even though a complete object was there.
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start >= 0:
        try:
            result, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
            continue
        if isinstance(result, dict):
            return result
        start = text.find("{", start + 1)
    return None


def _dump_rejected(journal: Any, item_id: str, attempt: int, stdout: str) -> str | None:
    """Write a rejected plan response next to the journal. Best effort."""
    path = getattr(journal, "_path", None)
    if path is None or not stdout:
        return None
    try:
        out_dir = Path(path).parent / "artifacts"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"plan-rejected-{item_id}-{attempt}.txt"
        out.write_text(stdout, encoding="utf-8")
    except OSError:
        return None
    return str(out)


# ---------------------------------------------------------------------------
# Check + Subtask construction
# ---------------------------------------------------------------------------


def _bsd_grep_violation(command: tuple[str, ...]) -> str | None:
    """Return the offending flag if *command* is a grep BSD grep cannot run.

    BSD grep, which is what /usr/bin/grep is on macOS, has neither `-P`
    (PCRE) nor `-z` (NUL-separated input). Either one exits 2 with "invalid
    option", which the command verifier records as a failed check, so a
    correct artefact fails and all three repair attempts are spent rewriting
    work that was already right.

    Whether a PCRE-capable grep is first on PATH is not something a plan can
    rely on: across this workspace's runs, `grep -P` ran fine in every run up
    to 20260916-1511-8b36 and then failed in every one from 20260917-1433-61eb
    (35 such exits over two runs, zero real defects). A check must not depend
    on which grep the run happens to find.

    Option arguments are respected, so `grep -e -P file` is a search for the
    literal text "-P" and is left alone, as is anything after a bare `--`.
    """
    if not command:
        return None
    if command[0].rsplit("/", 1)[-1] not in {"grep", "egrep", "fgrep", "rgrep"}:
        return None
    # Short options that consume a value, either glued to the cluster or as
    # the following argv element.
    takes_value = set("efmABCDd")
    skip_next = False
    for arg in command[1:]:
        if skip_next:
            skip_next = False
            continue
        if arg == "--":
            break
        if arg in {"--perl-regexp", "--null-data"}:
            return arg
        if arg.startswith("--") or not arg.startswith("-") or arg == "-":
            continue
        for i, flag in enumerate(arg[1:], start=1):
            if flag in {"P", "z"}:
                return f"-{flag}"
            if flag in takes_value:
                # The rest of the cluster is this flag's value; if the cluster
                # ends here the value is the next argv element.
                skip_next = i == len(arg) - 1
                break
    return None


_GREPS = {"grep", "egrep", "fgrep", "rgrep"}
_REGEX_META = set("\\.^$*+?()[]{}|")
# A pattern that is nothing but one of these words proves the agent typed it,
# not that the work is right. "OK" is deliberately absent: as a marker a
# subtask writes into real output it is a fact about that output.
_SELF_REPORT_WORDS = frozenset(
    {"done", "draft", "complete", "completed", "finished", "success", "todo", "wip", "placeholder"}
)
_BRITTLE_WORDS = 5


def _grep_patterns(command: tuple[str, ...]) -> tuple[list[str], bool]:
    """(patterns, reads_pattern_file) for a grep argv; ([], False) otherwise."""
    if not command or command[0].rsplit("/", 1)[-1] not in _GREPS:
        return [], False
    patterns: list[str] = []
    from_file = False
    operands: list[str] = []
    takes_value = set("efmABCDd")
    args = list(command[1:])
    i = 0
    while i < len(args):
        arg = args[i]
        i += 1
        if arg == "--":
            operands.extend(args[i:])
            break
        if arg.startswith("--"):
            name, _, value = arg.partition("=")
            if name in ("--regexp", "--file"):
                if not value and i < len(args):
                    value = args[i]
                    i += 1
                if name == "--regexp":
                    patterns.append(value)
                else:
                    from_file = True
            continue
        if not arg.startswith("-") or arg == "-":
            operands.append(arg)
            continue
        for j, flag in enumerate(arg[1:], start=1):
            if flag in takes_value:
                value = arg[j + 1 :]
                if not value and i < len(args):
                    value = args[i]
                    i += 1
                if flag == "e":
                    patterns.append(value)
                elif flag == "f":
                    from_file = True
                break
    if not patterns and not from_file and operands:
        patterns.append(operands[0])
    return patterns, from_file


def _pattern_violation(pattern: str, where: str) -> str | None:
    """A model-written pattern that cannot prove the work: brittle, or self-report."""
    bare = pattern.replace("\\b", "").strip("^$ \t").lower()
    if bare in _SELF_REPORT_WORDS:
        return (
            f"{where} {pattern!r} only checks that the word {bare!r} was written, which"
            " proves effort, not outcome. Match text that only the finished work"
            " contains (a fact, a number, a heading from the real source)."
        )
    if not (_REGEX_META & set(pattern)) and len(pattern.split()) >= _BRITTLE_WORDS:
        return (
            f"{where} {pattern!r} demands one exact spelling of a {len(pattern.split())}-word"
            " phrase, so a correct document worded differently fails it. Anchor on the"
            " fact: a short distinctive term, with \\s+ between words and"
            " alternation for variants, e.g. Art(icle)?\\s*2\\(12\\)."
        )
    return None


def _plan_rule_violation(
    kind: CheckKind, command: tuple[str, ...] | None, pattern: str | None
) -> str | None:
    """Rules the model keeps breaking, enforced on model-written plans (W22).

    The prose in _RULES only names these; this is where they are decided.
    """
    if command:
        program = command[0].rsplit("/", 1)[-1]
        if program == "gh":
            return (
                "kind=command runs `gh`, which is always refused. For GitHub data use"
                " `jumar-gh` with the same arguments (read-only; needs `network`)."
            )
        patterns, from_file = _grep_patterns(command)
        if from_file or len(patterns) > 1:
            return (
                "kind=command is a grep with several patterns (-e ... -e ..., or -f),"
                " which passes when ANY one matches: it cannot prove that all of them,"
                " or that they appear in order. Use kind=file with a `path` and one"
                " `pattern` such as (?s)First:.*Second:.*Third: instead."
            )
        for grep_pattern in patterns:
            found = _pattern_violation(grep_pattern, "grep pattern")
            if found:
                return found
    if pattern is not None and kind in (CheckKind.file, CheckKind.absence):
        return _pattern_violation(pattern, "pattern")
    return None


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
        bad_flag = _bsd_grep_violation(command)
        if bad_flag is not None:
            return (
                f"kind=command uses `grep {bad_flag}`, which BSD grep on macOS does not"
                " support: it exits 2 with 'invalid option' and the check can never"
                " pass. Use kind=file with a `path` and a `pattern` instead. The file"
                " verifier matches the pattern against the whole file with Python's"
                " re, so an ordered multi-section pattern such as (?s)A:.*B:.*C: works"
                " there."
            )

    rule = _plan_rule_violation(kind, command, pattern)
    if rule is not None:
        return rule

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

        if authored_texts:
            subtask_caps = item_capabilities
        else:
            raw_caps = raw.get("capabilities", [])
            valid_caps: set[Capability] = set()
            if isinstance(raw_caps, list):
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


def _pinned_indices(item: TodoItem) -> tuple[int, ...]:
    """Indices of authored subtasks that carry an author-written check."""
    return tuple(i for i, c in enumerate(item.authored_checks) if c is not None)


def _merge_pinned(item: TodoItem, model_subtasks: tuple[Subtask, ...]) -> tuple[Subtask, ...]:
    """Interleave author-checked subtasks with the ones the model checked.

    ``model_subtasks`` covers only the subtasks without a pinned check, in
    order, with ``depends_on`` indices relative to that subset; they are
    mapped back onto positions in the full authored list.
    """
    pinned = set(_pinned_indices(item))
    unpinned = [i for i in range(len(item.authored_subtasks)) if i not in pinned]
    from_model = iter(model_subtasks)
    merged: list[Subtask] = []
    for i, text in enumerate(item.authored_subtasks):
        check = item.authored_checks[i] if i in pinned else None
        if check is not None:
            merged.append(
                Subtask(
                    subtask_id=f"{item.item_id}#{i}",
                    index=i,
                    description=text,
                    check=check,
                    capabilities=item.capabilities,
                    depends_on=(),
                    status=SubtaskStatus.pending,
                    attempts=(),
                )
            )
            continue
        st = next(from_model)
        merged.append(
            Subtask(
                subtask_id=f"{item.item_id}#{i}",
                index=i,
                description=st.description,
                check=st.check,
                capabilities=st.capabilities,
                depends_on=tuple(unpinned[d] for d in st.depends_on if d < len(unpinned)),
                status=SubtaskStatus.pending,
                attempts=(),
            )
        )
    return tuple(merged)


# ---------------------------------------------------------------------------
# Journal serialisation helper
# ---------------------------------------------------------------------------


def _plan_payload(plan: Plan, pinned: tuple[int, ...] = ()) -> dict[str, Any]:
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
                "check_source": "author" if s.index in pinned else "model",
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

    resolved = resolve_harness(config, item.meta.get("harness")).for_stage("decompose")
    harness_info = HarnessInfo(
        agent=resolved.agent,
        model=resolved.model,
        harness=resolved.agent,
        invoked_as=resolved.agent,
        base_url=resolved.base_url,
        api_key_env=resolved.api_key_env,
        reasoning_effort=resolved.reasoning_effort,
        max_tokens=resolved.max_tokens,
        max_tool_steps=resolved.max_tool_steps,
        commands_allow=config.commands.allow,
        commands_deny=config.commands.deny,
    )

    authored = bool(item.authored_subtasks)
    source = "authored" if authored else "model"
    pinned = _pinned_indices(item) if authored else ()
    # Only the subtasks without an author-written check go to the model; a
    # pinned check is never offered up for the model to replace.
    to_check: tuple[str, ...] = tuple(
        t for i, t in enumerate(item.authored_subtasks) if i not in pinned
    )

    def _accept(subtasks: tuple[Subtask, ...], result: AgentResult | None) -> Plan:
        """Journal the full plan before returning it (AC3.6)."""
        session_id = make_session_id()
        plan = Plan(
            item_id=item.item_id,
            subtasks=subtasks,
            source=source,
            created_at=stamp(),
            harness=harness_info,
            session_id=session_id,
        )
        payload = _plan_payload(plan, pinned)
        payload["session_id"] = session_id
        # Usage for the accepted plan only. A rejected attempt's tokens are
        # still spent, but they are journalled on its own plan_rejected
        # event rather than folded into the accepted plan's total.
        payload["completion_tokens"] = getattr(result, "completion_tokens", None)
        payload["prompt_tokens"] = getattr(result, "prompt_tokens", None)
        journal.append(PLAN_CREATED, item_id=item.item_id, payload=payload)
        return plan

    max_subtasks = item_max_subtasks(item, config)

    if authored and len(item.authored_subtasks) > max_subtasks:
        journal.append(
            PLAN_REJECTED,
            item_id=item.item_id,
            payload={
                "attempt": 1,
                "reason": "plan_too_long",
                "rejection_detail": (
                    f"item has {len(item.authored_subtasks)} authored subtasks; "
                    f"maximum is {max_subtasks}"
                ),
            },
        )
        raise DecomposeError(
            FailureCode.plan_too_long,
            f"Decomposition of {item.item_id!r} failed: plan_too_long",
        )

    if authored and not to_check:
        # Every step and every proof was written by the author: nothing for a
        # model to decide, so no model is asked.
        return _accept(_merge_pinned(item, ()), None)

    base_prompt = _authored_prompt(item, to_check) if authored else _model_prompt(item, config)

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

        # Distinguish *why* there is no parseable plan. A harness that timed
        # out, one that returned nothing, and one that returned genuine
        # malformed JSON used to collapse into the same "parse_error" —
        # indistinguishable in the progress line, in `plan_rejected`, and in
        # `agent_stdout_head` (empty for the first two). On the `openai`
        # harness the empty case is the common one, not an edge: a model that
        # answers into `reasoning_content` with an empty `content` returns
        # `exit_status=0` with empty stdout — a successful call that said
        # nothing, previously reported as "not valid JSON".
        data: dict[str, Any] | None = None
        rejection: str | None
        detail: str | None
        subtasks: tuple[Subtask, ...]
        # The endpoint was never reached, or refused the call. There is no
        # response to parse, so reporting this as "response is not valid JSON"
        # is a lie: a stopped LM Studio produced `parse_error` with
        # prompt_tokens 0 and completion_tokens 0 and read exactly like the
        # model failures it was being compared against, twice, on 5 and 6 Sep.
        # `detect_harness_error` and `FailureCode.harness_error` already
        # existed; nothing consumed them.
        #
        # Guarded on exit_status because `detect_harness_error` scans stdout
        # as well as stderr, so a plan whose own text contains "connection
        # refused" would otherwise be discarded as an outage. A call that
        # returned a response has exit_status 0.
        #
        # Message only. The retry path and the FailureCode are deliberately
        # unchanged (see test_harness_outage_journalled_as_harness_error and
        # test_regression_run_20260812_0525_c9f7_...): an outage is still
        # retried once and still fails as unverifiable_plan. All that changes
        # is that the rejection now says what actually happened instead of
        # "response is not valid JSON".
        if harness_error is not None and result.exit_status != 0:
            subtasks, rejection, detail = (
                (),
                "harness_error",
                f"{harness_error}: {(result.stderr or result.stdout).strip()[:200]}".strip(),
            )
        elif result.timed_out:
            subtasks, rejection, detail = (
                (),
                "timed_out",
                f"agent call timed out after {config.subtask_timeout_s}s",
            )
        elif result.exit_status == 0 and not result.stdout.strip():
            subtasks, rejection, detail = (), "empty_response", "agent returned an empty response"
        else:
            if result.exit_status == 0:
                data = _extract_json(result.stdout)
            if data is None:
                subtasks, rejection, detail = (), "parse_error", "response is not valid JSON"
            else:
                subtasks, rejection, detail = _parse_and_validate(
                    data,
                    item.item_id,
                    item.capabilities,
                    max_subtasks,
                    to_check,
                )

        if rejection is None:
            if pinned:
                subtasks = _merge_pinned(item, subtasks)
            return _accept(subtasks, result)

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
            "completion_tokens": getattr(result, "completion_tokens", None),
            "prompt_tokens": getattr(result, "prompt_tokens", None),
            "agent_stdout_head": result.stdout[:500],
        }
        if detail:
            journal_payload["rejection_detail"] = detail
        if result.stderr:
            journal_payload["agent_stderr_head"] = result.stderr[:500]
        # A 500-character head is enough to see *that* a plan was rejected and
        # useless for seeing *why*: the malformed part of a plan is almost
        # always near its end. Write the whole response beside the journal so a
        # rejection is diagnosable after the fact instead of only reproducible.
        dump = _dump_rejected(journal, item.item_id, attempt_no + 1, result.stdout)
        if dump is not None:
            journal_payload["agent_stdout_path"] = dump
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
