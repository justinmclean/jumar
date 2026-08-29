# SPDX-License-Identifier: Apache-2.0
"""Tests for openai_agent.py — the in-process OpenAI-compatible harness (L1).

All tests run against a stub HTTP server on 127.0.0.1 (stdlib http.server) —
no live network access and no third-party dependency. Covers:

- The tool-calling loop terminates on a final (non-tool-call) message.
- The step cap trips rather than looping forever.
- A denied argv in the run_command tool is refused and spawns no process.
- allow_tools=False (decompose) sends no "tools" key in the request.
- The deadline is enforced across the whole loop, not per request.
- Capability gating and path-containment on read_file/write_file.
- Unreachable endpoint / missing base_url fail closed, never raise.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
import urllib.error
from collections.abc import Callable, Iterator
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

import jumar.openai_agent as openai_agent
from jumar.harness import IN_PROCESS_HARNESSES
from jumar.models import Capability, HarnessInfo
from jumar.openai_agent import run_openai_agent

_ALL_CAPS: frozenset[Capability] = frozenset(
    {Capability.read_fs, Capability.write_fs, Capability.run_commands}
)


def _harness(
    base_url: str | None,
    *,
    model: str = "qwen2.5-coder-32b",
    api_key_env: str | None = None,
    commands_allow: tuple[str, ...] = ("python3",),
    commands_deny: tuple[str, ...] = ("ssh", "mail"),
) -> HarnessInfo:
    return HarnessInfo(
        agent="openai",
        model=model,
        harness="openai",
        invoked_as="openai",
        base_url=base_url,
        api_key_env=api_key_env,
        commands_allow=commands_allow,
        commands_deny=commands_deny,
    )


# ---------------------------------------------------------------------------
# Stub chat-completions server
# ---------------------------------------------------------------------------


class _ScriptedHandler(BaseHTTPRequestHandler):
    """Replays canned /chat/completions responses; records every request body."""

    responses: list[dict[str, Any]] = []
    received: list[dict[str, Any]] = []
    headers_received: list[dict[str, str]] = []
    delay_s: float = 0.0

    def do_POST(self) -> None:  # noqa: N802 - stdlib method name
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        type(self).received.append(json.loads(raw))
        type(self).headers_received.append(dict(self.headers.items()))
        if type(self).delay_s:
            time.sleep(type(self).delay_s)
        idx = len(type(self).received) - 1
        table = type(self).responses
        resp = table[idx] if idx < len(table) else table[-1]
        body = json.dumps(resp).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # silence stdlib's access log
        pass


@pytest.fixture
def chat_server() -> Iterator[Callable[..., tuple[str, type[_ScriptedHandler]]]]:
    """Build a stub server; returns (base_url, handler_class) for inspection."""
    servers: list[HTTPServer] = []

    def _make(
        responses: list[dict[str, Any]], *, delay_s: float = 0.0
    ) -> tuple[str, type[_ScriptedHandler]]:
        handler = type(
            "_Handler",
            (_ScriptedHandler,),
            {
                "responses": responses,
                "received": [],
                "headers_received": [],
                "delay_s": delay_s,
            },
        )
        server = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        host, port = server.server_address[:2]
        return f"http://{host}:{port}/v1", handler

    yield _make
    for server in servers:
        server.shutdown()
        server.server_close()


def _message(content: str = "", tool_calls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg}]}


def _tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


# ---------------------------------------------------------------------------
# Final-answer / loop termination
# ---------------------------------------------------------------------------


def test_final_answer_terminates_the_loop(tmp_path: Path, chat_server: Any) -> None:
    base_url, handler = chat_server([_message("All done.")])
    result = run_openai_agent(
        "do the thing",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=30,
        harness=_harness(base_url),
    )
    assert result.exit_status == 0
    assert result.timed_out is False
    assert "All done." in result.stdout
    assert result.agent_claim == "All done."
    assert len(handler.received) == 1


def test_tool_call_then_final_answer(tmp_path: Path, chat_server: Any) -> None:
    (tmp_path / "foo.txt").write_text("hello world")
    base_url, handler = chat_server(
        [
            _message("", [_tool_call("1", "read_file", {"path": "foo.txt"})]),
            _message("read it, done."),
        ]
    )
    result = run_openai_agent(
        "read foo.txt",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=30,
        harness=_harness(base_url),
    )
    assert result.exit_status == 0
    assert result.agent_claim == "read it, done."
    assert len(handler.received) == 2
    # The tool result must have been fed back as a "tool" message.
    second_request_messages = handler.received[1]["messages"]
    tool_messages = [m for m in second_request_messages if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    tool_payload = json.loads(tool_messages[0]["content"])
    assert tool_payload["content"] == "hello world"


def test_empty_content_no_tool_calls_flagged_in_stderr(tmp_path: Path, chat_server: Any) -> None:
    """A model that answers with empty content and no tool calls is not a silent success.

    Regression coverage for W9: a model that parks its answer entirely in a
    field this schema does not surface (e.g. `reasoning_content`) returns
    `content=""` and no `tool_calls` — exit_status=0 with empty stdout,
    indistinguishable from a real success unless stderr names the condition.
    """
    base_url, handler = chat_server([_message("")])
    result = run_openai_agent(
        "do the thing",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=30,
        harness=_harness(base_url),
    )
    assert result.exit_status == 0  # the HTTP call itself succeeded
    assert result.stdout == ""
    assert result.agent_claim is None
    assert "empty message" in result.stderr
    assert len(handler.received) == 1


def test_nonempty_content_no_stderr_note(tmp_path: Path, chat_server: Any) -> None:
    """A real final answer must not carry the empty-message stderr note."""
    base_url, handler = chat_server([_message("All done.")])
    result = run_openai_agent(
        "do the thing",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=30,
        harness=_harness(base_url),
    )
    assert result.exit_status == 0
    assert result.stderr == ""


# ---------------------------------------------------------------------------
# Step cap
# ---------------------------------------------------------------------------


def test_step_cap_trips_instead_of_looping_forever(tmp_path: Path, chat_server: Any) -> None:
    base_url, handler = chat_server(
        [_message("", [_tool_call("1", "read_file", {"path": "missing.txt"})])]
    )
    result = run_openai_agent(
        "loop forever",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=30,
        harness=_harness(base_url),
    )
    assert result.exit_status == 1
    assert result.timed_out is False
    assert "step cap" in (result.stderr or "")
    from jumar.openai_agent import MAX_TOOL_STEPS

    assert len(handler.received) == MAX_TOOL_STEPS


# ---------------------------------------------------------------------------
# Denied argv — capability_denied, no process spawned
# ---------------------------------------------------------------------------


def test_denied_argv_yields_refusal_and_spawns_no_process(
    tmp_path: Path, chat_server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _explode(*_a: object, **_kw: object) -> None:
        raise AssertionError("subprocess.run must not be called for a denied argv")

    monkeypatch.setattr(subprocess, "run", _explode)

    base_url, handler = chat_server(
        [
            _message("", [_tool_call("1", "run_command", {"argv": ["ssh", "evil.example"]})]),
            _message("refused, stopping."),
        ]
    )
    result = run_openai_agent(
        "exfiltrate something",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=30,
        harness=_harness(base_url, commands_deny=("ssh",)),
    )
    assert result.exit_status == 0  # the loop itself completed fine
    second_request_messages = handler.received[1]["messages"]
    tool_messages = [m for m in second_request_messages if m.get("role") == "tool"]
    payload = json.loads(tool_messages[0]["content"])
    assert payload["error"] == "capability_denied"


def test_run_command_without_capability_is_refused(tmp_path: Path, chat_server: Any) -> None:
    base_url, handler = chat_server(
        [
            _message("", [_tool_call("1", "run_command", {"argv": ["python3", "-V"]})]),
            _message("done."),
        ]
    )
    no_run_caps = frozenset({Capability.read_fs, Capability.write_fs})
    run_openai_agent(
        "run something",
        cwd=tmp_path,
        capabilities=no_run_caps,
        timeout_s=30,
        harness=_harness(base_url),
    )
    tool_messages = [m for m in handler.received[1]["messages"] if m.get("role") == "tool"]
    payload = json.loads(tool_messages[0]["content"])
    assert payload["error"] == "capability_denied"
    assert "run_commands" in payload["reason"]


# ---------------------------------------------------------------------------
# allow_tools=False — decompose's contract
# ---------------------------------------------------------------------------


def test_allow_tools_false_sends_no_tools_key(tmp_path: Path, chat_server: Any) -> None:
    base_url, handler = chat_server([_message('{"subtasks": []}')])
    result = run_openai_agent(
        "produce a plan",
        cwd=tmp_path,
        capabilities=frozenset(),
        timeout_s=30,
        harness=_harness(base_url),
        allow_tools=False,
    )
    assert result.exit_status == 0
    assert "tools" not in handler.received[0]
    assert result.stdout == '{"subtasks": []}'


def test_allow_tools_true_sends_tools_key(tmp_path: Path, chat_server: Any) -> None:
    base_url, handler = chat_server([_message("ok")])
    run_openai_agent(
        "do it",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=30,
        harness=_harness(base_url),
        allow_tools=True,
    )
    assert "tools" in handler.received[0]
    names = {t["function"]["name"] for t in handler.received[0]["tools"]}
    assert names == {"read_file", "write_file", "run_command"}


# ---------------------------------------------------------------------------
# Deadline covers the whole loop, not one request
# ---------------------------------------------------------------------------


def test_deadline_covers_the_whole_loop(tmp_path: Path, chat_server: Any) -> None:
    """Two slow round trips must exhaust a short overall deadline — the
    per-request timeout floor must not let step 3 slip through unbounded."""
    base_url, handler = chat_server(
        [_message("", [_tool_call("1", "read_file", {"path": "missing.txt"})])],
        delay_s=0.6,
    )
    result = run_openai_agent(
        "keep going",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=1,
        harness=_harness(base_url),
    )
    assert result.timed_out is True
    assert result.exit_status == -1
    assert "deadline" in (result.stderr or "")
    # Two real round trips happened before the deadline tripped — this is
    # cumulative enforcement across requests, not a single per-request cap.
    assert len(handler.received) == 2


def test_request_still_in_flight_at_deadline_is_a_timeout_not_a_transport_error(
    tmp_path: Path, chat_server: Any
) -> None:
    """A slow model that is still generating when the budget runs out is a
    timeout, not an endpoint outage.

    Regression: every request is issued with the whole remaining budget, so
    the FIRST request can outlive the deadline and raise before the loop's own
    deadline check is ever reached. That was reported as
    `request to <url> failed: timed out` with `timed_out=False` — which reads
    in the journal as "the server fell over" for a server that was working
    fine, and leaves everything keyed on `timed_out` unfired.
    """
    base_url, handler = chat_server(
        [_message("done", [])],
        delay_s=2.0,
    )
    result = run_openai_agent(
        "slow generation",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=1,
        harness=_harness(base_url),
    )
    assert result.timed_out is True
    assert result.exit_status == -1
    assert "deadline" in (result.stderr or "")
    # The endpoint was reachable throughout — one request was made and the
    # server was mid-response when the clock ran out.
    assert len(handler.received) == 1


# ---------------------------------------------------------------------------
# HTTP error responses carry the server's own explanation
# ---------------------------------------------------------------------------


class _ErrorHandler(BaseHTTPRequestHandler):
    """Serves one HTTP error status with a JSON body, like a local model server."""

    status: int = 400
    body: bytes = b""

    def do_POST(self) -> None:  # noqa: N802 - stdlib method name
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args: object) -> None:  # silence stdlib's access log
        pass


@pytest.fixture
def error_server() -> Iterator[Callable[[int, bytes], str]]:
    servers: list[HTTPServer] = []

    def _make(status: int, body: bytes) -> str:
        handler = type("_Handler", (_ErrorHandler,), {"status": status, "body": body})
        server = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        host, port = server.server_address[:2]
        return f"http://{host}:{port}/v1"

    yield _make
    for server in servers:
        server.shutdown()
        server.server_close()


def test_http_error_body_is_surfaced(tmp_path: Path, error_server: Any) -> None:
    """A 400 must carry the server's explanation, not just urllib's status line.

    Observed: LM Studio answered a too-large prompt with a body naming the
    context-length overflow, and the harness reported only "HTTP Error 400:
    Bad Request" — three round trips of debugging to recover a message the
    server had already sent.
    """
    body = json.dumps(
        {
            "error": "The number of tokens to keep from the initial prompt is greater "
            "than the context length."
        }
    ).encode()
    base_url = error_server(400, body)

    result = run_openai_agent(
        "do the thing",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=10,
        harness=_harness(base_url),
    )

    assert result.exit_status == -1
    assert result.timed_out is False
    assert "context length" in (result.stderr or "")
    assert "400" in (result.stderr or "")


def test_http_error_body_is_capped(tmp_path: Path, error_server: Any) -> None:
    """The body is server-controlled, so it must not land in the journal whole."""
    base_url = error_server(500, b"x" * 50_000)

    result = run_openai_agent(
        "do the thing",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=10,
        harness=_harness(base_url),
    )

    assert result.exit_status == -1
    assert len(result.stderr or "") < 2_000


# ---------------------------------------------------------------------------
# Unreachable endpoint / misconfiguration — fail closed, never raise
# ---------------------------------------------------------------------------


def test_missing_base_url_fails_without_a_request(tmp_path: Path) -> None:
    result = run_openai_agent(
        "do the thing",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=30,
        harness=_harness(None),
    )
    assert result.exit_status == -1
    assert result.timed_out is False
    assert "base_url" in (result.stderr or "")


def test_unreachable_endpoint_fails_closed(tmp_path: Path) -> None:
    result = run_openai_agent(
        "do the thing",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=5,
        harness=_harness("http://127.0.0.1:1/v1"),
    )
    assert result.exit_status == -1
    assert result.timed_out is False
    assert result.agent_claim is None


@pytest.mark.parametrize(
    ("error", "timed_out"),
    [
        pytest.param(TimeoutError("timed out"), True, id="bare-timeout"),
        pytest.param(
            urllib.error.URLError(TimeoutError("timed out")),
            True,
            id="wrapped-timeout",
        ),
        pytest.param(
            urllib.error.HTTPError(
                "http://127.0.0.1:1234/v1/chat/completions",
                500,
                "boom",
                hdrs=None,
                fp=None,
            ),
            False,
            id="http-error",
        ),
        pytest.param(OSError("boom"), False, id="non-timeout-oserror"),
    ],
)
def test_request_errors_set_timeout_flag_correctly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    *,
    timed_out: bool,
) -> None:
    def _fail(*_a: object, **_kw: object) -> dict[str, object]:
        raise error

    monkeypatch.setattr(openai_agent, "_post_chat_completions", _fail)

    result = run_openai_agent(
        "do the thing",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=5,
        harness=_harness("http://127.0.0.1:1234/v1"),
    )
    assert result.exit_status == -1
    assert result.timed_out is timed_out


# ---------------------------------------------------------------------------
# Path containment on read_file / write_file
# ---------------------------------------------------------------------------


def test_write_file_outside_cwd_is_refused(tmp_path: Path, chat_server: Any) -> None:
    base_url, handler = chat_server(
        [
            _message(
                "",
                [_tool_call("1", "write_file", {"path": "../escaped.txt", "content": "x"})],
            ),
            _message("done."),
        ]
    )
    run_openai_agent(
        "write outside",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=30,
        harness=_harness(base_url),
    )
    assert not (tmp_path.parent / "escaped.txt").exists()
    tool_messages = [m for m in handler.received[1]["messages"] if m.get("role") == "tool"]
    payload = json.loads(tool_messages[0]["content"])
    assert payload["error"] == "path_outside_cwd"


def test_write_file_within_cwd_succeeds(tmp_path: Path, chat_server: Any) -> None:
    base_url, handler = chat_server(
        [
            _message("", [_tool_call("1", "write_file", {"path": "out.txt", "content": "hi"})]),
            _message("done."),
        ]
    )
    run_openai_agent(
        "write a file",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=30,
        harness=_harness(base_url),
    )
    assert (tmp_path / "out.txt").read_text() == "hi"


# ---------------------------------------------------------------------------
# Authorization header
# ---------------------------------------------------------------------------


def test_api_key_env_sets_authorization_header(
    tmp_path: Path, chat_server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LMSTUDIO_KEY", "s3cr3t")
    base_url, handler = chat_server([_message("ok")])
    run_openai_agent(
        "do it",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=30,
        harness=_harness(base_url, api_key_env="LMSTUDIO_KEY"),
    )
    assert handler.headers_received[0].get("Authorization") == "Bearer s3cr3t"


def test_no_api_key_env_sends_no_authorization_header(tmp_path: Path, chat_server: Any) -> None:
    base_url, handler = chat_server([_message("ok")])
    run_openai_agent(
        "do it",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=30,
        harness=_harness(base_url, api_key_env="JUMAR_TEST_UNSET_KEY_XYZ"),
    )
    assert "Authorization" not in handler.headers_received[0]


# ---------------------------------------------------------------------------
# Registration sanity
# ---------------------------------------------------------------------------


def test_openai_is_registered_as_in_process() -> None:
    assert "openai" in IN_PROCESS_HARNESSES


# ---------------------------------------------------------------------------
# Tool refusal paths — capability gating, malformed calls, unknown tools
# ---------------------------------------------------------------------------


def _tool_result(handler: Any, request_index: int = 1) -> dict[str, Any]:
    """Return the parsed JSON payload of the (first) tool-result message in
    request *request_index*'s outgoing messages."""
    messages = handler.received[request_index]["messages"]
    tool_messages = [m for m in messages if m.get("role") == "tool"]
    result: dict[str, Any] = json.loads(tool_messages[0]["content"])
    return result


