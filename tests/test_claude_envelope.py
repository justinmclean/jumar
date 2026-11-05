# SPDX-License-Identifier: Apache-2.0
"""Tests for the claude CLI's ``--output-format json`` envelope.

What is exercised
-----------------
- The claude argv asks for the JSON output format.
- run_agent() unwraps the envelope: stdout is the reply text, the claim is its
  last line, and the token counts come from ``usage``.
- With ``--json-schema`` the reply text is the structured object as JSON, so
  the judge's whole-reply parse still works.
- A step-cap subtype is reported as Termination.step_cap.
- An error envelope keeps the CLI's error text as stdout for the outage scan.
- Anything that is not an envelope is passed through unchanged with no counts.
- Non-claude harnesses are untouched.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

from jumar.config import Capability
from jumar.harness import (
    ClaudeEnvelope,
    HarnessInfo,
    build_argv,
    classify_termination,
    parse_claude_envelope,
    run_agent,
)
from jumar.models import Termination

_CAPS = frozenset({Capability.read_fs})


def _envelope(**overrides: object) -> dict:
    base: dict = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": 1,
        "session_id": "6d80938d-3d43-4ff9-9412-48aadbe06e1a",
        "total_cost_usd": 0.0035,
        "result": "first line\nok",
        "usage": {
            "input_tokens": 2,
            "cache_creation_input_tokens": 16398,
            "cache_read_input_tokens": 23357,
            "output_tokens": 4,
        },
    }
    base.update(overrides)
    return base


def _fake_claude(tmp_path: Path, stdout: str, exit_status: int = 0) -> str:
    """A stand-in binary that prints *stdout* and exits."""
    script = (
        "#!/usr/bin/env python3\nimport sys\nsys.stdin.read()\n"
        f"sys.stdout.write({stdout!r})\nsys.exit({exit_status})\n"
    )
    fake = tmp_path / "fake_claude"
    fake.write_text(script)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return str(fake)


def _info(binary: str, harness: str = "claude") -> HarnessInfo:
    return HarnessInfo(agent=harness, model="", harness=harness, invoked_as=binary)


# ---------------------------------------------------------------------------
# argv
# ---------------------------------------------------------------------------


def test_claude_argv_requests_json_output() -> None:
    argv = build_argv(
        agent_bin="claude", harness_name="claude", model=None, capabilities=_CAPS, prompt=""
    )
    i = argv.index("--output-format")
    assert argv[i + 1] == "json"


def test_other_harnesses_do_not_get_the_flag() -> None:
    for name in ("codex", "cursor", "gemini", "opencode", "kiro"):
        argv = build_argv(
            agent_bin=name, harness_name=name, model=None, capabilities=_CAPS, prompt="p"
        )
        assert "--output-format" not in argv, name


# ---------------------------------------------------------------------------
# parse_claude_envelope
# ---------------------------------------------------------------------------


def test_parse_reads_reply_and_token_counts() -> None:
    env = parse_claude_envelope(json.dumps(_envelope()))
    assert isinstance(env, ClaudeEnvelope)
    assert env.result == "first line\nok"
    assert env.prompt_tokens == 2 + 16398 + 23357
    assert env.completion_tokens == 4
    assert env.is_error is False
    assert env.subtype == "success"


def test_parse_falls_back_to_structured_output_when_result_missing() -> None:
    env = parse_claude_envelope(
        json.dumps(_envelope(result=None, structured_output={"verdict": "pass"}))
    )
    assert env is not None
    assert json.loads(env.result) == {"verdict": "pass"}


def test_parse_without_usage_reports_no_counts() -> None:
    env = parse_claude_envelope(json.dumps(_envelope(usage={}, subtype=7)))
    assert env is not None
    assert env.prompt_tokens is None
    assert env.completion_tokens is None
    assert env.subtype is None
    env = parse_claude_envelope(json.dumps(_envelope(usage=None, result=None)))
    assert env is not None and env.result == ""


def test_parse_rejects_anything_that_is_not_an_envelope() -> None:
    assert parse_claude_envelope("ok") is None
    assert parse_claude_envelope("") is None
    assert parse_claude_envelope("{not json") is None
    assert parse_claude_envelope(json.dumps({"verdict": "pass"})) is None
    assert parse_claude_envelope(json.dumps([1, 2])) is None


# ---------------------------------------------------------------------------
# run_agent
# ---------------------------------------------------------------------------


def test_run_agent_unwraps_the_envelope(tmp_path: Path) -> None:
    raw = json.dumps(_envelope())
    result = run_agent(
        "task",
        cwd=tmp_path,
        capabilities=_CAPS,
        timeout_s=10,
        harness=_info(_fake_claude(tmp_path, raw)),
    )
    assert result.exit_status == 0
    assert result.stdout == "first line\nok"
    assert result.agent_claim == "ok"
    assert result.prompt_tokens == 39757
    assert result.completion_tokens == 4
    assert result.envelope == raw
    assert result.termination is None
    assert classify_termination(result, None) is Termination.clean


def test_run_agent_schema_reply_is_the_json_text(tmp_path: Path) -> None:
    verdict = {"verdict": "pass", "reason": "fine", "artefacts_shown": ["a"]}
    raw = json.dumps(_envelope(result=json.dumps(verdict), structured_output=verdict))
    result = run_agent(
        "task",
        cwd=tmp_path,
        capabilities=_CAPS,
        timeout_s=10,
        harness=_info(_fake_claude(tmp_path, raw)),
        response_schema={"type": "object"},
    )
    assert json.loads(result.stdout) == verdict


def test_run_agent_step_cap_subtype_is_a_step_cap(tmp_path: Path) -> None:
    raw = json.dumps(_envelope(subtype="error_max_turns", is_error=True, result=""))
    result = run_agent(
        "task",
        cwd=tmp_path,
        capabilities=_CAPS,
        timeout_s=10,
        harness=_info(_fake_claude(tmp_path, raw, exit_status=1)),
    )
    assert result.termination is Termination.step_cap
    assert classify_termination(result, None) is Termination.step_cap


def test_run_agent_error_envelope_keeps_error_text_for_the_outage_scan(tmp_path: Path) -> None:
    raw = json.dumps(_envelope(is_error=True, result="You've hit your usage limit."))
    result = run_agent(
        "task",
        cwd=tmp_path,
        capabilities=_CAPS,
        timeout_s=10,
        harness=_info(_fake_claude(tmp_path, raw, exit_status=1)),
    )
    assert "usage limit" in result.stdout
    assert result.agent_claim == "You've hit your usage limit."


def test_run_agent_passes_non_envelope_output_through(tmp_path: Path) -> None:
    result = run_agent(
        "task",
        cwd=tmp_path,
        capabilities=_CAPS,
        timeout_s=10,
        harness=_info(_fake_claude(tmp_path, "plain text\nlast line\n")),
    )
    assert result.stdout == "plain text\nlast line\n"
    assert result.agent_claim == "last line"
    assert result.prompt_tokens is None
    assert result.envelope is None


def test_run_agent_leaves_other_harnesses_alone(tmp_path: Path) -> None:
    raw = json.dumps(_envelope())
    result = run_agent(
        "task",
        cwd=tmp_path,
        capabilities=_CAPS,
        timeout_s=10,
        harness=_info(_fake_claude(tmp_path, raw), harness="cursor"),
    )
    assert result.stdout == raw
    assert result.prompt_tokens is None
    assert result.envelope is None
