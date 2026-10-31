# SPDX-License-Identifier: Apache-2.0
"""Fail-closed guard for the claude harness's Bash tool.

The claude harness denies ``git push`` and ``gh`` with ``--disallowedTools``
patterns such as ``Bash(git push:*)``. A pattern matches the text of the
command line, and the agent writes that text, so every spelling the pattern
does not anticipate gets through: a control operator (``echo hi;git push``), a
wrapper (``env git push``, ``nohup git push``), a heredoc fed to a shell, a
``bash -c`` string, or a command name built from quoting or expansion
(``g'i't push``, ``$CMD push``).

This module treats the command line as untrusted text and reads it the way
bash will: it splits it into commands on every control operator and newline,
removes quoting, checks command and process substitutions, ``-c`` strings,
``eval`` arguments and heredocs fed to a shell as commands of their own, and
looks through the wrappers that run their argument as the command. Then it
applies the same hard denies as the patterns:

- ``gh`` is never run;
- ``git push`` (and the lower-level ``send-pack``) is never run, wherever the
  subcommand sits among git's global options;
- ``jumar-gh`` is not run when the subtask was not granted ``network``.

It fails **closed**. A command line it cannot parse, a command name it cannot
resolve to literal text, a shell reading its commands from a pipe, or a
wrapper whose options it does not know is refused, with a reason that tells
the agent how to spell the command plainly. Like the rest of the command
policy this is defence in depth, not a sandbox: an interpreter such as
``python3`` can still open a socket, and a script file the agent wrote is not
read. Running jumar in a container without push credentials is the control.

It is wired as a Claude Code ``PreToolUse`` hook by ``harness.build_argv``
and runs as ``python3 -I shell_guard.py``, so it is deliberately stdlib-only
with no imports from the jumar package. Exit status: ``0`` allows the call,
``2`` blocks it with the reason on stderr; any other failure is turned into
``2`` by the hook command itself.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import PurePosixPath

MAX_COMMAND_CHARS = 100_000
MAX_DEPTH = 4

# Command names are matched on their basename, as config.is_allowed() does.
_LITERAL_NAME = re.compile(r"^~?[A-Za-z0-9_.+:@%,/-]+$")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=")

_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "ash", "mksh", "yash"})
_RESERVED_SKIP = frozenset(
    {"!", "{", "}", "if", "then", "else", "elif", "fi", "do", "done", "while", "until"}
)
# Loop headers: the rest of the simple command holds no command name.
_RESERVED_HEADER = frozenset({"for", "select"})
_GIT_DENIED_SUBCOMMANDS = frozenset({"push", "send-pack", "http-push"})
# git global options that take their value as the next word.
_GIT_VALUE_OPTIONS = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env"}
)


class Blocked(Exception):
    """The command line must not run. ``str(exc)`` is the reason."""


@dataclass
class Word:
    text: str = ""
    literal: bool = True  # False once any expansion, substitution or glob appears
    quoted: bool = False  # any quote or backslash in the raw word


@dataclass
class Command:
    words: list[Word] = field(default_factory=list)
    piped_in: bool = False  # stdin comes from a pipe
    heredoc_bodies: list[tuple[str, bool]] = field(default_factory=list)  # (body, delimiter quoted)


def check_command(line: str, *, network: bool) -> None:
    """Raise :class:`Blocked` if *line* must not run; return normally otherwise."""
    if len(line) > MAX_COMMAND_CHARS:
        raise Blocked(
            f"jumar-guard[parse]: command line over {MAX_COMMAND_CHARS} characters; "
            "write it to a script file instead"
        )
    _check_line(line, network=network, depth=0)


def _check_line(line: str, *, network: bool, depth: int) -> None:
    if depth > MAX_DEPTH:
        raise Blocked(
            "jumar-guard[parse]: commands nested more than "
            f"{MAX_DEPTH} levels deep (substitutions, -c strings, eval); simplify the command"
        )
    for command in _Lexer(line, network=network, depth=depth).commands():
        _check_command(command, network=network, depth=depth)


# ---------------------------------------------------------------------------
# Lexer: one pass over the text, the way bash tokenises it
# ---------------------------------------------------------------------------


class _Lexer:
    """Split a command line into simple commands.

    Substitutions found anywhere (including inside double quotes, redirection
    targets and assignments) are checked recursively as they are met, so a
    word that is dropped later, such as a redirection target, cannot hide one.
    """

    def __init__(self, src: str, *, network: bool, depth: int) -> None:
        self.src = src
        self.n = len(src)
        self.i = 0
        self.network = network
        self.depth = depth
        self.out: list[Command] = []
        self.cmd = Command()
        self.word: Word | None = None
        self.pending_redirect = False
        self.pending_heredocs: list[tuple[str, bool, bool, Command]] = []
        self.heredoc_next = False
        self.heredoc_strip = False
        self.next_piped = False
        # case/esac bookkeeping: inside a case, words before ")" are a pattern.
        self.case_depth = 0
        self.expect_pattern = False

    # -- output ------------------------------------------------------------

    def commands(self) -> list[Command]:
        while self.i < self.n:
            self._step()
        self._end_word()
        if self.pending_heredocs or self.heredoc_next:
            raise Blocked("jumar-guard[parse]: heredoc without its body or delimiter line")
        self._end_command()
        return self.out

    def _end_word(self) -> None:
        if self.word is None:
            return
        word, self.word = self.word, None
        if self.heredoc_next:
            self.heredoc_next = False
            self.pending_heredocs.append((word.text, word.quoted, self.heredoc_strip, self.cmd))
            return
        if self.pending_redirect:
            self.pending_redirect = False
            return  # a redirection target is not a command word
        self.cmd.words.append(word)

    def _end_command(self) -> None:
        self._end_word()
        if self.pending_redirect or self.heredoc_next:
            raise Blocked("jumar-guard[parse]: redirection without a target")
        cmd, self.cmd = self.cmd, Command()
        first = cmd.words[0].text if cmd.words and cmd.words[0].literal else ""
        if first == "case" and not cmd.words[0].quoted:
            # A bare `case WORD in` header with its first pattern on a later line.
            self.case_depth += 1
            self.expect_pattern = True
            return
        if first == "esac" and self.case_depth:
            self.case_depth -= 1
            self.expect_pattern = False
            return
        if cmd.words or cmd.heredoc_bodies:
            self.out.append(cmd)

    def _separator(self, *, pipe: bool = False) -> None:
        self._end_command()
        if pipe:
            self.cmd.piped_in = True

    def _word(self) -> Word:
        if self.word is None:
            self.word = Word()
        return self.word

    # -- scanning ----------------------------------------------------------

    def _step(self) -> None:
        c = self.src[self.i]
        nxt = self.src[self.i + 1] if self.i + 1 < self.n else ""

        if c == "\\":
            if nxt == "\n":
                self.i += 2  # line continuation
                return
            if not nxt:
                raise Blocked("jumar-guard[parse]: trailing backslash")
            w = self._word()
            w.text += nxt
            w.quoted = True
            self.i += 2
            return
        if c == "'":
            end = self.src.find("'", self.i + 1)
            if end < 0:
                raise Blocked("jumar-guard[parse]: unbalanced single quote")
            w = self._word()
            w.text += self.src[self.i + 1 : end]
            w.quoted = True
            self.i = end + 1
            return
        if c == '"':
            w = self._word()
            w.quoted = True
            self.i = self._scan_double(self.i + 1, w)
            return
        if c == "$":
            self._dollar(self._word())
            return
        if c == "`":
            w = self._word()
            body, self.i = _backtick_body(self.src, self.i)
            self._recurse(body)
            w.literal = False
            return
        if c == "#" and self.word is None:
            end = self.src.find("\n", self.i)
            self.i = self.n if end < 0 else end
            return
        if c in " \t":
            self._end_word()
            self.i += 1
            return
        if c == "\n":
            self._end_word()
            self.i += 1
            self._read_heredoc_bodies()
            self._end_command()
            return
        if c in "<>" and nxt == "(" and self.word is None:
            # Process substitution: its own command line; the word is a path.
            body, self.i = _balanced(self.src, self.i + 1, "(", ")")
            self._recurse(body)
            w = self._word()
            w.literal = False
            return
        if c in ";&|()<>":
            self._operator(c, nxt)
            return
        fresh = self.word is None
        w = self._word()
        for token in ("[[", "[", "{"):
            if fresh and self._standalone(token):
                # `[`, `[[` and `{` as words of their own are commands or
                # reserved words, not glob or brace expansions.
                w.text += token
                self.i += len(token)
                return
        if c in "*?[{":
            w.literal = False
        w.text += c
        self.i += 1

    def _standalone(self, token: str) -> bool:
        if not self.src.startswith(token, self.i):
            return False
        j = self.i + len(token)
        return j >= self.n or self.src[j] in " \t\n;&|"

    def _operator(self, c: str, nxt: str) -> None:
        src, i = self.src, self.i
        if c in "<>":
            # A run of digits right before a redirection is a file descriptor.
            if self.word is not None and self.word.text.isdigit() and not self.word.quoted:
                self.word = None
            else:
                self._end_word()
            op = c
            j = i + 1
            if c == "<" and src.startswith("<<<", i):
                op, j = "<<<", i + 3
            elif c == "<" and src.startswith("<<-", i):
                op, j = "<<-", i + 3
            elif c == "<" and nxt == "<":
                op, j = "<<", i + 2
            elif nxt in ">&|" or (c == "<" and nxt == ">"):
                op, j = c + nxt, i + 2
            self.i = j
            if op in ("<<", "<<-"):
                self.heredoc_next = True
                self.heredoc_strip = op == "<<-"
            elif op in (">&", "<&") and self._at_fd_target():
                self._skip_fd_target()
            else:
                self.pending_redirect = True
            return
        if c == "&" and nxt == ">":
            self._end_word()
            self.i += 3 if src.startswith("&>>", i) else 2
            self.pending_redirect = True
            return
        if c == ";":
            if nxt == ";" or nxt == "&":
                self.i += 3 if src.startswith(";;&", i) else 2
                self._separator()
                if self.case_depth:
                    self.expect_pattern = True
                return
            self.i += 1
            self._separator()
            return
        if c == "|":
            self.i += 2 if nxt in "|&" else 1
            self._separator(pipe=nxt != "|")
            return
        if c == "&":
            self.i += 2 if nxt == "&" else 1
            self._separator()
            return
        if c == "(":
            self.i += 1
            if self.expect_pattern and not self.cmd.words and self.word is None:
                return  # optional "(" before a case pattern
            self._separator()
            return
        if c == ")":
            self.i += 1
            self._end_word()
            words = [w.text for w in self.cmd.words]
            if words[:1] == ["case"]:
                # `case WORD in PATTERN)` on one line: header and first pattern.
                self.cmd = Command()
                self.case_depth += 1
                self.expect_pattern = False
                return
            if self.expect_pattern:
                self.cmd = Command()  # the words were a case pattern
                self.expect_pattern = False
                return
            self._separator()
            return

    def _at_fd_target(self) -> bool:
        j = self.i
        while j < self.n and self.src[j] in " \t":
            j += 1
        m = re.match(r"\d+-?|-", self.src[j:])
        if m is None:
            return False
        return j + m.end() >= self.n or self.src[j + m.end()] in " \t\n;&|)<>"

    def _skip_fd_target(self) -> None:
        while self.i < self.n and self.src[self.i] in " \t":
            self.i += 1
        m = re.match(r"\d+-?|-", self.src[self.i :])
        assert m is not None
        self.i += m.end()

    def _dollar(self, w: Word) -> None:
        src, i = self.src, self.i
        nxt = src[i + 1] if i + 1 < self.n else ""
        if src.startswith("$((", i):
            # Arithmetic: its words are numbers and variables, not commands,
            # but a command substitution inside it still runs.
            body, self.i = _balanced(src, i + 1, "(", ")")
            self._scan_for_substitutions(body)
            w.literal = False
            return
        if nxt == "(":
            body, self.i = _balanced(src, i + 1, "(", ")")
            self._recurse(body)
            w.text += "$(" + body + ")"  # kept for the refusal message only
            w.literal = False
            return
        if nxt == "{":
            body, self.i = _balanced(src, i + 1, "{", "}")
            self._scan_for_substitutions(body)
            w.text += "${" + body + "}"  # kept for the refusal message only
            w.literal = False
            return
        if nxt == "'":
            # ANSI-C quoting can spell any byte (`$'\x67it'`): never literal.
            j = i + 2
            while j < self.n and src[j] != "'":
                j += 2 if src[j] == "\\" else 1
            if j >= self.n:
                raise Blocked("jumar-guard[parse]: unbalanced $'...' quote")
            w.text += src[i + 2 : j]
            w.literal = False
            w.quoted = True
            self.i = j + 1
            return
        if nxt == '"':
            w.quoted = True
            self.i = self._scan_double(i + 2, w)
            return
        if nxt and (nxt.isalnum() or nxt in "_@*#?$!-"):
            w.literal = False
            w.text += "$"
            self.i += 1
            return
        w.text += "$"
        self.i += 1

    def _scan_double(self, j: int, w: Word) -> int:
        """Scan a double-quoted span starting after the quote; return the index past it."""
        src = self.src
        while j < self.n:
            c = src[j]
            if c == '"':
                return j + 1
            if c == "\\":
                if j + 1 >= self.n:
                    break
                w.text += src[j + 1] if src[j + 1] in '$`"\\\n' else c + src[j + 1]
                j += 2
                continue
            if c == "`":
                body, j = _backtick_body(src, j)
                self._recurse(body)
                w.literal = False
                continue
            if c == "$":
                save = self.i
                self.i = j
                if j + 1 < self.n and src[j + 1] == '"':
                    w.text += "$"
                    self.i = j + 1
                else:
                    self._dollar(w)
                j, self.i = self.i, save
                continue
            w.text += c
            j += 1
        raise Blocked("jumar-guard[parse]: unbalanced double quote")

    def _scan_for_substitutions(self, text: str) -> None:
        """Check every substitution inside parameter-expansion text."""
        sub = _Lexer(text, network=self.network, depth=self.depth)
        w = Word()
        j = 0
        while j < len(text):
            c = text[j]
            if c == "\\":
                j += 2
            elif c == "'":
                end = text.find("'", j + 1)
                j = len(text) if end < 0 else end + 1
            elif c == '"':
                j = sub._scan_double(j + 1, w)
            elif c == "`":
                body, j = _backtick_body(text, j)
                self._recurse(body)
            elif c == "$":
                sub.i = j
                sub._dollar(w)
                j = sub.i
            else:
                j += 1

    def _read_heredoc_bodies(self) -> None:
        for delim, quoted, strip, owner in self.pending_heredocs:
            lines: list[str] = []
            while True:
                if self.i >= self.n:
                    raise Blocked(f"jumar-guard[parse]: heredoc never reaches its {delim!r} line")
                end = self.src.find("\n", self.i)
                end = self.n if end < 0 else end
                line = self.src[self.i : end]
                self.i = min(end + 1, self.n)
                if (line.lstrip("\t") if strip else line) == delim:
                    break
                lines.append(line)
            body = "\n".join(lines)
            if not quoted:
                self._scan_for_substitutions(body)
            owner.heredoc_bodies.append((body, quoted))
        self.pending_heredocs = []

    def _recurse(self, body: str) -> None:
        _check_line(body, network=self.network, depth=self.depth + 1)


_HEREDOC_OP = re.compile(r"<<-?[ \t]*(['\"]?)([^\s'\";&|()<>]+)\1")


def _balanced(src: str, start: int, open_c: str, close_c: str) -> tuple[str, int]:
    """Return (body, index past the closer) for a span opening at ``src[start]``.

    Heredoc bodies inside the span are skipped as data, so an apostrophe or a
    parenthesis in a commit message fed through ``$(cat <<'EOF' ...)`` does not
    unbalance it.
    """
    depth = 0
    j = start
    n = len(src)
    heredocs: list[tuple[str, bool]] = []
    while j < n:
        c = src[j]
        if c == "\\":
            j += 2
            continue
        if c == "<" and open_c == "(" and src.startswith("<<", j) and not src.startswith("<<<", j):
            m = _HEREDOC_OP.match(src, j)
            if m:
                heredocs.append((m.group(2), src.startswith("<<-", j)))
                j = m.end()
                continue
        if c == "\n" and heredocs:
            j += 1
            for delim, strip in heredocs:
                while True:
                    if j >= n:
                        raise Blocked(
                            f"jumar-guard[parse]: heredoc never reaches its {delim!r} line"
                        )
                    end = src.find("\n", j)
                    end = n if end < 0 else end
                    line = src[j:end]
                    j = end + 1
                    if (line.lstrip("\t") if strip else line) == delim:
                        break
            heredocs = []
            continue
        if c == "'" and open_c == "(":
            end = src.find("'", j + 1)
            if end < 0:
                break
            j = end + 1
            continue
        if c == '"':
            j += 1
            while j < n and src[j] != '"':
                j += 2 if src[j] == "\\" else 1
            j += 1
            continue
        if c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return src[start + 1 : j], j + 1
        j += 1
    raise Blocked(f"jumar-guard[parse]: unbalanced {open_c}...{close_c}")


def _backtick_body(src: str, start: int) -> tuple[str, int]:
    j = start + 1
    out: list[str] = []
    while j < len(src):
        c = src[j]
        if c == "\\" and j + 1 < len(src):
            out.append(src[j + 1] if src[j + 1] in "`$\\" else c + src[j + 1])
            j += 2
            continue
        if c == "`":
            return "".join(out), j + 1
        out.append(c)
        j += 1
    raise Blocked("jumar-guard[parse]: unbalanced backtick")


# ---------------------------------------------------------------------------
# Per-command checks
# ---------------------------------------------------------------------------


def _check_command(cmd: Command, *, network: bool, depth: int) -> None:
    words = list(cmd.words)
    stdin_hidden = cmd.piped_in

    # Leading assignments and reserved words are not the command.
    while words:
        w = words[0]
        bare = w.literal and not w.quoted
        if not w.quoted and _ASSIGNMENT.match(w.text):
            words.pop(0)
            continue
        if bare and w.text in _RESERVED_SKIP:
            words.pop(0)
            continue
        if bare and w.text in _RESERVED_HEADER:
            return  # `for x in ...`: no command name in the header
        if bare and w.text == "function":
            words = words[2:]  # `function NAME`; the body follows
            continue
        if bare and w.text == "coproc":
            words.pop(0)
            if len(words) > 1 and words[1].literal and words[1].text == "{":
                words = words[2:]  # `coproc NAME { ...; }`
            continue
        break

    args_hidden = False
    while words:
        name = _command_name(words[0])
        base = PurePosixPath(name).name or name
        rest = words[1:]

        if base == "gh":
            raise Blocked(
                "jumar-guard[deny]: gh is never run by jumar's agent. "
                "Use jumar-gh for read-only GitHub access."
            )
        if base == "jumar-gh" and not network:
            raise Blocked(
                "jumar-guard[deny]: jumar-gh needs the network capability, "
                "which this subtask was not granted."
            )
        if base == "git":
            _check_git(rest, network=network, depth=depth, args_hidden=args_hidden)
            return
        if base in _SHELLS:
            _check_shell(cmd, rest, network=network, depth=depth, stdin_hidden=stdin_hidden)
            return
        if base == "eval":
            _check_eval(rest, network=network, depth=depth)
            return
        if base == "alias":
            for w in rest:
                if "=" in w.text:
                    _check_line(w.text.split("=", 1)[1], network=network, depth=depth + 1)
            return
        if base == "busybox":
            words = rest  # `busybox APPLET ...` runs the applet
            continue
        if base == "watch":
            _check_joined(_skip_options(rest, base, _WATCH_VALUE, _WATCH_FLAGS), network, depth)
            return

        unwrapped = _unwrap(base, rest)
        if unwrapped is None:
            return  # not a wrapper: an ordinary command we have no rule for
        words, hides_args = unwrapped
        args_hidden = args_hidden or hides_args


def _command_name(w: Word) -> str:
    if not w.literal:
        raise Blocked(
            "jumar-guard[expansion]: the command name is built from an expansion "
            f"({w.text!r}); spell the command name literally"
        )
    if w.text in ("[", "[["):
        return w.text
    if not _LITERAL_NAME.match(w.text):
        raise Blocked(f"jumar-guard[expansion]: cannot resolve command name {w.text!r}")
    return w.text[2:] if w.text.startswith("~/") else w.text


def _check_git(rest: list[Word], *, network: bool, depth: int, args_hidden: bool) -> None:
    j = 0
    while j < len(rest):
        w = rest[j]
        t = w.text
        if not w.literal:
            raise Blocked(
                "jumar-guard[expansion]: git's subcommand or a global option before it "
                f"comes from an expansion ({t!r}); spell it literally"
            )
        if t in _GIT_VALUE_OPTIONS:
            if j + 1 < len(rest):
                _check_git_config_value(t, rest[j + 1].text)
            j += 2
            continue
        if t.startswith("-c") and len(t) > 2:
            _check_git_config_value("-c", t[2:])
            j += 1
            continue
        if t.startswith("--config-env="):
            _check_git_config_value("--config-env", t.split("=", 1)[1])
            j += 1
            continue
        if t.startswith("-"):
            j += 1
            continue
        sub = t
        sub_args = rest[j + 1 :]
        if sub in _GIT_DENIED_SUBCOMMANDS:
            raise Blocked("jumar-guard[deny]: git push is never run by jumar's agent.")
        if sub == "config" and any(a.text.startswith("alias.") for a in sub_args):
            raise Blocked("jumar-guard[deny]: git aliases are not set by jumar's agent.")
        if sub == "submodule" and sub_args[:1] and sub_args[0].text == "foreach":
            cmd_words = [a for a in sub_args[1:] if not a.text.startswith("--")]
            _check_joined(cmd_words, network, depth)
        if sub == "rebase":
            for k, a in enumerate(sub_args):
                if a.text in ("-x", "--exec") and k + 1 < len(sub_args):
                    _check_joined([sub_args[k + 1]], network, depth)
                elif a.text.startswith("--exec="):
                    _check_line(a.text.split("=", 1)[1], network=network, depth=depth + 1)
        if sub == "bisect" and sub_args[:1] and sub_args[0].text == "run":
            _check_joined(sub_args[1:], network, depth)
        return
    if args_hidden:
        raise Blocked(
            "jumar-guard[expansion]: git's subcommand would come from stdin (xargs); "
            "spell the git command literally"
        )


def _check_git_config_value(option: str, value: str) -> None:
    key = value.split("=", 1)[0].lower()
    if key.startswith("alias."):
        raise Blocked(f"jumar-guard[deny]: git {option} cannot define an alias for jumar's agent.")


def _check_shell(
    cmd: Command, rest: list[Word], *, network: bool, depth: int, stdin_hidden: bool
) -> None:
    j = 0
    command_string = False
    while j < len(rest):
        w = rest[j]
        t = w.text
        if not w.literal and t.startswith("-"):
            raise Blocked(f"jumar-guard[expansion]: shell option {t!r} comes from an expansion")
        if t == "--" or t == "-":
            j += 1
            break
        if t.startswith("--"):
            j += 2 if t in ("--rcfile", "--init-file") else 1
            continue
        if t[:1] in "-+" and len(t) > 1:
            flags = t[1:]
            if "c" in flags and t[0] == "-":
                command_string = True
            j += 1
            if "o" in flags or "O" in flags:
                j += 1  # -o / -O take an option name
            continue
        break
    operands = rest[j:]
    if command_string:
        if not operands:
            raise Blocked("jumar-guard[parse]: shell -c without a command string")
        if not operands[0].literal:
            raise Blocked(
                "jumar-guard[expansion]: the shell's -c string comes from an expansion; "
                "write the commands literally"
            )
        _check_line(operands[0].text, network=network, depth=depth + 1)
        return
    if operands:
        return  # runs a script file, which is not read here
    # The shell reads its commands from stdin.
    for body, _quoted in cmd.heredoc_bodies:
        _check_line(body, network=network, depth=depth + 1)
    if stdin_hidden:
        raise Blocked(
            "jumar-guard[parse]: a shell reading its commands from a pipe cannot be checked; "
            "run the commands directly or from a script file"
        )


def _check_eval(rest: list[Word], *, network: bool, depth: int) -> None:
    args = list(rest)
    while args and args[0].literal and args[0].text in ("--", "builtin", "command", "-p"):
        args.pop(0)
    _check_joined(args, network, depth)


def _check_joined(words: list[Word], network: bool, depth: int) -> None:
    if any(not w.literal for w in words):
        raise Blocked(
            "jumar-guard[expansion]: a command string built from an expansion cannot be "
            "checked; write the commands literally"
        )
    _check_line(" ".join(w.text for w in words), network=network, depth=depth + 1)


# ---------------------------------------------------------------------------
# Wrappers: commands that run their arguments as the command
# ---------------------------------------------------------------------------

# name -> (options taking a value, options without one, operands to skip before the command)
_WRAPPERS: dict[str, tuple[frozenset[str], frozenset[str], int]] = {
    "env": (
        frozenset({"-u", "--unset", "-C", "--chdir"}),
        frozenset({"-i", "--ignore-environment", "-0", "--null", "-v", "--debug", "-"}),
        0,
    ),
    "command": (frozenset(), frozenset({"-p"}), 0),
    "builtin": (frozenset(), frozenset(), 0),
    "exec": (frozenset({"-a"}), frozenset({"-c", "-l"}), 0),
    "nohup": (frozenset(), frozenset(), 0),
    "time": (
        frozenset({"-o", "--output", "-f", "--format"}),
        frozenset({"-p", "-a", "--append", "-v", "--verbose", "-q", "--quiet", "--portability"}),
        0,
    ),
    "nice": (frozenset({"-n", "--adjustment"}), frozenset(), 0),
    "timeout": (
        frozenset({"-s", "--signal", "-k", "--kill-after"}),
        frozenset({"--preserve-status", "--foreground", "-v", "--verbose"}),
        1,  # DURATION
    ),
    "setsid": (frozenset(), frozenset({"-c", "--ctty", "-f", "--fork", "-w", "--wait"}), 0),
    "stdbuf": (frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}), frozenset(), 0),
    "sudo": (
        frozenset({"-u", "--user", "-g", "--group", "-C", "-D", "-h", "-p", "-r", "-t", "-U"}),
        frozenset(
            {"-E", "-H", "-n", "-k", "-S", "-b", "-P", "--preserve-env", "--non-interactive"}
        ),
        0,
    ),
    "doas": (frozenset({"-u", "-C"}), frozenset({"-n", "-s"}), 0),
    "xargs": (
        frozenset(
            {
                "-a",
                "--arg-file",
                "-d",
                "--delimiter",
                "-E",
                "-I",
                "-L",
                "--max-lines",
                "-n",
                "--max-args",
                "-P",
                "--max-procs",
                "-s",
                "--max-chars",
                "--process-slot-var",
            }
        ),
        frozenset(
            {
                "-0",
                "--null",
                "-r",
                "--no-run-if-empty",
                "-t",
                "--verbose",
                "-p",
                "--interactive",
                "-x",
                "--exit",
                "--open-tty",
                "-o",
            }
        ),
        0,
    ),
    "flock": (
        frozenset({"-w", "--timeout", "-E", "--conflict-exit-code"}),
        frozenset(
            {
                "-s",
                "--shared",
                "-x",
                "-e",
                "--exclusive",
                "-u",
                "--unlock",
                "-n",
                "--nonblock",
                "-o",
                "--close",
                "-F",
                "--no-fork",
                "--verbose",
            }
        ),
        1,  # the lock file
    ),
}
_WATCH_VALUE = frozenset({"-n", "--interval", "-q", "--equexit"})
_WATCH_FLAGS = frozenset(
    {
        "-b",
        "--beep",
        "-d",
        "--differences",
        "-e",
        "--errexit",
        "-g",
        "--chgexit",
        "-t",
        "--no-title",
        "-x",
        "--exec",
        "-c",
        "--color",
        "-p",
        "--precise",
        "-w",
        "--no-wrap",
    }
)


def _unwrap(base: str, rest: list[Word]) -> tuple[list[Word], bool] | None:
    """If *base* is a wrapper, return (the wrapped command's words, args hidden)."""
    if base not in _WRAPPERS:
        return None
    if base == "command" and any(w.text in ("-v", "-V") for w in rest[:2]):
        return [], False  # a lookup, not a run
    if base == "env" and any(
        w.text in ("-S", "--split-string") or w.text.startswith(("-S", "--split-string="))
        for w in rest
    ):
        raise Blocked(
            "jumar-guard[wrapper]: env -S splits its string with its own escapes, "
            "so the command it runs cannot be checked"
        )
    if base == "flock" and any(w.text in ("-c", "--command") for w in rest):
        k = next(i for i, w in enumerate(rest) if w.text in ("-c", "--command"))
        return ([Word("sh"), Word("-c"), *rest[k + 1 : k + 2]], False)
    value_opts, flag_opts, skip = _WRAPPERS[base]
    words = _skip_options(rest, base, value_opts, flag_opts)
    if base == "env":
        while words and not words[0].quoted and _ASSIGNMENT.match(words[0].text):
            words = words[1:]
    words = words[skip:]
    if base == "xargs":
        return (words or [Word("echo")]), True
    return words, False


def _skip_options(
    rest: list[Word], base: str, value_opts: frozenset[str], flag_opts: frozenset[str]
) -> list[Word]:
    j = 0
    while j < len(rest):
        w = rest[j]
        t = w.text
        if t == "--":
            return rest[j + 1 :]
        if not t.startswith("-") or t == "-" and "-" not in flag_opts:
            if base == "env" and not w.quoted and _ASSIGNMENT.match(t):
                j += 1
                continue
            return rest[j:]
        if not w.literal:
            raise Blocked(f"jumar-guard[wrapper]: {base} option {t!r} comes from an expansion")
        if t in flag_opts:
            j += 1
            continue
        if t in value_opts:
            j += 2
            continue
        opt = t.split("=", 1)[0]
        if "=" in t and opt in value_opts:
            j += 1
            continue
        # Attached short value (-n5, -oL, -uNAME) or a numeric nice level (-10).
        if t[:2] in value_opts and len(t) > 2:
            j += 1
            continue
        if base == "nice" and t[1:].isdigit():
            j += 1
            continue
        raise Blocked(
            f"jumar-guard[wrapper]: unknown {base} option {t!r}, so the command it runs "
            "cannot be found; drop the wrapper or the option"
        )
    return []


# ---------------------------------------------------------------------------
# Claude Code PreToolUse hook entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    network = "--no-network" not in args
    try:
        raw = sys.stdin.read(MAX_COMMAND_CHARS * 4 + 1)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise Blocked("jumar-guard[hook]: hook input is not a JSON object")
        if payload.get("tool_name") != "Bash":
            return 0
        tool_input = payload.get("tool_input")
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        if not isinstance(command, str):
            raise Blocked("jumar-guard[hook]: Bash call without a command string")
        check_command(command, network=network)
    except Blocked as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — fail closed on anything unexpected
        print(f"jumar-guard[error]: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