def test_read_file_without_capability_is_refused(tmp_path: Path, chat_server: Any) -> None:
    (tmp_path / "foo.txt").write_text("secret")
    base_url, handler = chat_server(
        [
            _message("", [_tool_call("1", "read_file", {"path": "foo.txt"})]),
            _message("done."),
        ]
    )
    no_read_caps = frozenset({Capability.write_fs, Capability.run_commands})
    run_openai_agent(
        "read it",
        cwd=tmp_path,
        capabilities=no_read_caps,
        timeout_s=30,
        harness=_harness(base_url),
    )
    assert _tool_result(handler) == {"error": "capability_denied", "reason": "read_fs not granted"}


def test_read_file_outside_cwd_is_refused(tmp_path: Path, chat_server: Any) -> None:
    base_url, handler = chat_server(
        [
            _message("", [_tool_call("1", "read_file", {"path": "../outside.txt"})]),
            _message("done."),
        ]
    )
    run_openai_agent(
        "read outside",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=30,
        harness=_harness(base_url),
    )
    assert _tool_result(handler)["error"] == "path_outside_cwd"


def test_write_file_without_capability_is_refused(tmp_path: Path, chat_server: Any) -> None:
    base_url, handler = chat_server(
        [
            _message("", [_tool_call("1", "write_file", {"path": "out.txt", "content": "x"})]),
            _message("done."),
        ]
    )
    no_write_caps = frozenset({Capability.read_fs, Capability.run_commands})
    run_openai_agent(
        "write it",
        cwd=tmp_path,
        capabilities=no_write_caps,
        timeout_s=30,
        harness=_harness(base_url),
    )
    assert _tool_result(handler) == {
        "error": "capability_denied",
        "reason": "write_fs not granted",
    }
    assert not (tmp_path / "out.txt").exists()


