# SPDX-License-Identifier: Apache-2.0
"""Tests for the claude harness shell guard (src/jumar/shell_guard.py).

`--disallowedTools "Bash(git push:*)"` matches command text the agent writes.
These tests pin that every spelling which defeats a text pattern is refused
for a denied command, and that ordinary commands, including the shapes an
agent uses every day, still run. Observed against a real claude CLI before
this guard existed: `bash -c 'git push origin main'` and
`echo 'git push origin main' | sh` both reached the remote.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from jumar.harness import build_argv, claude_guard_settings
from jumar.models import Capability
from jumar.shell_guard import Blocked, check_command

GUARD = Path(__file__).resolve().parent.parent / "src" / "jumar" / "shell_guard.py"


def _blocked(line: str, *, network: bool = True) -> str:
    with pytest.raises(Blocked) as exc_info:
        check_command(line, network=network)
    return str(exc_info.value)


# ---------------------------------------------------------------------------
# Every bypass spelling is refused for a denied command
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "git push",
        "git push origin main",
        "gh pr create --fill",
        "/usr/bin/git push",
        "/usr/local/bin/gh auth status",
    ],
)
def test_plain_denied_commands_are_refused(line: str) -> None:
    assert "jumar-guard[deny]" in _blocked(line)


@pytest.mark.parametrize(
    "line",
    [
        "echo hi;git push",
        "echo hi ; git push",
        "true && git push",
        "false || gh pr list",
        "echo x|gh api user",
        "echo x |& gh api user",
        "sleep 1 &git push",
        "echo a\ngit push origin main",
        "echo a \\\n; git push",
    ],
)
def test_control_operators_do_not_hide_a_denied_command(line: str) -> None:
    assert "jumar-guard[deny]" in _blocked(line)


@pytest.mark.parametrize(
    "line",
    [
        "bash <<EOF\ngit push\nEOF",
        "sh <<'EOF'\ngh pr create\nEOF",
        "bash <<-EOF\n\tgit push\n\tEOF",
        "cat <<EOF\n$(git push)\nEOF",
    ],
)
def test_heredocs_fed_to_a_shell_or_holding_substitutions_are_checked(line: str) -> None:
    assert "jumar-guard[deny]" in _blocked(line)


@pytest.mark.parametrize(
    "line",
    [
        "env git push",
        "env -i FOO=1 git push",
        "env -u HOME -- git push",
        "nohup git push &",
        "command git push",
        "command -p git push",
        "builtin exec git push",
        "exec git push",
        "time git push",
        "time -p git push",
        "timeout 5 git push",
        "timeout -s KILL 5 git push",
        "nice -n 5 git push",
        "nice -10 git push",
        "setsid git push",
        "stdbuf -oL git push",
        "sudo -u root git push",
        "xargs git push",
        "xargs -0 -n 1 gh",
        "flock /tmp/l git push",
        "flock /tmp/l -c 'git push'",
        "watch -n 5 git push",
        "busybox sh -c 'git push'",
        "env nohup command git push",
    ],
)
def test_wrappers_do_not_hide_a_denied_command(line: str) -> None:
    assert "jumar-guard[deny]" in _blocked(line)


@pytest.mark.parametrize(
    "line",
    [
        "$CMD push",
        '"$GIT" push',
        "${G} push",
        "$(echo git) push",
        "`echo git` push",
        "/usr/bin/gi? push",
        "/usr/bin/g[i]t push",
        "{git,} push",
        "$'\\x67it' push",
        "git${IFS}push",
        "git $SUB origin",
        "xargs git",
        'eval "$X"',
        'bash -c "$CMD"',
    ],
)
def test_command_names_built_from_expansion_are_refused(line: str) -> None:
    assert "jumar-guard[expansion]" in _blocked(line)


@pytest.mark.parametrize(
    "line",
    [
        "g'i't push",
        '"g"h pr list',
        "\\git push",
        "'gh' api user",
        '"git" "push"',
    ],
)
def test_quoting_is_removed_before_matching(line: str) -> None:
    assert "jumar-guard[deny]" in _blocked(line)


@pytest.mark.parametrize(
    "line",
    [
        "echo $(git push)",
        'echo "$(gh pr view 1)"',
        "x=`git push`",
        "cat <(git push)",
        "diff <(ls) >(gh api user)",
        "echo ${x:-$(git push)}",
        "echo $((1 + $(git push)))",
        "echo hi > $(git push)",
    ],
)
def test_substitutions_are_checked_wherever_they_appear(line: str) -> None:
    assert "jumar-guard[deny]" in _blocked(line)


@pytest.mark.parametrize(
    "line",
    [
        "bash -c 'git push'",
        'sh -lc "gh pr list"',
        "zsh -ec 'echo; git push'",
        "bash -o pipefail -c 'git push'",
        "eval git push",
        "eval 'git push'",
        "alias p='git push'",
        "bash -c \"bash -c 'git push'\"",
    ],
)
def test_strings_run_as_commands_are_checked(line: str) -> None:
    assert "jumar-guard[deny]" in _blocked(line)


@pytest.mark.parametrize(
    "line",
    [
        "git -C . push",
        "git --git-dir=.git --work-tree . push",
        "git -c user.name=x push",
        "git --no-pager push origin",
        "git send-pack origin main",
        "git submodule foreach 'git push'",
        "git rebase -x 'git push' main",
        "git rebase --exec='git push' main",
        "git bisect run git push",
    ],
)
def test_git_push_is_found_wherever_the_subcommand_sits(line: str) -> None:
    assert "jumar-guard[deny]" in _blocked(line)


@pytest.mark.parametrize(
    "line",
    ["git -c alias.p=push p", "git config alias.p push", "git config --global alias.up push"],
)
def test_git_aliases_cannot_be_used_to_rename_push(line: str) -> None:
    assert "alias" in _blocked(line)


@pytest.mark.parametrize(
    "line",
    [
        "function f { git push; }",
        "f() { git push; }",
        "{ git push; }",
        "(git push)",
        "if true; then git push; fi",
        "while true; do git push; done",
        "for i in 1 2; do git push; done",
        "case x in a) git push;; esac",
        "case $1 in\n  *)\n    gh pr list\n    ;;\nesac",
        "! git push",
        "coproc git push",
    ],
)
def test_compound_commands_are_looked_inside(line: str) -> None:
    assert "jumar-guard[deny]" in _blocked(line)


# ---------------------------------------------------------------------------
# Fail closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "git push 'oops",
        'echo "unterminated',
        "echo \\",
        "echo $(git status",
        "echo `date",
        "cat <<EOF\nno end",
        "echo hi >",
    ],
)
def test_unparseable_lines_are_refused(line: str) -> None:
    assert "jumar-guard[parse]" in _blocked(line)


def test_a_shell_reading_commands_from_a_pipe_is_refused() -> None:
    assert "jumar-guard[parse]" in _blocked("echo 'git push origin main' | sh")
    assert "jumar-guard[parse]" in _blocked("cat cmds.txt | bash")


@pytest.mark.parametrize(
    "line", ["env -S 'git push'", "env --split-string='git push'", "sudo -s", "nice --bogus x"]
)
def test_unparseable_wrappers_are_refused(line: str) -> None:
    assert "jumar-guard[wrapper]" in _blocked(line)


def test_nesting_beyond_the_limit_is_refused() -> None:
    line = "echo hi"
    for _ in range(8):
        line = f"echo $({line})"
    assert "nested" in _blocked(line)


def test_an_oversized_command_line_is_refused() -> None:
    assert "characters" in _blocked("echo " + "x" * 200_000)


# ---------------------------------------------------------------------------
# jumar-gh follows the network capability
# ---------------------------------------------------------------------------


def test_jumar_gh_is_refused_without_network() -> None:
    assert "network" in _blocked("jumar-gh pr view 1", network=False)
    assert "network" in _blocked("echo x && env jumar-gh api user", network=False)


def test_jumar_gh_runs_with_network() -> None:
    check_command("jumar-gh pr view 1", network=True)


# ---------------------------------------------------------------------------
# Ordinary commands still run
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "make check",
        "git status",
        "git -C sub status",
        "git diff HEAD~1 -- src/",
        "git log --grep=push --format='%h %s' -5",
        "git stash push -m wip",
        "git commit -m \"$(cat <<'EOF'\nFix the parser (don't break heredocs)\n\nBody.\nEOF\n)\"",
        "cat <<EOF\ngit push\nEOF",
        "cat <<'EOF' > notes.md\ngh pr list is how you'd do it\nEOF",
        "python3 -m pytest -q && ruff check src",
        "uv run --all-extras pytest -q",
        'for f in *.py; do echo "$f"; done',
        "case $x in a) echo a;; *) echo b;; esac",
        'case "$1" in\n  start)\n    echo s\n    ;;\n  *)\n    echo other\n    ;;\nesac',
        "if [ -f x ]; then echo y; fi",
        "[[ -f a && -f b ]] && echo ok",
        "ls | grep foo > out.txt 2>&1",
        'echo "it\'s fine"',
        "grep -q 'push' file",
        "f() { echo hi; }; f",
        "(cd sub && make)",
        "{ echo a; echo b; } > out",
        "export X=$(pwd)",
        "echo $((1+2)) $(( $a + 1 ))",
        "VAR=1 python3 x.py",
        "env FOO=1 python3 x.py",
        "timeout 60 make check",
        "command -v git",
        "bash scripts/test.sh",
        "bash -c 'make check'",
        'while read l; do echo "$l"; done < <(ls)',
        "echo '{\"a\": 1}' | python3 -m json.tool",
        "ls *.py",
        "echo ${HOME}/x",
        "sleep 1 &",
        "! grep -q x file",
        "cd /tmp\nls -la",
        "# just a comment",
        "",
    ],
)
def test_ordinary_commands_are_allowed(line: str) -> None:
    check_command(line, network=False)


# ---------------------------------------------------------------------------
# The hook protocol: JSON on stdin, exit 2 + stderr to block
# ---------------------------------------------------------------------------


def _hook(payload: object, *args: str) -> subprocess.CompletedProcess[str]:
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, "-I", str(GUARD), *args],
        input=body,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _bash(command: object) -> dict[str, object]:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def test_hook_blocks_with_exit_2_and_the_reason_on_stderr() -> None:
    result = _hook(_bash("bash -c 'git push origin main'"))
    assert result.returncode == 2
    assert "jumar-guard[deny]" in result.stderr
    assert result.stdout == ""


def test_hook_allows_with_exit_0() -> None:
    result = _hook(_bash("make check"))
    assert result.returncode == 0
    assert result.stderr == ""


def test_hook_ignores_tools_other_than_bash() -> None:
    assert _hook({"tool_name": "Read", "tool_input": {"file_path": "x"}}).returncode == 0


@pytest.mark.parametrize(
    "payload",
    ["not json", "[1, 2]", {"tool_name": "Bash"}, _bash(None), _bash(["git", "push"])],
)
def test_hook_fails_closed_on_malformed_input(payload: object) -> None:
    result = _hook(payload)
    assert result.returncode == 2
    assert "jumar-guard" in result.stderr


def test_hook_no_network_flag_refuses_jumar_gh() -> None:
    assert _hook(_bash("jumar-gh pr view 1"), "--no-network").returncode == 2
    assert _hook(_bash("jumar-gh pr view 1")).returncode == 0


def test_hook_runs_isolated_without_the_jumar_package(tmp_path: Path) -> None:
    """`python3 -I` has no PYTHONPATH and no site-packages hacks: stdlib only."""
    copy = tmp_path / "shell_guard.py"
    copy.write_text(GUARD.read_text())
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(copy)],
        input=json.dumps(_bash("git push")),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert "jumar-guard[deny]" in result.stderr


# ---------------------------------------------------------------------------
# Wiring: the claude argv installs the hook, and the hook command fails closed
# ---------------------------------------------------------------------------


def _claude_argv(caps: frozenset[Capability]) -> list[str]:
    return build_argv(
        agent_bin="claude", harness_name="claude", model=None, capabilities=caps, prompt="x"
    )


def _hook_command(argv: list[str]) -> str:
    settings = json.loads(argv[argv.index("--settings") + 1])
    (entry,) = settings["hooks"]["PreToolUse"]
    assert entry["matcher"] == "Bash"
    (hook,) = entry["hooks"]
    assert hook["type"] == "command"
    command = hook["command"]
    assert isinstance(command, str)
    return command


def _run_hook_command(command: str, line: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", "-c", command],
        input=json.dumps(_bash(line)),
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_claude_argv_installs_the_guard_hook_and_keeps_the_patterns() -> None:
    argv = _claude_argv(frozenset({Capability.network}))
    assert "--settings" in argv
    assert "Bash(git push:*)" in argv  # defence in depth stays
    assert "Bash(gh:*)" in argv
    command = _hook_command(argv)
    assert command.endswith(" || exit 2")
    assert "-I" in command and "shell_guard.py" in command


def test_the_exact_hook_command_blocks_and_allows() -> None:
    command = _hook_command(_claude_argv(frozenset({Capability.network})))
    blocked = _run_hook_command(command, "echo 'git push origin main' | sh")
    assert blocked.returncode == 2
    assert "jumar-guard" in blocked.stderr
    assert _run_hook_command(command, "make check").returncode == 0


def test_the_hook_command_follows_the_network_capability() -> None:
    without = _hook_command(_claude_argv(frozenset()))
    with_net = _hook_command(_claude_argv(frozenset({Capability.network})))
    assert "--no-network" in without
    assert "--no-network" not in with_net
    assert _run_hook_command(without, "jumar-gh pr view 1").returncode == 2
    assert _run_hook_command(with_net, "jumar-gh pr view 1").returncode == 0


def test_the_hook_command_blocks_when_the_guard_cannot_start(tmp_path: Path) -> None:
    """Claude only treats exit 2 as a block: a crash must not become an allow."""
    settings = json.loads(claude_guard_settings(network=True))
    command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    broken = command.replace(sys.executable, str(tmp_path / "no-such-python"), 1)
    assert broken != command
    assert _run_hook_command(broken, "make check").returncode == 2


def test_planning_calls_carry_the_guard_too() -> None:
    argv = build_argv(
        agent_bin="claude",
        harness_name="claude",
        model=None,
        capabilities=frozenset(),
        prompt="x",
        allow_tools=False,
    )
    assert "--settings" in argv
