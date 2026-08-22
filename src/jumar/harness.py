# SPDX-License-Identifier: Apache-2.0
"""Agent-CLI abstraction — the single choke point for all harness invocations.

Public API
----------
SUPPORTED_HARNESSES  : frozenset[str]
IN_PROCESS_HARNESSES : frozenset[str] – harnesses jumar drives itself (see openai_agent.py)
AgentResult          – outcome of one agent invocation
build_argv()         – pure argv construction, testable without launching anything
prompt_via_stdin()   – True iff this harness reads the prompt from stdin
scrub_env()          – return a cleaned environment dict
run_agent()          – dispatch one agent invocation and return its result
detect_harness_error() – classify a result as a harness-level infrastructure
                        outage (usage limit, auth failure, missing binary), or
                        None when it is an ordinary agent response.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .models import Capability, HarnessInfo

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_HARNESSES: frozenset[str] = frozenset(
    {"claude", "codex", "cursor", "gemini", "opencode", "kiro", "openai"}
)

# Harnesses jumar drives itself, in-process, instead of spawning an agent-CLI
# subprocess. build_argv() has nothing to build for one of these — there is no
# argv, only an HTTP call — so it raises rather than inventing one; run_agent()
# branches to openai_agent.run_openai_agent() before it ever reaches the
# subprocess path below. See openai_agent.py.
IN_PROCESS_HARNESSES: frozenset[str] = frozenset({"openai"})

# Harnesses that support session resumption via a --resume flag.
# Only claude accepts --resume <session-id>; all others run a fresh context
# every invocation and must fall back to today's behaviour.
SESSION_RESUMABLE_HARNESSES: frozenset[str] = frozenset({"claude"})

# Harnesses that cannot express tool-level denials (git push, gh, sendmail…).
# Using one of these without allow_unrestricted_harness = true is a startup error.
UNRESTRICTED_HARNESSES: frozenset[str] = frozenset(
    {"codex", "cursor", "gemini", "opencode", "kiro"}
)

# Hard-deny tool patterns embedded in every claude invocation.
# These mirror the spec-loop's --disallowedTools defence in depth.
_CLAUDE_HARD_DENY_TOOLS: tuple[str, ...] = ("Bash(git push:*)", "Bash(gh:*)")

# claude tool names to disable when network capability is not granted.
_CLAUDE_NO_NETWORK_TOOLS: tuple[str, ...] = ("WebSearch", "WebFetch")

# Every tool claude exposes. Disabled wholesale for PLANNING calls.
#
# Decomposition wants one structured completion — a JSON plan — not an agent
# session. Left with tools, a planner handed an item about fetching documents
# will go and fetch them: observed 2026-08-11, where a plan call for a
# 15-subtask audit item ran past twenty minutes because the planner started
# browsing vendor sites instead of authoring checks. Planning must not touch
# the world it is planning against.
_CLAUDE_ALL_TOOLS: tuple[str, ...] = (
    "Bash",
    "Read",
    "Write",
    "Edit",
    "NotebookEdit",
    "Glob",
    "Grep",
    "WebSearch",
    "WebFetch",
    "Task",
    "TodoWrite",
)

# The single env var each harness needs (empty string means none).
_HARNESS_API_KEY: dict[str, str] = {
    "claude": "ANTHROPIC_API_KEY",
    "codex": "OPENAI_API_KEY",
    "opencode": "ANTHROPIC_API_KEY",
    "cursor": "",
    "gemini": "GOOGLE_API_KEY",
    "kiro": "",
}

# Env keys always stripped from the subprocess environment.
_STRIP_ENV_KEYS: frozenset[str] = frozenset({"GITHUB_TOKEN", "GH_TOKEN", "SSH_AUTH_SOCK"})


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentResult:
    """Outcome of one agent invocation."""

    exit_status: int
    stdout: str
    stderr: str
    timed_out: bool
    # Last non-empty stdout line — stored as a *claim*, never as a verdict.
    agent_claim: str | None


# ---------------------------------------------------------------------------
# Harness-level outage detection
# ---------------------------------------------------------------------------
#
# A harness-level outage — the CLI itself never got to run the prompt — is a
# different kind of failure than a working agent producing a bad response.
# Undetected, it is misread as the latter: run 20260812-0525-c9f7's
# agent_stdout_head was "You've hit your session limit ...", which the
# decompose stage parsed as a rejected JSON plan and journalled as an
# ordinary `plan_rejected`. Nothing downstream inspected the text, so the
# item's @failed= budget burned on infrastructure the item's author had no
# way to fix by rewriting their todo line.
#
# Categories, and the signatures that name them. Matched case-insensitively
# against stdout+stderr combined. Phrase signatures are safe as substrings;
# short/generic ones (401, login) are matched as whole words to cut down on
# false positives from an agent legitimately discussing HTTP status codes or
# writing a login form.

HARNESS_ERROR_USAGE_LIMIT = "usage_limit"
HARNESS_ERROR_AUTH_FAILURE = "auth_failure"
HARNESS_ERROR_BINARY_MISSING = "binary_missing"

_USAGE_LIMIT_PHRASES: tuple[str, ...] = (
    "session limit",
    "rate limit",
    "credit balance",
    "usage limit",
)
_AUTH_FAILURE_PHRASES: tuple[str, ...] = (
    "failed to authenticate",
    "invalid api key",
)
_AUTH_FAILURE_WORDS: tuple[str, ...] = ("401", "login")
_BINARY_MISSING_PHRASES: tuple[str, ...] = (
    "no such file or directory",
    "command not found",
    "is not recognized as an internal or external command",
)


def detect_harness_error(result: AgentResult) -> str | None:
    """Classify *result* as a harness-level infrastructure outage, or None.

    Returns one of HARNESS_ERROR_USAGE_LIMIT / HARNESS_ERROR_AUTH_FAILURE /
    HARNESS_ERROR_BINARY_MISSING when stdout/stderr carries the signature of
    a usage-limit hit, an authentication failure, or a missing agent binary —
    the harness CLI failed to run at all, as opposed to running and producing
    a bad response. Returns None for everything else, including a genuine
    timeout (already modelled separately as ``AgentResult.timed_out`` /
    ``FailureCode.timed_out``, which retrying can plausibly fix — an outage
    cannot).

    Pure function: only inspects fields already on *result*. Callers decide
    what to do with the classification (journal it, skip a retry, exclude it
    from @failed= accounting); this function only detects and names it.
    """
    if result.timed_out:
        return None
    haystack = f"{result.stdout}\n{result.stderr}".lower()
    if any(phrase in haystack for phrase in _BINARY_MISSING_PHRASES):
        return HARNESS_ERROR_BINARY_MISSING
    if any(phrase in haystack for phrase in _USAGE_LIMIT_PHRASES):
        return HARNESS_ERROR_USAGE_LIMIT
    if any(phrase in haystack for phrase in _AUTH_FAILURE_PHRASES) or any(
        re.search(rf"\b{re.escape(word)}\b", haystack) for word in _AUTH_FAILURE_WORDS
    ):
        return HARNESS_ERROR_AUTH_FAILURE
    return None


# ---------------------------------------------------------------------------
# Pure argv construction (testable without launching)
# ---------------------------------------------------------------------------


def prompt_via_stdin(harness_name: str) -> bool:
    """True iff this harness expects the prompt on stdin rather than as an argv token."""
    return harness_name in {"claude", "codex"}


def build_argv(
    *,
    agent_bin: str,
    harness_name: str,
    model: str | None,
    capabilities: frozenset[Capability],
    prompt: str,
    allow_tools: bool = True,
    session_id: str | None = None,
    session_is_new: bool = False,
) -> list[str]:
    """Build the argv list for one agent invocation.

    ``allow_tools=False`` is for planning: the agent must answer from the
    prompt alone. Only harnesses that can express tool restrictions honour it —
    see UNRESTRICTED_HARNESSES — so for the others it is a no-op and the
    caller gets an agent that may still reach for the world.

    Pure function — does not launch anything.  For stdin-based harnesses
    (claude, codex) the prompt is NOT embedded in the returned argv; the
    caller must write it to stdin.  For all others it appears as the final
    positional argument.

    ``session_id`` is only honoured by harnesses in SESSION_RESUMABLE_HARNESSES
    (currently only "claude").  When provided for a non-resumable harness it is
    silently ignored, preserving today's behaviour for those callers.

    ``session_is_new`` selects the flag: True emits ``--session-id`` (create
    the session with this UUID — first subtask of an item), False emits
    ``--resume`` (continue it — every later subtask and all repairs).  A
    session must be created before it can be resumed; resuming an id claude
    has never seen is an error.

    Raises ValueError for an unsupported harness name.
    """
    if harness_name not in SUPPORTED_HARNESSES:
        raise ValueError(f"Unsupported harness: {harness_name!r}")
    if harness_name in IN_PROCESS_HARNESSES:
        raise ValueError(
            f"{harness_name!r} is an in-process harness — it has no argv. "
            "run_agent() dispatches it via openai_agent.run_openai_agent() "
            "instead of building a subprocess command line."
        )

    model_args: list[str] = ["--model", model] if model else []
    has_network = Capability.network in capabilities

    if harness_name == "claude":
        disallowed: list[str] = list(_CLAUDE_HARD_DENY_TOOLS)
        if not allow_tools:
            disallowed.extend(_CLAUDE_ALL_TOOLS)
        elif not has_network:
            disallowed.extend(_CLAUDE_NO_NETWORK_TOOLS)
        argv: list[str] = [
            agent_bin,
            "-p",
            "--dangerously-skip-permissions",
            # No MCP servers, ever. Two reasons, and the second is the serious
            # one:
            #
            # 1. Startup cost. Every invocation boots every configured server
            #    before reading the prompt, and jumar spawns a fresh agent per
            #    subtask. On a machine with a dozen servers that dominates the
            #    run.
            # 2. The send boundary. `config.py` denies sendmail, ssh, scp and
            #    friends — and an MCP mail or calendar server would hand the
            #    agent exactly the capability the deny list exists to withhold,
            #    without appearing in any argv we inspect. A boundary that an
            #    unrelated user config can open is not a boundary.
            #
            # `--strict-mcp-config` with no `--mcp-config` means: use only the
            # servers named there, and none are.
            "--strict-mcp-config",
        ]
        for tool in disallowed:
            argv += ["--disallowedTools", tool]
        argv += model_args
        if session_id is not None:
            argv += ["--session-id" if session_is_new else "--resume", session_id]
        # Prompt is piped to stdin; not embedded in argv.

    elif harness_name == "codex":
        argv = [
            agent_bin,
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
        ]
        argv += model_args
        argv += ["-"]  # signals: read prompt from stdin

    elif harness_name == "cursor":
        argv = [
            agent_bin,
            "agent",
            "--print",
            "--force",
            "--trust",
        ]
        argv += model_args
        argv += [prompt]

    elif harness_name == "gemini":
        argv = [agent_bin, "--yolo"]
        argv += model_args
        argv += ["--prompt", prompt]

    elif harness_name == "opencode":
        argv = [agent_bin, "run", "--auto"]
        argv += model_args
        argv += [prompt]

    else:  # kiro
        argv = [agent_bin, "chat", "--no-interactive", prompt]

    return argv


# ---------------------------------------------------------------------------
# Environment scrubbing
# ---------------------------------------------------------------------------


def scrub_env(
    harness_name: str,
    env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return a sanitised copy of *env* (defaults to os.environ).

    Strips:
    - GITHUB_TOKEN, GH_TOKEN, SSH_AUTH_SOCK — always removed.
    - Any *_API_KEY not belonging to the named harness — removed so one
      agent cannot read another's credentials.
    """
    base: dict[str, str] = env if env is not None else dict(os.environ)
    keep_key = _HARNESS_API_KEY.get(harness_name, "")

    result: dict[str, str] = {}
    for k, v in base.items():
        if k in _STRIP_ENV_KEYS:
            continue
        if k.endswith("_API_KEY") and k != keep_key:
            continue
        result[k] = v
    return result


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def run_agent(
    prompt: str,
    *,
    cwd: Path,
    capabilities: frozenset[Capability],
    timeout_s: int,
    harness: HarnessInfo,
    allow_tools: bool = True,
    session_id: str | None = None,
    session_is_new: bool = False,
) -> AgentResult:
    """Build argv, scrub env, and run the agent subprocess.

    Returns an AgentResult whose ``timed_out`` flag is set on timeout.
    Never raises — all subprocess errors are captured in the result.
    """
    harness_name = harness.harness

    if harness_name in IN_PROCESS_HARNESSES:
        from .openai_agent import run_openai_agent

        return run_openai_agent(
            prompt,
            cwd=cwd,
            capabilities=capabilities,
            timeout_s=timeout_s,
            harness=harness,
            allow_tools=allow_tools,
        )

    agent_bin = harness.invoked_as or harness.harness
    model: str | None = harness.model if harness.model else None

    argv = build_argv(
        agent_bin=agent_bin,
        harness_name=harness_name,
        model=model,
        capabilities=capabilities,
        prompt=prompt,
        allow_tools=allow_tools,
        session_id=session_id,
        session_is_new=session_is_new,
    )
    env = scrub_env(harness_name)
    stdin_bytes = prompt.encode() if prompt_via_stdin(harness_name) else None

    try:
        proc = subprocess.run(  # noqa: S603
            argv,
            cwd=cwd,
            env=env,
            input=stdin_bytes,
            capture_output=True,
            timeout=timeout_s,
            shell=False,
            check=False,
        )
        stdout = proc.stdout.decode(errors="replace")
        stderr = proc.stderr.decode(errors="replace")
        lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
        agent_claim: str | None = lines[-1] if lines else None
        return AgentResult(
            exit_status=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=False,
            agent_claim=agent_claim,
        )
    except subprocess.TimeoutExpired as exc:
        raw_out = exc.stdout if isinstance(exc.stdout, bytes) else b""
        raw_err = exc.stderr if isinstance(exc.stderr, bytes) else b""
        return AgentResult(
            exit_status=-1,
            stdout=raw_out.decode(errors="replace"),
            stderr=raw_err.decode(errors="replace"),
            timed_out=True,
            agent_claim=None,
        )
    except OSError as exc:
        return AgentResult(
            exit_status=-1,
            stdout="",
            stderr=str(exc),
            timed_out=False,
            agent_claim=None,
        )