def test_run_command_invalid_argv_is_refused(tmp_path: Path, chat_server: Any) -> None:
    base_url, handler = chat_server(
        [
            _message("", [_tool_call("1", "run_command", {"argv": "not-a-list"})]),
            _message("done."),
        ]
    )
    run_openai_agent(
        "run something malformed",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=30,
        harness=_harness(base_url),
    )
    assert _tool_result(handler)["error"] == "invalid_argv"


def test_run_command_success_executes_the_process(tmp_path: Path, chat_server: Any) -> None:
    base_url, handler = chat_server(
        [
            _message("", [_tool_call("1", "run_command", {"argv": ["python3", "-c", "print(1)"]})]),
            _message("ran it."),
        ]
    )
    run_openai_agent(
        "run python3",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=30,
        harness=_harness(base_url, commands_allow=("python3",)),
    )
    result = _tool_result(handler)
    assert result["exit_status"] == 0
    assert result["stdout"].strip() == "1"


def test_unknown_tool_name_is_refused(tmp_path: Path, chat_server: Any) -> None:
    base_url, handler = chat_server(
        [
            _message("", [_tool_call("1", "delete_everything", {})]),
            _message("done."),
        ]
    )
    run_openai_agent(
        "do something unsupported",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=30,
        harness=_harness(base_url),
    )
    assert _tool_result(handler) == {"error": "unknown_tool", "name": "delete_everything"}


