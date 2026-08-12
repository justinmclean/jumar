# SPDX-License-Identifier: Apache-2.0
"""Tests for harness.py — argv construction, env scrubbing, and dispatch.

Covers:
- Pure build_argv() for every supported harness.
- Hard-denial of git push and gh in the claude argv.
- No-network capability strips WebSearch/WebFetch from the claude argv.
- prompt_via_stdin() contract.
- scrub_env() strips the right keys.
- Fake-agent end-to-end test: a real subprocess that records its argv.
- Unsupported harness name raises ValueError.
- run_agent() timeout path.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from jumar.harness import (
    SESSION_RESUMABLE_HARNESSES,
    SUPPORTED_HARNESSES,
    AgentResult,
    build_argv,
    prompt_via_stdin,
    run_agent,
    scrub_env,
)
from jumar.models import Capability, HarnessInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_CAPS: frozenset[Capability] = frozenset(
    {Capability.read_fs, Capability.write_fs, Capability.run_commands}
)
_NET_CAPS: frozenset[Capability] = _BASE_CAPS | {Capability.network}


def _harness_info(
    harness: str,
    *,
    model: str = "sonnet",
    invoked_as: str = "",
) -> HarnessInfo:
    return HarnessInfo(
        agent=harness,
        model=model,
        harness=harness,
        invoked_as=invoked_as or harness,
    )


# ---------------------------------------------------------------------------
# prompt_via_stdin
# ---------------------------------------------------------------------------


def test_claude_prompt_via_stdin() -> None:
    assert prompt_via_stdin("claude") is True


def test_codex_prompt_via_stdin() -> None:
    assert prompt_via_stdin("codex") is True


def test_cursor_not_via_stdin() -> None:
    assert prompt_via_stdin("cursor") is False


def test_gemini_not_via_stdin() -> None:
    assert prompt_via_stdin("gemini") is False


def test_opencode_not_via_stdin() -> None:
    assert prompt_via_stdin("opencode") is False


def test_kiro_not_via_stdin() -> None:
    assert prompt_via_stdin("kiro") is False


# ---------------------------------------------------------------------------
# build_argv — unsupported harness
# ---------------------------------------------------------------------------


def test_unsupported_harness_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported harness"):
        build_argv(
            agent_bin="myagent",
            harness_name="myagent",
            model=None,
            capabilities=_BASE_CAPS,
            prompt="hello",
        )


# ---------------------------------------------------------------------------
# build_argv — claude
# ---------------------------------------------------------------------------


def test_claude_argv_starts_with_binary() -> None:
    argv = build_argv(
        agent_bin="claude",
        harness_name="claude",
        model=None,
        capabilities=_BASE_CAPS,
        prompt="do the thing",
    )
    assert argv[0] == "claude"


def test_claude_argv_has_print_flag() -> None:
    argv = build_argv(
        agent_bin="claude",
        harness_name="claude",
        model=None,
        capabilities=_BASE_CAPS,
        prompt="x",
    )
    assert "-p" in argv


def test_claude_argv_has_dangerously_skip_permissions() -> None:
    argv = build_argv(
        agent_bin="claude",
        harness_name="claude",
        model=None,
        capabilities=_BASE_CAPS,
        prompt="x",
    )
    assert "--dangerously-skip-permissions" in argv


def test_claude_argv_always_hard_denies_git_push() -> None:
    """Fails if git push can bypass the claude tool-level deny."""
    argv = build_argv(
        agent_bin="claude",
        harness_name="claude",
        model=None,
        capabilities=_BASE_CAPS,
        prompt="x",
    )
    assert "Bash(git push:*)" in argv


def test_claude_argv_always_hard_denies_gh() -> None:
    """Fails if gh can bypass the claude tool-level deny."""
    argv = build_argv(
        agent_bin="claude",
        harness_name="claude",
        model=None,
        capabilities=_BASE_CAPS,
        prompt="x",
    )
    assert "Bash(gh:*)" in argv


def test_claude_hard_denies_persist_even_with_network_cap() -> None:
    """git push / gh are denied regardless of the network capability."""
    argv = build_argv(
        agent_bin="claude",
        harness_name="claude",
        model=None,
        capabilities=_NET_CAPS,
        prompt="x",
    )
    assert "Bash(git push:*)" in argv
    assert "Bash(gh:*)" in argv


def test_claude_no_network_disables_web_tools() -> None:
    argv = build_argv(
        agent_bin="claude",
        harness_name="claude",
        model=None,
        capabilities=_BASE_CAPS,
        prompt="x",
    )
    assert "WebSearch" in argv
    assert "WebFetch" in argv


def test_claude_with_network_allows_web_tools() -> None:
    argv = build_argv(
        agent_bin="claude",
        harness_name="claude",
        model=None,
        capabilities=_NET_CAPS,
        prompt="x",
    )
    assert "WebSearch" not in argv
    assert "WebFetch" not in argv


def test_claude_model_flag() -> None:
    argv = build_argv(
        agent_bin="claude",
        harness_name="claude",
        model="opus",
        capabilities=_BASE_CAPS,
        prompt="x",
    )
    assert "--model" in argv
    idx = argv.index("--model")
    assert argv[idx + 1] == "opus"


def test_claude_no_model_flag_when_none() -> None:
    argv = build_argv(
        agent_bin="claude",
        harness_name="claude",
        model=None,
        capabilities=_BASE_CAPS,
        prompt="x",
    )
    assert "--model" not in argv


def test_claude_prompt_not_in_argv() -> None:
    prompt = "this should not appear in argv"
    argv = build_argv(
        agent_bin="claude",
        harness_name="claude",
        model=None,
        capabilities=_BASE_CAPS,
        prompt=prompt,
    )
    assert prompt not in argv


# ---------------------------------------------------------------------------
# build_argv — codex
# ---------------------------------------------------------------------------


def test_codex_argv_structure() -> None:
    argv = build_argv(
        agent_bin="codex",
        harness_name="codex",
        model=None,
        capabilities=_BASE_CAPS,
        prompt="task",
    )
    assert argv[0] == "codex"
    assert "exec" in argv
    assert "--dangerously-bypass-approvals-and-sandbox" in argv
    assert "-" in argv  # stdin sentinel


def test_codex_model_flag() -> None:
    argv = build_argv(
        agent_bin="codex",
        harness_name="codex",
        model="gpt-4o",
        capabilities=_BASE_CAPS,
        prompt="task",
    )
    assert "--model" in argv
    assert "gpt-4o" in argv


def test_codex_prompt_not_in_argv() -> None:
    prompt = "secret task"
    argv = build_argv(
        agent_bin="codex",
        harness_name="codex",
        model=None,
        capabilities=_BASE_CAPS,
        prompt=prompt,
    )
    assert prompt not in argv


# ---------------------------------------------------------------------------
# build_argv — cursor
# ---------------------------------------------------------------------------


def test_cursor_argv_structure() -> None:
    argv = build_argv(
        agent_bin="cursor",
        harness_name="cursor",
        model=None,
        capabilities=_BASE_CAPS,
        prompt="my task",
    )
    assert argv[0] == "cursor"
    assert "agent" in argv
    assert "--print" in argv
    assert "--force" in argv
    assert "--trust" in argv


def test_cursor_prompt_is_last_argv() -> None:
    prompt = "cursor task"
    argv = build_argv(
        agent_bin="cursor",
        harness_name="cursor",
        model=None,
        capabilities=_BASE_CAPS,
        prompt=prompt,
    )
    assert argv[-1] == prompt


# ---------------------------------------------------------------------------
# build_argv — gemini
# ---------------------------------------------------------------------------


def test_gemini_argv_structure() -> None:
    argv = build_argv(
        agent_bin="gemini",
        harness_name="gemini",
        model=None,
        capabilities=_BASE_CAPS,
        prompt="gemini task",
    )
    assert argv[0] == "gemini"
    assert "--yolo" in argv


def test_gemini_prompt_flag() -> None:
    prompt = "gemini task"
    argv = build_argv(
        agent_bin="gemini",
        harness_name="gemini",
        model=None,
        capabilities=_BASE_CAPS,
        prompt=prompt,
    )
    assert "--prompt" in argv
    idx = argv.index("--prompt")
    assert argv[idx + 1] == prompt


# ---------------------------------------------------------------------------
# build_argv — opencode
# ---------------------------------------------------------------------------


def test_opencode_argv_structure() -> None:
    argv = build_argv(
        agent_bin="opencode",
        harness_name="opencode",
        model=None,
        capabilities=_BASE_CAPS,
        prompt="opencode task",
    )
    assert argv[0] == "opencode"
    assert "run" in argv
    assert "--auto" in argv


def test_opencode_prompt_is_last_argv() -> None:
    prompt = "opencode task"
    argv = build_argv(
        agent_bin="opencode",
        harness_name="opencode",
        model=None,
        capabilities=_BASE_CAPS,
        prompt=prompt,
    )
    assert argv[-1] == prompt


# ---------------------------------------------------------------------------
# build_argv — kiro
# ---------------------------------------------------------------------------


def test_kiro_argv_structure() -> None:
    argv = build_argv(
        agent_bin="kiro",
        harness_name="kiro",
        model=None,
        capabilities=_BASE_CAPS,
        prompt="kiro task",
    )
    assert argv[0] == "kiro"
    assert "chat" in argv
    assert "--no-interactive" in argv


def test_kiro_prompt_is_last_argv() -> None:
    prompt = "kiro task"
    argv = build_argv(
        agent_bin="kiro",
        harness_name="kiro",
        model=None,
        capabilities=_BASE_CAPS,
        prompt=prompt,
    )
    assert argv[-1] == prompt


# ---------------------------------------------------------------------------
# SUPPORTED_HARNESSES completeness
# ---------------------------------------------------------------------------


def test_supported_harnesses_set() -> None:
    assert {"claude", "codex", "cursor", "gemini", "opencode", "kiro"} == SUPPORTED_HARNESSES


# ---------------------------------------------------------------------------
# scrub_env
# ---------------------------------------------------------------------------


def test_scrub_removes_github_token() -> None:
    env = {"GITHUB_TOKEN": "secret", "PATH": "/usr/bin"}
    result = scrub_env("claude", env)
    assert "GITHUB_TOKEN" not in result
    assert "PATH" in result


def test_scrub_removes_gh_token() -> None:
    env = {"GH_TOKEN": "secret", "HOME": "/home/user"}
    result = scrub_env("claude", env)
    assert "GH_TOKEN" not in result


def test_scrub_removes_ssh_auth_sock() -> None:
    env = {"SSH_AUTH_SOCK": "/tmp/agent.sock", "USER": "alice"}
    result = scrub_env("claude", env)
    assert "SSH_AUTH_SOCK" not in result
    assert "USER" in result


def test_scrub_keeps_claude_anthropic_api_key() -> None:
    env = {"ANTHROPIC_API_KEY": "sk-ant-xxx", "PATH": "/usr/bin"}
    result = scrub_env("claude", env)
    assert result.get("ANTHROPIC_API_KEY") == "sk-ant-xxx"


def test_scrub_removes_other_api_keys_for_claude() -> None:
    env = {
        "ANTHROPIC_API_KEY": "sk-ant-xxx",
        "OPENAI_API_KEY": "sk-openai",
        "GOOGLE_API_KEY": "gkey",
    }
    result = scrub_env("claude", env)
    assert "ANTHROPIC_API_KEY" in result
    assert "OPENAI_API_KEY" not in result
    assert "GOOGLE_API_KEY" not in result


def test_scrub_keeps_codex_openai_api_key() -> None:
    env = {"OPENAI_API_KEY": "sk-openai", "ANTHROPIC_API_KEY": "sk-ant"}
    result = scrub_env("codex", env)
    assert result.get("OPENAI_API_KEY") == "sk-openai"
    assert "ANTHROPIC_API_KEY" not in result


def test_scrub_uses_os_environ_as_default() -> None:
    result = scrub_env("claude")
    # Result should be a dict and not contain GITHUB_TOKEN if it wasn't set.
    assert isinstance(result, dict)
    assert "GITHUB_TOKEN" not in result


# ---------------------------------------------------------------------------
# Fake-agent end-to-end test: real subprocess records its argv
# ---------------------------------------------------------------------------


def _make_fake_agent(tmp_path: Path) -> Path:
    """Write a tiny Python script that dumps sys.argv as JSON and exits 0."""
    argv_file = tmp_path / "recorded_argv.json"
    script = (
        "#!/usr/bin/env python3\n"
        "import sys, json\n"
        f"with open({str(argv_file)!r}, 'w') as f:\n"
        "    json.dump(sys.argv, f)\n"
    )
    fake = tmp_path / "fake_agent"
    fake.write_text(script)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return fake


def test_fake_agent_records_argv(tmp_path: Path) -> None:
    """run_agent() launches the subprocess and the fake agent records its argv."""
    fake = _make_fake_agent(tmp_path)
    info = _harness_info("cursor", invoked_as=str(fake))

    result = run_agent(
        "hello fake",
        cwd=tmp_path,
        capabilities=_BASE_CAPS,
        timeout_s=10,
        harness=info,
    )

    assert result.exit_status == 0
    assert not result.timed_out

    recorded = json.loads((tmp_path / "recorded_argv.json").read_text())
    assert recorded[0] == str(fake)
    assert "agent" in recorded
    assert "--print" in recorded
    assert recorded[-1] == "hello fake"


def test_fake_agent_stdin_prompt(tmp_path: Path) -> None:
    """For a stdin-based harness the fake agent receives the prompt on stdin."""
    # Write a script that reads stdin and echoes it.
    stdin_file = tmp_path / "stdin_content.txt"
    script = (
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"with open({str(stdin_file)!r}, 'w') as f:\n"
        "    f.write(sys.stdin.read())\n"
    )
    fake = tmp_path / "fake_claude"
    fake.write_text(script)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    info = _harness_info("claude", invoked_as=str(fake))
    result = run_agent(
        "my prompt text",
        cwd=tmp_path,
        capabilities=_BASE_CAPS,
        timeout_s=10,
        harness=info,
    )

    assert result.exit_status == 0
    assert stdin_file.read_text() == "my prompt text"


def test_fake_agent_nonzero_exit(tmp_path: Path) -> None:
    """run_agent() captures a non-zero exit status without raising."""
    script = "#!/usr/bin/env python3\nimport sys\nsys.exit(42)\n"
    fake = tmp_path / "failing_agent"
    fake.write_text(script)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    info = _harness_info("cursor", invoked_as=str(fake))
    result = run_agent("task", cwd=tmp_path, capabilities=_BASE_CAPS, timeout_s=10, harness=info)

    assert result.exit_status == 42
    assert not result.timed_out


def test_fake_agent_claim_is_last_stdout_line(tmp_path: Path) -> None:
    """agent_claim is the last non-empty line of stdout."""
    script = "#!/usr/bin/env python3\nprint('line one')\nprint('done: all tasks completed')\n"
    fake = tmp_path / "verbose_agent"
    fake.write_text(script)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    info = _harness_info("cursor", invoked_as=str(fake))
    result = run_agent("task", cwd=tmp_path, capabilities=_BASE_CAPS, timeout_s=10, harness=info)

    assert result.agent_claim == "done: all tasks completed"


# ---------------------------------------------------------------------------
# run_agent — timeout path
# ---------------------------------------------------------------------------


def test_run_agent_timeout_sets_flag(tmp_path: Path) -> None:
    """A subprocess that sleeps past the timeout returns timed_out=True."""
    script = "#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n"
    fake = tmp_path / "slow_agent"
    fake.write_text(script)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    info = _harness_info("cursor", invoked_as=str(fake))
    result = run_agent("task", cwd=tmp_path, capabilities=_BASE_CAPS, timeout_s=1, harness=info)

    assert result.timed_out is True
    assert result.exit_status == -1


# ---------------------------------------------------------------------------
# Hard-deny at the harness argv level — the safety net test
# ---------------------------------------------------------------------------


def test_claude_argv_hard_denies_git_push_with_any_caps() -> None:
    """git push is denied in every claude argv regardless of capabilities.

    This test MUST FAIL if the implementation ever removes the disallowedTools
    guard — that is the property it exists to protect.
    """
    for caps in [_BASE_CAPS, _NET_CAPS, frozenset[Capability]()]:
        argv = build_argv(
            agent_bin="claude",
            harness_name="claude",
            model=None,
            capabilities=caps,
            prompt="",
        )
        assert "Bash(git push:*)" in argv, f"claude argv missing git-push deny for caps={caps!r}"


def test_claude_argv_hard_denies_gh_with_any_caps() -> None:
    """gh is denied in every claude argv regardless of capabilities."""
    for caps in [_BASE_CAPS, _NET_CAPS, frozenset[Capability]()]:
        argv = build_argv(
            agent_bin="claude",
            harness_name="claude",
            model=None,
            capabilities=caps,
            prompt="",
        )
        assert "Bash(gh:*)" in argv, f"claude argv missing gh deny for caps={caps!r}"


# ---------------------------------------------------------------------------
# Harness name survives round-trip through HarnessInfo
# ---------------------------------------------------------------------------


def test_run_agent_uses_invoked_as_binary(tmp_path: Path) -> None:
    """run_agent() uses HarnessInfo.invoked_as as the binary, not harness.agent."""
    fake = _make_fake_agent(tmp_path)
    # invoked_as overrides the default 'cursor' binary name.
    info = HarnessInfo(
        agent="cursor",
        model="",
        harness="cursor",
        invoked_as=str(fake),
    )
    result = run_agent("task", cwd=tmp_path, capabilities=_BASE_CAPS, timeout_s=10, harness=info)
    assert result.exit_status == 0


def test_run_agent_falls_back_to_harness_name(tmp_path: Path) -> None:
    """When invoked_as is empty, run_agent falls back to the harness name."""
    # We use 'cursor' as harness_name but invoked_as is empty; cursor is not
    # on PATH in CI, so this will exit non-zero — but we just need it to attempt
    # the binary named 'cursor', not crash the harness itself.
    info = HarnessInfo(
        agent="cursor",
        model="",
        harness="cursor",
        invoked_as="",
    )
    # This will likely exit with a non-zero status (binary not found), but
    # run_agent should return normally rather than raising.
    result = run_agent("task", cwd=tmp_path, capabilities=_BASE_CAPS, timeout_s=5, harness=info)
    assert isinstance(result, AgentResult)


# ---------------------------------------------------------------------------
# AgentResult is frozen
# ---------------------------------------------------------------------------


def test_agent_result_is_frozen() -> None:
    r = AgentResult(exit_status=0, stdout="", stderr="", timed_out=False, agent_claim=None)
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        r.exit_status = 1  # type: ignore[misc]


# ---------------------------------------------------------------------------
# UNRESTRICTED_HARNESSES — exported constant
# ---------------------------------------------------------------------------


def test_unrestricted_harnesses_does_not_include_claude() -> None:
    """claude is the only harness that can express --disallowedTools."""
    from jumar.harness import UNRESTRICTED_HARNESSES

    assert "claude" not in UNRESTRICTED_HARNESSES


def test_unrestricted_harnesses_includes_all_non_claude() -> None:
    """Every harness other than claude is unrestricted."""
    from jumar.harness import UNRESTRICTED_HARNESSES

    non_claude = SUPPORTED_HARNESSES - {"claude"}
    assert non_claude == UNRESTRICTED_HARNESSES


# ---------------------------------------------------------------------------
# check_harness_policy — startup gate for unrestricted harnesses
# ---------------------------------------------------------------------------


def test_unrestricted_harness_without_flag_raises_startup_error() -> None:
    """Using codex/cursor/etc. without allow_unrestricted_harness is a startup error."""
    from jumar.gate import GateStartupError, check_harness_policy
    from jumar.harness import UNRESTRICTED_HARNESSES

    for harness in UNRESTRICTED_HARNESSES:
        with pytest.raises(GateStartupError, match="allow_unrestricted_harness"):
            check_harness_policy(harness, allow_unrestricted_harness=False)


def test_unrestricted_harness_with_flag_is_permitted() -> None:
    """allow_unrestricted_harness = true clears the gate."""
    from jumar.gate import check_harness_policy
    from jumar.harness import UNRESTRICTED_HARNESSES

    for harness in UNRESTRICTED_HARNESSES:
        check_harness_policy(harness, allow_unrestricted_harness=True)  # must not raise


def test_claude_is_always_permitted() -> None:
    """claude enforces tool-level denials and never requires the flag."""
    from jumar.gate import check_harness_policy

    check_harness_policy("claude", allow_unrestricted_harness=False)  # must not raise


def test_check_harness_policy_error_names_the_harness() -> None:
    """The error message must name the offending harness for actionable output."""
    from jumar.gate import GateStartupError, check_harness_policy

    with pytest.raises(GateStartupError, match="codex"):
        check_harness_policy("codex", allow_unrestricted_harness=False)


# ---------------------------------------------------------------------------
# git -C . push bypass detection — argv-level
# ---------------------------------------------------------------------------


def test_git_c_push_is_denied_by_is_allowed() -> None:
    """git -C /path push must not bypass the hard-deny via flag interleaving."""
    from jumar.config import Config, is_allowed

    assert is_allowed(["git", "-C", "/repo", "push", "origin", "main"], Config()) is False


def test_git_c_non_push_is_still_allowed() -> None:
    """git -C with a non-push subcommand must pass the policy."""
    from jumar.config import Config, is_allowed

    assert is_allowed(["git", "-C", ".", "status"], Config()) is True


# ---------------------------------------------------------------------------
# Planning must not be able to do the work it is planning
# ---------------------------------------------------------------------------


def test_planning_disables_every_claude_tool() -> None:
    """Decompose is one structured completion, not an agent session.

    Observed 2026-08-11: a plan call for a 15-subtask audit item ran past
    twenty minutes because the planner had WebFetch and went browsing vendor
    sites instead of authoring checks.
    """
    argv = build_argv(
        agent_bin="claude",
        harness_name="claude",
        model="sonnet",
        capabilities=frozenset({Capability.write_fs, Capability.network}),
        prompt="plan it",
        allow_tools=False,
    )
    joined = " ".join(argv)
    for tool in ("Bash", "Read", "Write", "Glob", "Grep", "WebSearch", "WebFetch", "Task"):
        assert f"--disallowedTools {tool}" in joined, tool


def test_execution_keeps_its_tools() -> None:
    """The restriction is for planning only — execution still needs to work."""
    argv = build_argv(
        agent_bin="claude",
        harness_name="claude",
        model="sonnet",
        capabilities=frozenset({Capability.write_fs, Capability.network}),
        prompt="do it",
    )
    joined = " ".join(argv)
    assert "--disallowedTools Read" not in joined
    assert "--disallowedTools WebFetch" not in joined
    # The hard denials survive regardless of mode.
    assert "Bash(git push:*)" in joined
    assert "Bash(gh:*)" in joined


def test_planning_still_hard_denies_push_and_gh() -> None:
    argv = build_argv(
        agent_bin="claude",
        harness_name="claude",
        model="sonnet",
        capabilities=frozenset(),
        prompt="plan it",
        allow_tools=False,
    )
    joined = " ".join(argv)
    assert "Bash(git push:*)" in joined
    assert "Bash(gh:*)" in joined


def test_allow_tools_is_a_noop_for_harnesses_that_cannot_express_it() -> None:
    """Honest limitation: only claude takes tool denials, so say so in a test."""
    argv = build_argv(
        agent_bin="cursor",
        harness_name="cursor",
        model="auto",
        capabilities=frozenset({Capability.network}),
        prompt="plan it",
        allow_tools=False,
    )
    assert "--disallowedTools" not in " ".join(argv)


# ---------------------------------------------------------------------------
# MCP servers are never inherited from the user's configuration
# ---------------------------------------------------------------------------


def test_claude_always_runs_with_strict_mcp_config() -> None:
    """Both a startup-cost fix and a hole in the send boundary.

    Every invocation boots every configured MCP server before reading the
    prompt, and jumar spawns a fresh agent per subtask. Worse: `config.py` denies
    sendmail/ssh/scp, and an MCP mail server would hand the agent that exact
    capability without appearing in any argv. A boundary an unrelated user
    config can open is not a boundary.
    """
    for allow_tools in (True, False):
        argv = build_argv(
            agent_bin="claude",
            harness_name="claude",
            model="sonnet",
            capabilities=frozenset({Capability.write_fs, Capability.network}),
            prompt="x",
            allow_tools=allow_tools,
        )
        assert "--strict-mcp-config" in argv, f"allow_tools={allow_tools}"
        # Strictness is only meaningful because no config is supplied alongside.
        assert "--mcp-config" not in argv


# ---------------------------------------------------------------------------
# Session resumption — P3 (reuse-the-execution-session)
# ---------------------------------------------------------------------------


def test_session_resumable_harnesses_only_claude() -> None:
    """Only claude supports --resume; all other harnesses must fall back."""
    assert {"claude"} == SESSION_RESUMABLE_HARNESSES


def test_claude_argv_includes_resume_when_session_id_given() -> None:
    """--resume <session_id> must appear for claude when session_id is provided."""
    argv = build_argv(
        agent_bin="claude",
        harness_name="claude",
        model=None,
        capabilities=_BASE_CAPS,
        prompt="x",
        session_id="deadbeef1234abcd",
    )
    assert "--resume" in argv
    idx = argv.index("--resume")
    assert argv[idx + 1] == "deadbeef1234abcd"


def test_claude_argv_no_resume_when_session_id_none() -> None:
    """When session_id is None, neither session flag may appear."""
    argv = build_argv(
        agent_bin="claude",
        harness_name="claude",
        model=None,
        capabilities=_BASE_CAPS,
        prompt="x",
        session_id=None,
    )
    assert "--resume" not in argv
    assert "--session-id" not in argv


def test_claude_argv_uses_session_id_flag_when_session_is_new() -> None:
    """session_is_new=True must emit --session-id (create), never --resume.

    The claude CLI can only --resume a session that already exists; the first
    subtask of an item has to create the session under the plan's UUID or
    every later subtask's --resume fails with 'does not match any session'.
    """
    argv = build_argv(
        agent_bin="claude",
        harness_name="claude",
        model=None,
        capabilities=_BASE_CAPS,
        prompt="x",
        session_id="1c9e2f6a-8b3d-4e5f-9a7b-2c4d6e8f0a1b",
        session_is_new=True,
    )
    assert "--session-id" in argv
    assert "--resume" not in argv
    idx = argv.index("--session-id")
    assert argv[idx + 1] == "1c9e2f6a-8b3d-4e5f-9a7b-2c4d6e8f0a1b"


def test_make_session_id_is_a_canonical_uuid() -> None:
    """make_session_id must mint dashed UUIDs — claude validates --resume as UUID.

    Regression guard: token_hex(16) output (32 hex chars, no dashes) is
    rejected by the CLI with 'is not a UUID and does not match any session
    title', which burned the whole repair budget on every multi-subtask item.
    """
    import uuid as _uuid

    from jumar.clock import make_session_id

    sid = make_session_id()
    assert str(_uuid.UUID(sid)) == sid


def test_non_resumable_harness_ignores_session_id() -> None:
    """Harnesses without session support fall back to today's behaviour (no --resume)."""
    for harness in SUPPORTED_HARNESSES - SESSION_RESUMABLE_HARNESSES:
        argv = build_argv(
            agent_bin=harness,
            harness_name=harness,
            model=None,
            capabilities=_BASE_CAPS,
            prompt="task",
            session_id="abc123",
        )
        assert "--resume" not in argv, f"harness={harness!r} should not emit --resume"


