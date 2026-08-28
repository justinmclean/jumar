# SPDX-License-Identifier: Apache-2.0
"""In-process harness for an OpenAI-compatible chat-completions endpoint.

Public API
----------
run_openai_agent()  – drive one agent session against ``{base_url}/chat/completions``,
                       looping on tool calls up to a step cap, and return an
                       AgentResult in the same shape every subprocess harness
                       returns (see harness.AgentResult).

Unlike every other harness, this one never spawns an agent-CLI subprocess —
jumar itself is the agent loop. The model is offered three tools
(``read_file``, ``write_file``, ``run_command``) and jumar executes each tool
call itself, gated by the subtask's declared ``Capability`` set and the
configured command allow/deny policy (``config.is_allowed()``) — the same
checks every other verifier and the execute stage already apply. That is the
whole argument for the in-process route over pointing an unrestricted CLI
harness at the endpoint: the send boundary and the ``git push`` / ``gh``
hard-deny become jumar's own enforcement, in Python, instead of a flag a
third-party binary may or may not honour — so this harness needs no
``allow_unrestricted_harness`` opt-in (see ``harness.UNRESTRICTED_HARNESSES``).

stdlib only (``urllib.request``) — ``dependencies`` in pyproject.toml stays
empty. ``timeout_s`` is a deadline across the *whole* tool-calling loop, not a
per-request timeout: a chatty local model that keeps calling tools does not
get a fresh budget on every round trip.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .config import CommandPolicy, Config, is_allowed
from .harness import AgentResult
from .models import Capability, HarnessInfo

# Default env var to read the API key from when [harness] api_key_env is unset.
DEFAULT_API_KEY_ENV = "OPENAI_API_KEY"

# Hard cap on tool-calling round trips per invocation. A model that never
# stops calling tools (or loops between two of them) must not hang the run —
# it trips this cap and the subtask is recorded as a failed attempt, exactly
# like a subprocess harness that exits non-zero.
MAX_TOOL_STEPS = 20

# Every request gets at least this many seconds even if the overall deadline
# is nearly spent, so a request already in flight is not sent with a
# zero-or-negative timeout (which urllib treats as "no timeout").
_MIN_REQUEST_TIMEOUT_S = 1.0

_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file, path relative to the working directory.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write UTF-8 text to a file, path relative to the working directory. "
                "Creates parent directories as needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Run a command as an argv list — no shell, no shell operators. "
                "Subject to the configured command allow/deny policy; a denied "
                "argv[0] is refused and nothing is spawned."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "argv": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["argv"],
            },
        },
    },
)


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def _resolve_in_cwd(cwd: Path, raw_path: str) -> Path | None:
    """Return *raw_path* resolved against *cwd*, or None if it escapes cwd.

    Containment, not just a syntactic check: ``(cwd / raw_path).resolve()``
    must still be inside ``cwd.resolve()`` after symlinks and ``..`` are
    collapsed. An absolute path or a ``../`` escape returns None rather than
    being silently clamped — the caller turns that into a refusal, the same
    shape as a capability or policy denial.
    """
    try:
        candidate = (cwd / raw_path).resolve()
        base = cwd.resolve()
    except OSError:
        return None
    if candidate != base and base not in candidate.parents:
        return None
    return candidate


def _tool_read_file(cwd: Path, args: dict[str, Any], capabilities: frozenset[Capability]) -> str:
    if Capability.read_fs not in capabilities:
        return json.dumps({"error": "capability_denied", "reason": "read_fs not granted"})
    raw_path = str(args.get("path", ""))
    target = _resolve_in_cwd(cwd, raw_path)
    if target is None:
        return json.dumps({"error": "path_outside_cwd", "path": raw_path})
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return json.dumps({"error": "read_failed", "path": raw_path, "detail": str(exc)})
    return json.dumps({"path": raw_path, "content": content})


def _tool_write_file(cwd: Path, args: dict[str, Any], capabilities: frozenset[Capability]) -> str:
    if Capability.write_fs not in capabilities:
        return json.dumps({"error": "capability_denied", "reason": "write_fs not granted"})
    raw_path = str(args.get("path", ""))
    content = str(args.get("content", ""))
    target = _resolve_in_cwd(cwd, raw_path)
    if target is None:
        return json.dumps({"error": "path_outside_cwd", "path": raw_path})
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return json.dumps({"error": "write_failed", "path": raw_path, "detail": str(exc)})
    return json.dumps({"path": raw_path, "bytes_written": len(content.encode("utf-8"))})


def _tool_run_command(
    cwd: Path,
    args: dict[str, Any],
    *,
    capabilities: frozenset[Capability],
    policy_config: Config,
) -> str:
    if Capability.run_commands not in capabilities:
        return json.dumps({"error": "capability_denied", "reason": "run_commands not granted"})
    raw_argv = args.get("argv")
    if not isinstance(raw_argv, list) or not raw_argv:
        return json.dumps({"error": "invalid_argv", "argv": raw_argv})
    argv = [str(a) for a in raw_argv]

    if not is_allowed(argv, policy_config):
        return json.dumps(
            {
                "error": "capability_denied",
                "reason": f"{argv[0]!r} is not permitted by the configured command policy",
                "argv": argv,
            }
        )

    try:
        proc = subprocess.run(  # noqa: S603
            argv,
            cwd=cwd,
            capture_output=True,
            timeout=120,
            shell=False,
            check=False,
        )
    except FileNotFoundError:
        return json.dumps({"error": "binary_not_found", "argv": argv})
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "timed_out", "argv": argv})

    return json.dumps(
        {
            "argv": argv,
            "exit_status": proc.returncode,
            "stdout": proc.stdout.decode(errors="replace")[:4000],
            "stderr": proc.stderr.decode(errors="replace")[:4000],
        }
    )


def _dispatch_tool_call(
    call: dict[str, Any],
    *,
    cwd: Path,
    capabilities: frozenset[Capability],
    policy_config: Config,
) -> str:
    function = call.get("function") or {}
    name = str(function.get("name", ""))
    raw_arguments = function.get("arguments", "{}")
    try:
        args = json.loads(raw_arguments) if isinstance(raw_arguments, str) else dict(raw_arguments)
    except (TypeError, ValueError):
        return json.dumps({"error": "invalid_arguments", "raw": raw_arguments})
    if not isinstance(args, dict):
        return json.dumps({"error": "invalid_arguments", "raw": raw_arguments})

    if name == "read_file":
        return _tool_read_file(cwd, args, capabilities)
    if name == "write_file":
        return _tool_write_file(cwd, args, capabilities)
    if name == "run_command":
        return _tool_run_command(cwd, args, capabilities=capabilities, policy_config=policy_config)
    return json.dumps({"error": "unknown_tool", "name": name})


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _post_chat_completions(
    base_url: str,
    payload: dict[str, Any],
    *,
    api_key: str | None,
    timeout_s: float,
) -> dict[str, Any]:
    """POST *payload* to ``{base_url}/chat/completions`` and return the parsed JSON body.

    Raises OSError/urllib.error.URLError/TimeoutError on transport failure and
    ValueError on an unparseable or non-object response — callers turn all of
    these into a failed AgentResult rather than letting them propagate.
    """
    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout_s) as resp:  # noqa: S310
        raw = resp.read()
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError(f"Non-object JSON response from {url}")
    return parsed


def _is_timeout_error(exc: OSError | TimeoutError | urllib.error.URLError | ValueError) -> bool:
    """Return True when *exc* represents a request timeout."""
    if isinstance(exc, TimeoutError):
        return True
    return isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_openai_agent(
    prompt: str,
    *,
    cwd: Path,
    capabilities: frozenset[Capability],
    timeout_s: int,
    harness: HarnessInfo,
    allow_tools: bool = True,
) -> AgentResult:
    """Drive one agent session against an OpenAI-compatible endpoint.

    ``harness.base_url`` (resolved per-stage by the caller, e.g.
    ``config.harness.for_stage("execute")``) selects the server;
    ``harness.api_key_env`` names the environment variable holding its API
    key, defaulting to ``OPENAI_API_KEY`` — an endpoint that needs no key
    (typical for a local server) simply resolves to an unset variable, and
    the request is then sent with no ``Authorization`` header.

    ``allow_tools=False`` (decompose) omits the ``tools`` key from the
    request entirely, matching the subprocess harnesses' behaviour of
    answering from the prompt alone.

    ``timeout_s`` bounds the *whole* loop, not one request — never raises;
    every failure mode (unreachable endpoint, unparseable response, deadline
    exceeded, step cap exceeded) is encoded in the returned AgentResult.
    """
    if not harness.base_url:
        return AgentResult(
            exit_status=-1,
            stdout="",
            stderr=(
                "openai harness selected but no base_url is configured; "
                'set [harness] base_url = "http://<host>:<port>/v1" in jumar.toml'
            ),
            timed_out=False,
            agent_claim=None,
        )

    api_key_env = harness.api_key_env or DEFAULT_API_KEY_ENV
    api_key = os.environ.get(api_key_env) or None
    policy_config = Config(
        commands=CommandPolicy(allow=harness.commands_allow, deny=harness.commands_deny)
    )

    started = time.monotonic()
    deadline = started + max(timeout_s, 0)
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    transcript: list[str] = []
    completion_tokens = 0

    def _rate() -> dict[str, Any]:
        """Throughput fields for an AgentResult, whenever the loop ends."""
        return {
            "completion_tokens": completion_tokens,
            "generation_seconds": time.monotonic() - started,
        }

    for _step in range(MAX_TOOL_STEPS):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return AgentResult(
                exit_status=-1,
                stdout="\n".join(transcript),
                stderr=f"deadline of {timeout_s}s exceeded across the tool-calling loop",
                timed_out=True,
                agent_claim=None,
                **_rate(),
            )

        payload: dict[str, Any] = {"model": harness.model, "messages": messages}
        if allow_tools:
            payload["tools"] = list(_TOOLS)

        try:
            response = _post_chat_completions(
                harness.base_url,
                payload,
                api_key=api_key,
                timeout_s=max(remaining, _MIN_REQUEST_TIMEOUT_S),
            )
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            return AgentResult(
                exit_status=-1,
                stdout="\n".join(transcript),
                stderr=f"request to {harness.base_url} failed: {exc}",
                timed_out=_is_timeout_error(exc),
                agent_claim=None,
                **_rate(),
            )

        usage = response.get("usage")
        if isinstance(usage, dict):
            with contextlib.suppress(TypeError, ValueError):
                completion_tokens += int(usage.get("completion_tokens") or 0)

        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            return AgentResult(
                exit_status=-1,
                stdout="\n".join(transcript),
                stderr=f"no choices in response from {harness.base_url}",
                timed_out=False,
                agent_claim=None,
                **_rate(),
            )

        message = choices[0].get("message") or {}
        content = str(message.get("content") or "")
        tool_calls = message.get("tool_calls") or []
        if content:
            transcript.append(content)

        if not tool_calls or not allow_tools:
            stdout = "\n".join(transcript)
            lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
            # A model can return exit_status=0 having said nothing — e.g. an
            # answer parked entirely in a `reasoning_content` field the chat-
            # completions schema does not surface here, leaving `content`
            # empty. That is a successful HTTP call but not a successful
            # answer; flag it in stderr rather than returning a bare success
            # indistinguishable from one that actually said something, so a
            # caller inspecting the result (or the harness_error/decompose
            # rejection classifiers) can tell the two apart.
            stderr = (
                "" if stdout.strip() else "assistant returned an empty message with no tool calls"
            )
            return AgentResult(
                exit_status=0,
                stdout=stdout,
                stderr=stderr,
                timed_out=False,
                agent_claim=lines[-1] if lines else None,
                **_rate(),
            )

        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
        for call in tool_calls:
            result_text = _dispatch_tool_call(
                call, cwd=cwd, capabilities=capabilities, policy_config=policy_config
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(call.get("id", "")),
                    "content": result_text,
                }
            )

    return AgentResult(
        exit_status=1,
        stdout="\n".join(transcript),
        stderr=f"tool-call step cap ({MAX_TOOL_STEPS}) exceeded without a final answer",
        timed_out=False,
        agent_claim=None,
        **_rate(),
    )
