# SPDX-License-Identifier: Apache-2.0
"""Tests for the termination class (W17): how an agent run ended decides
whether anything it left behind may be checked at all."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import jumar.verify as verify_mod
from jumar.harness import AgentResult, classify_termination
from jumar.journal import Journal
from jumar.models import Check, CheckKind, Termination, Verdict
from jumar.verify import VerifyContext, run_verify, unclean_result
from jumar.verify.file import verify_file


def _result(
    exit_status: int = 0,
    *,
    stdout: str = "done",
    stderr: str = "",
    timed_out: bool = False,
    termination: Termination | None = None,
) -> AgentResult:
    return AgentResult(
        exit_status=exit_status,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        agent_claim=None,
        termination=termination,
    )


# ---------------------------------------------------------------------------
# classify_termination
# ---------------------------------------------------------------------------


def test_a_zero_exit_with_nothing_reported_is_clean() -> None:
    assert classify_termination(_result(), None) is Termination.clean


def test_a_harness_outage_outranks_everything() -> None:
    r = _result(1, timed_out=True, termination=Termination.step_cap)
    assert classify_termination(r, "usage_limit") is Termination.harness_outage


def test_a_timeout_outranks_what_the_harness_reported() -> None:
    r = _result(-1, timed_out=True, termination=Termination.clean)
    assert classify_termination(r, None) is Termination.timed_out


@pytest.mark.parametrize(
    "reported", [Termination.step_cap, Termination.truncated, Termination.denied_tool]
)
def test_what_the_harness_reports_is_kept(reported: Termination) -> None:
    assert classify_termination(_result(0, termination=reported), None) is reported


def test_a_non_zero_exit_is_never_clean() -> None:
    assert classify_termination(_result(1), None) is Termination.abnormal_exit
    assert classify_termination(_result(-9), None) is Termination.abnormal_exit


def test_claudes_max_turns_stop_is_a_step_cap() -> None:
    r = _result(1, stdout="Error: Reached max turns (25)")
    assert classify_termination(r, None) is Termination.step_cap


# ---------------------------------------------------------------------------
# run_verify refuses to let a check vouch for a cut-off run
# ---------------------------------------------------------------------------


def _ctx(tmp_path: Path, termination: Termination | None) -> VerifyContext:
    return VerifyContext(
        cwd=tmp_path,
        run_dir=tmp_path / "run",
        evidence_head_bytes=256,
        subtask_id="item#0",
        attempt_no=0,
        termination=termination,
    )


def _journal(tmp_path: Path) -> Journal:
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    return Journal(run_dir / "journal.jsonl", "run")


def _entries(tmp_path: Path) -> list[dict[str, Any]]:
    text = (tmp_path / "run" / "journal.jsonl").read_text()
    return [json.loads(line) for line in text.splitlines() if line.strip()]


_CHECK = Check(kind=CheckKind.file, statement="marker says OK", path="marker.txt", pattern="OK")


@pytest.mark.parametrize(
    "termination",
    [t for t in Termination if t is not Termination.clean],
)
def test_a_passing_artefact_does_not_pass_a_cut_off_run(
    tmp_path: Path, termination: Termination
) -> None:
    (tmp_path / "marker.txt").write_text("OK\n")  # would pass the check

    result = run_verify(
        _CHECK, journal=_journal(tmp_path), item_id="item", ctx=_ctx(tmp_path, termination)
    )

    assert result.verdict is Verdict.failed
    assert result.termination is termination
    assert result.evidence["error"] == "unclean_termination"
    assert result.evidence["termination"] == termination.value
    assert result.evidence["check_run"] is False
    (entry,) = [e for e in _entries(tmp_path) if e["event"] == "verification"]
    assert entry["payload"]["verdict"] == "failed"
    assert entry["payload"]["termination"] == termination.value


def test_a_cut_off_run_never_reaches_the_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Check] = []

    def spy(check: Check, ctx: VerifyContext) -> Any:
        calls.append(check)
        return verify_file(check, ctx)

    monkeypatch.setitem(verify_mod._registry, CheckKind.file, spy)
    run_verify(
        _CHECK,
        journal=_journal(tmp_path),
        item_id="item",
        ctx=_ctx(tmp_path, Termination.step_cap),
    )
    assert calls == []


def test_a_clean_run_is_checked_and_the_verdict_carries_clean(tmp_path: Path) -> None:
    (tmp_path / "marker.txt").write_text("OK\n")
    result = run_verify(
        _CHECK, journal=_journal(tmp_path), item_id="item", ctx=_ctx(tmp_path, Termination.clean)
    )
    assert result.verdict is Verdict.passed
    assert result.termination is Termination.clean
    (entry,) = [e for e in _entries(tmp_path) if e["event"] == "verification"]
    assert entry["payload"]["termination"] == "clean"


def test_a_clean_run_still_fails_a_check_it_does_not_meet(tmp_path: Path) -> None:
    result = run_verify(
        _CHECK, journal=_journal(tmp_path), item_id="item", ctx=_ctx(tmp_path, Termination.clean)
    )
    assert result.verdict is Verdict.failed
    assert result.evidence.get("error") != "unclean_termination"


def test_no_termination_on_the_context_keeps_the_old_behaviour(tmp_path: Path) -> None:
    (tmp_path / "marker.txt").write_text("OK\n")
    result = run_verify(
        _CHECK, journal=_journal(tmp_path), item_id="item", ctx=_ctx(tmp_path, None)
    )
    assert result.verdict is Verdict.passed
    assert result.termination is None


def test_unclean_result_says_why_in_its_summary(tmp_path: Path) -> None:
    r = unclean_result(_CHECK, _ctx(tmp_path, Termination.timed_out), Termination.timed_out)
    assert "timed_out" in r.summary
    assert "not checked" in r.summary