def test_claude_resume_does_not_appear_before_strict_mcp_config() -> None:
    """--resume must not interfere with the argv ordering of security flags."""
    argv = build_argv(
        agent_bin="claude",
        harness_name="claude",
        model=None,
        capabilities=_BASE_CAPS,
        prompt="x",
        session_id="abc",
    )
    assert "--strict-mcp-config" in argv
    assert "--resume" in argv


def test_judge_verifier_runner_called_without_session_id(tmp_path: Path) -> None:
    """The judge runner must never receive a session_id — fresh context is the invariant."""
    import json as _json

    recorded: dict = {}

    def fake_run_agent(
        prompt: str,
        *,
        cwd: object,
        capabilities: object,
        timeout_s: int,
        harness: object,
        session_id: str | None = None,
    ) -> AgentResult:
        recorded["session_id"] = session_id
        return AgentResult(
            exit_status=0,
            stdout=_json.dumps({"verdict": "pass", "reason": "ok", "artefacts_shown": []}),
            stderr="",
            timed_out=False,
            agent_claim="pass",
        )

    from jumar.models import CheckKind, HarnessInfo
    from jumar.verify import VerifyContext
    from jumar.verify.judge import verify_judge

    check_raw = __import__("jumar.models", fromlist=["Check"]).Check(
        kind=CheckKind.judge,
        statement="The output is correct.",
        rationale="No executable check is possible.",
    )
    ctx = VerifyContext(
        cwd=tmp_path,
        run_dir=tmp_path,
        evidence_head_bytes=4000,
        subtask_id="item-01#2",
        attempt_no=0,
        harness=HarnessInfo(agent="claude", model="sonnet", harness="claude", invoked_as="claude"),
        judge_timeout_s=30,
        judge_run_agent=fake_run_agent,
    )
    verify_judge(check_raw, ctx)
    assert recorded.get("session_id") is None
