# SPDX-License-Identifier: Apache-2.0
"""Tests for config.py: load_config() and is_allowed()."""

from __future__ import annotations

import textwrap
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from getstuffdone.config import (
    Capability,
    CommandPolicy,
    Config,
    HarnessConfig,
    is_allowed,
    load_config,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content))


# ---------------------------------------------------------------------------
# load_config — defaults (no config file present)
# ---------------------------------------------------------------------------


def test_defaults_todo_path(tmp_path: Path) -> None:
    assert load_config(tmp_path).todo_path == "todo.md"


def test_defaults_numeric_fields(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    assert cfg.max_subtasks == 12
    assert cfg.max_repairs == 2
    assert cfg.subtask_timeout_s == 900
    assert cfg.evidence_head_bytes == 4000


def test_defaults_bool_fields(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    assert cfg.halt_on_fail is False
    assert cfg.commit_on_complete is False


def test_default_capabilities_are_read_write_run(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    assert Capability.read_fs in cfg.capabilities
    assert Capability.write_fs in cfg.capabilities
    assert Capability.run_commands in cfg.capabilities


def test_network_is_a_default_capability(tmp_path: Path) -> None:
    """Decided 2026-08-08: an agent that cannot fetch a primary source will
    write from memory instead, which is the worse failure. The enforced
    boundary moved from *fetch* to *send*; see `_DEFAULT_DENY`."""
    assert Capability.network in load_config(tmp_path).capabilities


def test_git_commit_not_in_default_capabilities(tmp_path: Path) -> None:
    assert Capability.git_commit not in load_config(tmp_path).capabilities


def test_default_harness(tmp_path: Path) -> None:
    h = load_config(tmp_path).harness
    assert h.agent == "claude"
    assert h.model == "sonnet"


def test_default_allow_list(tmp_path: Path) -> None:
    allow = load_config(tmp_path).commands.allow
    assert "python3" in allow
    assert "git" in allow
    assert "make" in allow


def test_default_deny_list_is_the_send_boundary(tmp_path: Path) -> None:
    """Deny outbound *transmission*, not outbound reading."""
    deny = load_config(tmp_path).commands.deny
    for sender in ("mail", "mailx", "sendmail", "ssh", "scp", "sftp", "rsync"):
        assert sender in deny, sender


def test_fetchers_are_not_denied(tmp_path: Path) -> None:
    deny = load_config(tmp_path).commands.deny
    assert "curl" not in deny
    assert "wget" not in deny


# ---------------------------------------------------------------------------
# load_config — from gsd.toml
# ---------------------------------------------------------------------------


def test_load_todo_path_from_gsd_toml(tmp_path: Path) -> None:
    _write(
        tmp_path / "gsd.toml",
        """\
        [gsd]
        todo_path = "work.md"
    """,
    )
    assert load_config(tmp_path).todo_path == "work.md"


def test_load_numeric_fields_from_gsd_toml(tmp_path: Path) -> None:
    _write(
        tmp_path / "gsd.toml",
        """\
        [gsd]
        max_subtasks = 5
        max_repairs = 1
        subtask_timeout_s = 300
        evidence_head_bytes = 2000
    """,
    )
    cfg = load_config(tmp_path)
    assert cfg.max_subtasks == 5
    assert cfg.max_repairs == 1
    assert cfg.subtask_timeout_s == 300
    assert cfg.evidence_head_bytes == 2000


def test_load_bool_fields_from_gsd_toml(tmp_path: Path) -> None:
    _write(
        tmp_path / "gsd.toml",
        """\
        [gsd]
        halt_on_fail = true
        commit_on_complete = true
    """,
    )
    cfg = load_config(tmp_path)
    assert cfg.halt_on_fail is True
    assert cfg.commit_on_complete is True


def test_load_capabilities_from_gsd_toml(tmp_path: Path) -> None:
    _write(
        tmp_path / "gsd.toml",
        """\
        [gsd]
        capabilities = ["read_fs", "network"]
    """,
    )
    cfg = load_config(tmp_path)
    assert cfg.capabilities == frozenset({Capability.read_fs, Capability.network})


def test_load_harness_from_gsd_toml(tmp_path: Path) -> None:
    _write(
        tmp_path / "gsd.toml",
        """\
        [gsd.harness]
        agent = "gemini"
        model = "flash"
    """,
    )
    h = load_config(tmp_path).harness
    assert h.agent == "gemini"
    assert h.model == "flash"


def test_load_commands_from_gsd_toml(tmp_path: Path) -> None:
    _write(
        tmp_path / "gsd.toml",
        """\
        [gsd.commands]
        allow = ["python3", "make"]
        deny = ["curl"]
    """,
    )
    c = load_config(tmp_path).commands
    assert c.allow == ("python3", "make")
    assert c.deny == ("curl",)


def test_unset_fields_keep_defaults_with_gsd_toml(tmp_path: Path) -> None:
    _write(
        tmp_path / "gsd.toml",
        """\
        [gsd]
        todo_path = "tasks.md"
    """,
    )
    cfg = load_config(tmp_path)
    assert cfg.max_subtasks == 12  # default preserved


# ---------------------------------------------------------------------------
# load_config — from pyproject.toml [tool.gsd]
# ---------------------------------------------------------------------------


def test_load_from_pyproject_toml(tmp_path: Path) -> None:
    _write(
        tmp_path / "pyproject.toml",
        """\
        [project]
        name = "myproject"

        [tool.gsd]
        todo_path = "tasks.md"
        max_repairs = 3
    """,
    )
    cfg = load_config(tmp_path)
    assert cfg.todo_path == "tasks.md"
    assert cfg.max_repairs == 3
    assert cfg.max_subtasks == 12  # default preserved


def test_pyproject_without_tool_gsd_uses_defaults(tmp_path: Path) -> None:
    _write(
        tmp_path / "pyproject.toml",
        """\
        [project]
        name = "myproject"
    """,
    )
    assert load_config(tmp_path).todo_path == "todo.md"


# ---------------------------------------------------------------------------
# load_config — source priority
# ---------------------------------------------------------------------------


def test_gsd_toml_wins_over_pyproject(tmp_path: Path) -> None:
    _write(tmp_path / "gsd.toml", '[gsd]\ntodo_path = "from_gsd.md"\n')
    _write(tmp_path / "pyproject.toml", '[tool.gsd]\ntodo_path = "from_pyproject.md"\n')
    assert load_config(tmp_path).todo_path == "from_gsd.md"


def test_cli_overrides_win_over_file(tmp_path: Path) -> None:
    _write(tmp_path / "gsd.toml", "[gsd]\nmax_subtasks = 5\n")
    cfg = load_config(tmp_path, cli_overrides={"max_subtasks": 20})
    assert cfg.max_subtasks == 20


def test_cli_overrides_win_over_defaults(tmp_path: Path) -> None:
    cfg = load_config(tmp_path, cli_overrides={"halt_on_fail": True})
    assert cfg.halt_on_fail is True


def test_cli_override_todo_path(tmp_path: Path) -> None:
    cfg = load_config(tmp_path, cli_overrides={"todo_path": "custom.md"})
    assert cfg.todo_path == "custom.md"


# ---------------------------------------------------------------------------
# Config — construction and immutability
# ---------------------------------------------------------------------------


def test_config_is_frozen() -> None:
    cfg = Config()
    with pytest.raises(FrozenInstanceError):
        cfg.max_subtasks = 99  # type: ignore[misc]


def test_config_default_capabilities_frozen_set() -> None:
    assert isinstance(Config().capabilities, frozenset)


def test_harness_config_defaults() -> None:
    h = HarnessConfig()
    assert h.agent == "claude"
    assert h.model == "sonnet"


def test_command_policy_defaults() -> None:
    p = CommandPolicy()
    assert "python3" in p.allow
    assert "curl" in p.allow
    assert "sendmail" in p.deny


# ---------------------------------------------------------------------------
# is_allowed — allowed commands (happy path)
# ---------------------------------------------------------------------------


def test_python3_is_allowed() -> None:
    assert is_allowed(["python3", "script.py"], Config()) is True


def test_pytest_is_allowed() -> None:
    assert is_allowed(["pytest", "tests/"], Config()) is True


def test_ruff_is_allowed() -> None:
    assert is_allowed(["ruff", "check", "src"], Config()) is True


def test_make_is_allowed() -> None:
    assert is_allowed(["make", "check"], Config()) is True


def test_git_status_is_allowed() -> None:
    assert is_allowed(["git", "status"], Config()) is True


def test_git_commit_is_allowed() -> None:
    assert is_allowed(["git", "commit", "-m", "msg"], Config()) is True


def test_git_diff_is_allowed() -> None:
    assert is_allowed(["git", "diff"], Config()) is True


# ---------------------------------------------------------------------------
# is_allowed — deny list (negative)
# ---------------------------------------------------------------------------


def test_curl_is_allowed() -> None:
    assert is_allowed(["curl", "https://example.com"], Config()) is True


def test_wget_is_allowed() -> None:
    assert is_allowed(["wget", "https://example.com"], Config()) is True


def test_sendmail_is_denied() -> None:
    assert is_allowed(["sendmail", "-t"], Config()) is False


def test_mail_is_denied() -> None:
    assert is_allowed(["mail", "-s", "subject", "someone@example.com"], Config()) is False


def test_rsync_is_denied() -> None:
    assert is_allowed(["rsync", "-a", "out/", "host:/tmp"], Config()) is False


def test_ssh_is_denied() -> None:
    assert is_allowed(["ssh", "user@host"], Config()) is False


def test_scp_is_denied() -> None:
    assert is_allowed(["scp", "file", "host:path"], Config()) is False


def test_deny_wins_over_allow() -> None:
    cfg = Config(
        commands=CommandPolicy(
            allow=("curl", "wget"),
            deny=("curl",),
        )
    )
    assert is_allowed(["curl", "example.com"], cfg) is False


# ---------------------------------------------------------------------------
# is_allowed — not in allow list (negative)
# ---------------------------------------------------------------------------


def test_unknown_command_is_denied() -> None:
    assert is_allowed(["rm", "-rf", "/"], Config()) is False


def test_bash_not_in_default_allow() -> None:
    assert is_allowed(["bash", "-c", "echo hi"], Config()) is False


def test_python2_style_invocation_is_allowed() -> None:
    """`python` is on the allow list alongside `python3`. This is deliberate
    and it is why the allow list is defence in depth rather than a boundary:
    `python -c "import smtplib"` sends mail. The container is the control."""
    assert is_allowed(["python", "script.py"], Config()) is True


# ---------------------------------------------------------------------------
# is_allowed — empty argv (negative)
# ---------------------------------------------------------------------------


def test_empty_argv_denied() -> None:
    assert is_allowed([], Config()) is False


# ---------------------------------------------------------------------------
# is_allowed — hard deny (always refused, even if in allow list)
# ---------------------------------------------------------------------------


def test_git_push_hard_denied() -> None:
    cfg = Config(commands=CommandPolicy(allow=("git",), deny=()))
    assert is_allowed(["git", "push", "origin", "main"], cfg) is False


def test_git_push_force_hard_denied() -> None:
    cfg = Config(commands=CommandPolicy(allow=("git",), deny=()))
    assert is_allowed(["git", "push", "--force"], cfg) is False


def test_git_push_upstream_hard_denied() -> None:
    cfg = Config(commands=CommandPolicy(allow=("git",), deny=()))
    assert is_allowed(["git", "push", "-u", "origin", "HEAD"], cfg) is False


def test_gh_hard_denied() -> None:
    cfg = Config(commands=CommandPolicy(allow=("gh",), deny=()))
    assert is_allowed(["gh", "pr", "create"], cfg) is False


def test_gh_alone_hard_denied() -> None:
    cfg = Config(commands=CommandPolicy(allow=("gh",), deny=()))
    assert is_allowed(["gh"], cfg) is False


def test_git_push_denied_with_default_config() -> None:
    assert is_allowed(["git", "push"], Config()) is False


def test_gh_denied_with_default_config() -> None:
    assert is_allowed(["gh", "pr", "list"], Config()) is False


# ---------------------------------------------------------------------------
# is_allowed — custom policy
# ---------------------------------------------------------------------------


def test_custom_allow_list() -> None:
    cfg = Config(commands=CommandPolicy(allow=("node",), deny=()))
    assert is_allowed(["node", "index.js"], cfg) is True
    assert is_allowed(["python3", "script.py"], cfg) is False


def test_custom_deny_list() -> None:
    cfg = Config(commands=CommandPolicy(allow=("python3", "make", "git"), deny=("make",)))
    assert is_allowed(["make", "check"], cfg) is False
    assert is_allowed(["python3", "test.py"], cfg) is True
