# SPDX-License-Identifier: Apache-2.0
"""Smoke tests so the validation gate is green from the first build iteration."""

from __future__ import annotations

import pytest

from getstuffdone import __version__
from getstuffdone.cli import SUBCOMMANDS, build_parser, main


def test_version_is_set() -> None:
    assert __version__


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "usage" in capsys.readouterr().out.lower()


@pytest.mark.parametrize("command", SUBCOMMANDS)
def test_known_subcommands_parse(command: str) -> None:
    # resume and report require a run_id positional; supply a stub.
    _NEEDS_RUN_ID = {"resume", "report"}
    argv = [command, "stub-run-id"] if command in _NEEDS_RUN_ID else [command]
    assert build_parser().parse_args(argv).command == command


def test_unimplemented_subcommand_reports_nonzero() -> None:
    assert main(["plan"]) == 2