def test_malformed_tool_arguments_json_is_refused(tmp_path: Path, chat_server: Any) -> None:
    broken_call = {
        "id": "1",
        "type": "function",
        "function": {"name": "read_file", "arguments": "{not valid json"},
    }
    base_url, handler = chat_server([_message("", [broken_call]), _message("done.")])
    run_openai_agent(
        "read with a broken tool call",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=30,
        harness=_harness(base_url),
    )
    assert _tool_result(handler)["error"] == "invalid_arguments"


def test_non_object_tool_arguments_is_refused(tmp_path: Path, chat_server: Any) -> None:
    array_call = {
        "id": "1",
        "type": "function",
        "function": {"name": "read_file", "arguments": "[1, 2, 3]"},
    }
    base_url, handler = chat_server([_message("", [array_call]), _message("done.")])
    run_openai_agent(
        "read with array arguments",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=30,
        harness=_harness(base_url),
    )
    assert _tool_result(handler)["error"] == "invalid_arguments"


# ---------------------------------------------------------------------------
# Response-shape failures
# ---------------------------------------------------------------------------


def test_no_choices_in_response_fails_closed(tmp_path: Path, chat_server: Any) -> None:
    base_url, handler = chat_server([{"choices": []}])
    result = run_openai_agent(
        "do the thing",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=30,
        harness=_harness(base_url),
    )
    assert result.exit_status == -1
    assert "no choices" in (result.stderr or "")


