# SPDX-License-Identifier: Apache-2.0
"""``gsd`` command-line entry point.

Skeleton only. Subcommands land with their pipeline stages, in the phase order
set out in ``specs/04-technical-plan.md``; see ``IMPLEMENTATION_PLAN.md``.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from . import __version__

SUBCOMMANDS = ("plan", "run", "resume", "report", "schedule", "doctor")


def build_parser() -> argparse.ArgumentParser:
    """Return the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="gsd",
        description="Turn a todo list into verified work.",
    )
    parser.add_argument("--version", action="version", version=f"gsd {__version__}")
    parser.add_argument(
        "command",
        nargs="?",
        choices=SUBCOMMANDS,
        help="subcommand to run (not yet implemented)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI. Returns the process exit status."""
    args = build_parser().parse_args(argv)
    if args.command is None:
        build_parser().print_help()
        return 0
    print(f"gsd {args.command}: not implemented yet — see IMPLEMENTATION_PLAN.md")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
