# SPDX-License-Identifier: Apache-2.0
"""Agent-CLI abstraction — the single choke point for all harness invocations.

Public API
----------
SUPPORTED_HARNESSES : frozenset[str]
AgentResult         – outcome of one agent invocation
build_argv()        – pure argv construction, testable without launching anything
prompt_via_stdin()  – True iff this harness reads the prompt from stdin
scrub_env()         – return a cleaned environment dict
run_agent()         – dispatch one agent invocation and return its result
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .models import Capability, HarnessInfo

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_HARNESSES: frozenset[str] = frozenset(
    {"claude", "codex", "cursor", "gemini", "opencode", "kiro"}
)

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

    Raises ValueError for an unsupported harness name.
    """
    if harness_name not in SUPPORTED_HARNESSES:
        raise ValueError(f"Unsupported harness: {harness_name!r}")

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
        ]
        for tool in disallowed:
            argv += ["--disallowedTools", tool]
        argv += model_args
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
) -> AgentResult:
    """Build argv, scrub env, and run the agent subprocess.

    Returns an AgentResult whose ``timed_out`` flag is set on timeout.
    Never raises — all subprocess errors are captured in the result.
    """
    agent_bin = harness.invoked_as or harness.harness
    harness_name = harness.harness
    model: str | None = harness.model if harness.model else None

    argv = build_argv(
        agent_bin=agent_bin,
        harness_name=harness_name,
        model=model,
        capabilities=capabilities,
        prompt=prompt,
        allow_tools=allow_tools,
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