def test_non_object_response_fails_closed(tmp_path: Path, chat_server: Any) -> None:
    base_url, handler = chat_server([["not", "an", "object"]])
    result = run_openai_agent(
        "do the thing",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=30,
        harness=_harness(base_url),
    )
    assert result.exit_status == -1
    assert result.timed_out is False


# ---------------------------------------------------------------------------
# Usage accounting
# ---------------------------------------------------------------------------


def _with_usage(
    response: dict[str, Any], *, prompt_tokens: int, completion_tokens: int
) -> dict[str, Any]:
    return {
        **response,
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


def test_usage_is_summed_across_the_tool_loop(tmp_path: Path, chat_server: Any) -> None:
    """Both token counts accumulate per request, not per conversation.

    The prompt grows each turn and is re-sent in full, so summing prompt_tokens
    double-counts the prefix by design: a metered endpoint bills per call, and
    that repeated prefix is real spend.
    """
    (tmp_path / "foo.txt").write_text("hello world")
    base_url, handler = chat_server(
        [
            _with_usage(
                _message("", [_tool_call("1", "read_file", {"path": "foo.txt"})]),
                prompt_tokens=1200,
                completion_tokens=50,
            ),
            _with_usage(_message("done."), prompt_tokens=1800, completion_tokens=30),
        ]
    )
    result = run_openai_agent(
        "read foo.txt",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=30,
        harness=_harness(base_url),
    )
    assert len(handler.received) == 2
    assert result.prompt_tokens == 3000
    assert result.completion_tokens == 80


def test_usage_absent_from_response_leaves_counts_at_zero(tmp_path: Path, chat_server: Any) -> None:
    """A server that reports no usage must not crash the loop or invent numbers."""
    base_url, _handler = chat_server([_message("All done.")])
    result = run_openai_agent(
        "do the thing",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=30,
        harness=_harness(base_url),
    )
    assert result.exit_status == 0
    assert result.prompt_tokens == 0
    assert result.completion_tokens == 0


def test_malformed_usage_values_are_ignored(tmp_path: Path, chat_server: Any) -> None:
    """A non-numeric token count is skipped rather than aborting the attempt."""
    base_url, _handler = chat_server(
        [{**_message("All done."), "usage": {"prompt_tokens": "lots", "completion_tokens": 12}}]
    )
    result = run_openai_agent(
        "do the thing",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=30,
        harness=_harness(base_url),
    )
    assert result.exit_status == 0
    assert result.prompt_tokens == 0
    assert result.completion_tokens == 12


# ---------------------------------------------------------------------------
# reasoning_effort
# ---------------------------------------------------------------------------


def test_reasoning_effort_is_sent_when_configured(tmp_path: Path, chat_server: Any) -> None:
    base_url, handler = chat_server([_message("All done.")])
    harness = replace(_harness(base_url), reasoning_effort="low")
    result = run_openai_agent(
        "do the thing",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=30,
        harness=harness,
    )
    assert result.exit_status == 0
    assert handler.received[0]["reasoning_effort"] == "low"


def test_reasoning_effort_is_omitted_when_unset(tmp_path: Path, chat_server: Any) -> None:
    """An endpoint that rejects unknown keys must not see the key by default."""
    base_url, handler = chat_server([_message("All done.")])
    result = run_openai_agent(
        "do the thing",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=30,
        harness=_harness(base_url),
    )
    assert result.exit_status == 0
    assert "reasoning_effort" not in handler.received[0]


def test_reasoning_effort_is_sent_on_every_turn_of_the_loop(
    tmp_path: Path, chat_server: Any
) -> None:
    (tmp_path / "foo.txt").write_text("hello world")
    base_url, handler = chat_server(
        [
            _message("", [_tool_call("1", "read_file", {"path": "foo.txt"})]),
            _message("done."),
        ]
    )
    result = run_openai_agent(
        "read foo.txt",
        cwd=tmp_path,
        capabilities=_ALL_CAPS,
        timeout_s=30,
        harness=replace(_harness(base_url), reasoning_effort="medium"),
    )
    assert result.exit_status == 0
    assert len(handler.received) == 2
    assert all(r["reasoning_effort"] == "medium" for r in handler.received)
