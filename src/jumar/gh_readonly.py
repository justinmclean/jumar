"""Read-only gh: the one way an agent or check reaches authenticated GitHub.

`gh` itself stays hard-denied (config._HARD_DENY, harness._CLAUDE_HARD_DENY_TOOLS)
because an argv prefix cannot tell `gh pr view` from `gh pr merge`, or a GET
`gh api` call from one carrying `-f`. This module makes that call once, by
parsing the arguments, and the `jumar-gh` entry point execs gh only when it
says yes. Anything it does not recognise is refused.

Allowed:
  - api ENDPOINT with no method override, field or input flags (a GET)
  - api graphql with -f/-F fields, when no field value contains a mutation
  - issue|pr view|list|status|diff|checks
  - search (all kinds)
  - run|release|workflow view|list, repo view, label list
"""

from __future__ import annotations

import os
import re
import sys

_READ_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "issue": frozenset({"view", "list", "status"}),
    "pr": frozenset({"view", "list", "status", "diff", "checks"}),
    "run": frozenset({"view", "list"}),
    "release": frozenset({"view", "list"}),
    "workflow": frozenset({"view", "list"}),
    "repo": frozenset({"view"}),
    "label": frozenset({"list"}),
}

# Flags that make `gh api` send a body or a non-GET method.
_API_WRITE_FLAGS = ("-X", "--method", "-f", "--raw-field", "-F", "--field", "--input")
_GRAPHQL_FIELD_FLAGS = ("-f", "--raw-field", "-F", "--field")
_MUTATION = re.compile(r"\bmutation\b", re.IGNORECASE)
# `gh api` flags that take a separate value, so the value is not the endpoint.
_API_VALUE_FLAGS = frozenset(
    {
        "-H",
        "--header",
        "-q",
        "--jq",
        "-t",
        "--template",
        "-X",
        "--method",
        "-f",
        "--raw-field",
        "-F",
        "--field",
        "--input",
        "--hostname",
        "-p",
        "--preview",
        "--cache",
    }
)


def _flag_values(args: list[str], flags: tuple[str, ...]) -> list[str] | None:
    """Values given to *flags* in *args*, in both `-f v` and `-f=v` forms.

    None when a flag is the last token with no value.
    """
    values: list[str] = []
    for i, arg in enumerate(args):
        for flag in flags:
            if arg == flag:
                if i + 1 >= len(args):
                    return None
                values.append(args[i + 1])
            elif arg.startswith(flag + "="):
                values.append(arg[len(flag) + 1 :])
            elif len(flag) == 2 and arg.startswith(flag) and len(arg) > 2:
                values.append(arg[2:])
    return values


def _uses_any(args: list[str], flags: tuple[str, ...]) -> bool:
    values = _flag_values(args, flags)
    return values is None or bool(values)


def _api_endpoint(args: list[str]) -> str | None:
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg in _API_VALUE_FLAGS:
            skip = True
        elif not arg.startswith("-"):
            return arg
    return None


def _api_is_read_only(args: list[str]) -> bool:
    endpoint = _api_endpoint(args)
    if endpoint is None:
        return False
    if endpoint == "graphql":
        if _uses_any(args, ("-X", "--method", "--input")):
            return False
        fields = _flag_values(args, _GRAPHQL_FIELD_FLAGS)
        if not fields:
            return False
        # `-F query=@file` reads the query from disk, where it cannot be checked.
        return not any(_MUTATION.search(v) or "=@" in v for v in fields)
    methods = _flag_values(args, ("-X", "--method"))
    if methods is None or any(m.upper() != "GET" for m in methods):
        return False
    return not _uses_any(args, _API_WRITE_FLAGS[2:])


def is_read_only(args: list[str]) -> bool:
    """True iff `gh *args` only reads. *args* excludes the `gh` itself."""
    if not args:
        return False
    command, rest = args[0], args[1:]
    if command == "api":
        return _api_is_read_only(rest)
    if command == "search":
        return bool(rest)
    allowed = _READ_SUBCOMMANDS.get(command)
    return allowed is not None and bool(rest) and rest[0] in allowed


def main() -> None:
    args = sys.argv[1:]
    if not is_read_only(args):
        print(
            "jumar-gh: refused, not a read-only gh command: gh " + " ".join(args),
            file=sys.stderr,
        )
        sys.exit(2)
    try:
        os.execvp("gh", ["gh", *args])
    except OSError as exc:
        print(f"jumar-gh: could not run gh: {exc}", file=sys.stderr)
        sys.exit(127)


if __name__ == "__main__":
    main()
