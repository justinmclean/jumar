# SPDX-License-Identifier: Apache-2.0
"""Tests for verify/judge.py — Stage 6: Verify, kind=judge.

Acceptance criteria covered:
- AC6.4  The ``judge`` verifier receives no part of the executing agent's
         transcript — asserted by a test that inspects the assembled verifier
         prompt and asserts no fragment of the executor's transcript is present.

Also covers:
- No harness configured in VerifyContext → inconclusive (never a pass)
- Agent returns {"verdict": "pass", ...} → passed
- Agent returns {"verdict": "fail", ...} → failed
- Agent returns {"verdict": "inconclusive", ...} → inconclusive
- Unparseable agent response → inconclusive (not a pass)
- Garbage / unexpected response → failed (not a pass; default-fail framing)
- Prompt contains the acceptance statement
- Prompt contains the rationale
- check.path set → artefact file content included in the assembled prompt
- check.path set but file missing → no crash; inconclusive or fail returned
- Evidence file written to run_dir/artifacts/ on completion
- judge kind registered in the default verifier registry
- run_verify dispatches to verify_judge for kind=judge
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from getstuffdone.journal import VERIFICATION, Journal
from getstuffdone.models import Check, CheckKind, HarnessInfo, Verdict
from getstuffdone.verify import VerifyContext, _registry, run_verify
from getstuffdone.verify.judge import build_judge_prompt, verify_judge

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeResult:
    """Minimal stand-in for harness.AgentResult."""

    exit_status: int
    stdout: str
    stderr: str = ""
    timed_out: bool = False
    agent_claim: str | None = None


_HARNESS = HarnessInfo(
    agent="claude",
    model="sonnet",
    harness="claude",
    invoked_as="claude",
)

_JUDGE_CHECK = Check(
    kind=CheckKind.judge,
    statement="The output file contains valid JSON with a 'result' key.",
    rationale="File content quality cannot be checked mechanically.",
)


def _ctx(
    tmp_path: Path,
    *,
    harness: HarnessInfo | None = _HARNESS,
    runner: Any | None = None,
    subtask_id: str = "item#0",
    attempt_no: int = 0,
) -> VerifyContext:
    return VerifyContext(
        cwd=tmp_path,
        run_dir=tmp_path,
        evidence_head_bytes=4000,
        subtask_id=subtask_id,
        attempt_no=attempt_no,
        harness=harness,
        judge_timeout_s=30,
        judge_run_agent=runner,
    )


def _runner(verdict: str, reason: str = "evidence cited") -> Any:
    """Return a fake runner that responds with the given verdict."""

    def _run(
        prompt: str, *, cwd: Any, capabilities: Any, timeout_s: Any, harness: Any
    ) -> _FakeResult:
        return _FakeResult(
            exit_status=0,
            stdout=json.dumps(
                {
                    "verdict": verdict,
                    "reason": reason,
                    "artefacts_shown": [],
                }
            ),
        )

    return _run


def _capturing_runner() -> tuple[Any, list[str]]:
    """Return (runner, captured_prompts_list) — the runner appends each prompt."""
    prompts: list[str] = []

    def _run(
        prompt: str, *, cwd: Any, capabilities: Any, timeout_s: Any, harness: Any
    ) -> _FakeResult:
        prompts.append(prompt)
        return _FakeResult(
            exit_status=0,
            stdout=json.dumps({"verdict": "fail", "reason": "default", "artefacts_shown": []}),
        )

    return _run, prompts


# ---------------------------------------------------------------------------
# No harness → inconclusive
# ---------------------------------------------------------------------------


def test_no_harness_yields_inconclusive(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, harness=None, runner=None)
    result = verify_judge(_JUDGE_CHECK, ctx)
    assert result.verdict == Verdict.inconclusive


def test_no_harness_evidence_names_reason(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, harness=None, runner=None)
    result = verify_judge(_JUDGE_CHECK, ctx)
    assert result.evidence.get("error") == "no_harness_configured"


def test_no_harness_is_not_passed(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, harness=None, runner=None)
    result = verify_judge(_JUDGE_CHECK, ctx)
    assert result.verdict is not Verdict.passed


# ---------------------------------------------------------------------------
# Verdict mappings
# ---------------------------------------------------------------------------


def test_pass_verdict_yields_passed(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, runner=_runner("pass"))
    result = verify_judge(_JUDGE_CHECK, ctx)
    assert result.verdict == Verdict.passed


def test_fail_verdict_yields_failed(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, runner=_runner("fail"))
    result = verify_judge(_JUDGE_CHECK, ctx)
    assert result.verdict == Verdict.failed


def test_inconclusive_verdict_yields_inconclusive(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, runner=_runner("inconclusive"))
    result = verify_judge(_JUDGE_CHECK, ctx)
    assert result.verdict == Verdict.inconclusive


def test_inconclusive_is_not_passed(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, runner=_runner("inconclusive"))
    result = verify_judge(_JUDGE_CHECK, ctx)
    assert result.verdict is not Verdict.passed


# ---------------------------------------------------------------------------
# Default-fail framing: unparseable / garbage response is never a pass
# ---------------------------------------------------------------------------


def test_unparseable_response_yields_inconclusive(tmp_path: Path) -> None:
    """Spec rule: unparseable response → inconclusive (not a pass)."""

    def _garbage_runner(
        prompt: str, *, cwd: Any, capabilities: Any, timeout_s: Any, harness: Any
    ) -> _FakeResult:
        return _FakeResult(exit_status=0, stdout="I cannot determine this.")

    ctx = _ctx(tmp_path, runner=_garbage_runner)
    result = verify_judge(_JUDGE_CHECK, ctx)
    assert result.verdict == Verdict.inconclusive


def test_unparseable_response_is_not_passed(tmp_path: Path) -> None:
    def _garbage_runner(
        prompt: str, *, cwd: Any, capabilities: Any, timeout_s: Any, harness: Any
    ) -> _FakeResult:
        return _FakeResult(exit_status=0, stdout="looks good to me!")

    ctx = _ctx(tmp_path, runner=_garbage_runner)
    result = verify_judge(_JUDGE_CHECK, ctx)
    assert result.verdict is not Verdict.passed


def test_unknown_verdict_string_defaults_to_failed(tmp_path: Path) -> None:
    """Any verdict other than "pass" or "inconclusive" is treated as failed."""
    ctx = _ctx(tmp_path, runner=_runner("maybe"))
    result = verify_judge(_JUDGE_CHECK, ctx)
    assert result.verdict == Verdict.failed


def test_empty_stdout_yields_inconclusive(tmp_path: Path) -> None:
    def _empty_runner(
        prompt: str, *, cwd: Any, capabilities: Any, timeout_s: Any, harness: Any
    ) -> _FakeResult:
        return _FakeResult(exit_status=0, stdout="")

    ctx = _ctx(tmp_path, runner=_empty_runner)
    result = verify_judge(_JUDGE_CHECK, ctx)
    assert result.verdict == Verdict.inconclusive


# ---------------------------------------------------------------------------
# AC6.4 — fresh context: judge prompt must not contain any executor transcript
# ---------------------------------------------------------------------------


TRANSCRIPT_SENTINEL = "EXECUTOR_TRANSCRIPT_SENTINEL_MUST_NOT_APPEAR_IN_JUDGE_PROMPT"


def test_judge_prompt_excludes_executor_transcript(tmp_path: Path) -> None:
    """AC6.4: the assembled judge prompt contains no fragment of the executor's transcript.

    Simulates the real scenario: execute.py writes a transcript to
    run_dir/artifacts/{subtask_id}-attempt-{n}.txt.  The judge must not
    read or include that file.
    """
    # Plant a fake executor transcript exactly where execute.py would write it.
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    transcript = artifacts_dir / "item-0-attempt-0.txt"
    transcript.write_text(
        f"=== stdout ===\n{TRANSCRIPT_SENTINEL}\n=== stderr ===\n",
        encoding="utf-8",
    )

    runner, captured = _capturing_runner()
    ctx = _ctx(tmp_path, runner=runner)
    verify_judge(_JUDGE_CHECK, ctx)

    assert len(captured) == 1, "runner must have been called exactly once"
    assert TRANSCRIPT_SENTINEL not in captured[0], (
        "judge prompt must not contain any part of the executor's transcript (AC6.4)"
    )


def test_build_judge_prompt_excludes_executor_transcript(tmp_path: Path) -> None:
    """AC6.4 via build_judge_prompt(): the constructed prompt string never
    contains a transcript fragment, verified at the prompt-assembly level."""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    transcript = artifacts_dir / "item-0-attempt-0.txt"
    transcript.write_text(f"=== stdout ===\n{TRANSCRIPT_SENTINEL}\n", encoding="utf-8")

    ctx = _ctx(tmp_path, runner=None)
    prompt, _ = build_judge_prompt(_JUDGE_CHECK, ctx)

    assert TRANSCRIPT_SENTINEL not in prompt


# ---------------------------------------------------------------------------
# Prompt content assertions
# ---------------------------------------------------------------------------


def test_prompt_contains_acceptance_statement(tmp_path: Path) -> None:
    runner, captured = _capturing_runner()
    ctx = _ctx(tmp_path, runner=runner)
    verify_judge(_JUDGE_CHECK, ctx)
    assert _JUDGE_CHECK.statement in captured[0]


def test_prompt_contains_rationale(tmp_path: Path) -> None:
    runner, captured = _capturing_runner()
    ctx = _ctx(tmp_path, runner=runner)
    verify_judge(_JUDGE_CHECK, ctx)
    assert _JUDGE_CHECK.rationale is not None
    assert _JUDGE_CHECK.rationale in captured[0]


def test_prompt_has_default_fail_framing(tmp_path: Path) -> None:
    """The prompt must state that the default answer is FAIL."""
    runner, captured = _capturing_runner()
    ctx = _ctx(tmp_path, runner=runner)
    verify_judge(_JUDGE_CHECK, ctx)
    assert "FAIL" in captured[0] or "fail" in captured[0].lower()


def test_build_judge_prompt_returns_statement(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, runner=None)
    prompt, _ = build_judge_prompt(_JUDGE_CHECK, ctx)
    assert _JUDGE_CHECK.statement in prompt


# ---------------------------------------------------------------------------
# Artefact file inclusion
# ---------------------------------------------------------------------------


def test_artefact_file_content_included_in_prompt(tmp_path: Path) -> None:
    """If check.path is set, that file's content must appear in the judge prompt."""
    artefact = tmp_path / "output.txt"
    artefact.write_text("ARTEFACT_CONTENT_UNIQUE_STRING", encoding="utf-8")

    check = Check(
        kind=CheckKind.judge,
        statement="The output file contains the expected greeting.",
        rationale="Human-readable quality check.",
        path="output.txt",
    )
    runner, captured = _capturing_runner()
    ctx = _ctx(tmp_path, runner=runner)
    verify_judge(check, ctx)

    assert "ARTEFACT_CONTENT_UNIQUE_STRING" in captured[0]


def test_artefact_file_missing_does_not_crash(tmp_path: Path) -> None:
    """A missing artefact file must not crash the verifier."""
    check = Check(
        kind=CheckKind.judge,
        statement="File must be present.",
        rationale="Quality check.",
        path="nonexistent.txt",
    )
    runner, _ = _capturing_runner()
    ctx = _ctx(tmp_path, runner=runner)
    result = verify_judge(check, ctx)
    # Should complete without exception; verdict depends on agent response.
    assert result.kind == CheckKind.judge


def test_artefact_not_found_message_in_prompt(tmp_path: Path) -> None:
    check = Check(
        kind=CheckKind.judge,
        statement="File must be present.",
        rationale="Quality check.",
        path="missing_file.txt",
    )
    runner, captured = _capturing_runner()
    ctx = _ctx(tmp_path, runner=runner)
    verify_judge(check, ctx)
    assert "missing_file.txt" in captured[0]


def test_no_path_prompt_says_no_artefacts(tmp_path: Path) -> None:
    runner, captured = _capturing_runner()
    ctx = _ctx(tmp_path, runner=runner)
    verify_judge(_JUDGE_CHECK, ctx)
    assert "No artefacts explicitly specified" in captured[0]


# ---------------------------------------------------------------------------
# Evidence and artefact file
# ---------------------------------------------------------------------------


def test_evidence_contains_verdict_and_reason(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, runner=_runner("pass", reason="file has 'result' key"))
    result = verify_judge(_JUDGE_CHECK, ctx)
    assert result.evidence["verdict"] == "pass"
    assert result.evidence["reason"] == "file has 'result' key"


def test_evidence_path_written_on_completion(tmp_path: Path) -> None:
    """A JSON artefact file must be written to run_dir/artifacts/ after the judge runs."""
    ctx = _ctx(tmp_path, runner=_runner("pass"))
    result = verify_judge(_JUDGE_CHECK, ctx)
    assert result.evidence_path is not None
    assert Path(result.evidence_path).exists()


def test_evidence_path_is_none_when_no_harness(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, harness=None, runner=None)
    result = verify_judge(_JUDGE_CHECK, ctx)
    assert result.evidence_path is None


def test_summary_includes_verdict(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, runner=_runner("pass", reason="criterion met"))
    result = verify_judge(_JUDGE_CHECK, ctx)
    assert "pass" in result.summary


def test_kind_is_judge(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, runner=_runner("fail"))
    result = verify_judge(_JUDGE_CHECK, ctx)
    assert result.kind == CheckKind.judge


def test_subtask_id_and_attempt_propagated(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, runner=_runner("pass"), subtask_id="task#3", attempt_no=2)
    result = verify_judge(_JUDGE_CHECK, ctx)
    assert result.subtask_id == "task#3"
    assert result.attempt_no == 2


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


def test_judge_kind_registered_in_default_registry() -> None:
    assert CheckKind.judge in _registry


def test_run_verify_dispatches_to_verify_judge(tmp_path: Path) -> None:
    """run_verify() must dispatch kind=judge to the registered judge verifier."""
    journal = Journal(tmp_path / "j.jsonl", "r1")
    ctx = _ctx(tmp_path, runner=_runner("pass"))

    result = run_verify(_JUDGE_CHECK, journal=journal, item_id="item1", ctx=ctx)

    assert result.verdict == Verdict.passed
    assert result.kind == CheckKind.judge


def test_run_verify_judge_journals_verification_entry(tmp_path: Path) -> None:
    """run_verify() must journal a VERIFICATION entry for kind=judge."""
    journal_path = tmp_path / "j.jsonl"
    journal = Journal(journal_path, "r1")
    ctx = _ctx(tmp_path, runner=_runner("fail"))

    run_verify(_JUDGE_CHECK, journal=journal, item_id="item1", ctx=ctx)

    entries = [json.loads(line) for line in journal_path.read_text().splitlines()]
    verif = [e for e in entries if e["event"] == VERIFICATION]
    assert len(verif) == 1
    assert verif[0]["payload"]["kind"] == "judge"
    assert verif[0]["payload"]["verdict"] == "failed"


# ---------------------------------------------------------------------------
# JSON inside markdown fence is still parsed
# ---------------------------------------------------------------------------


def test_response_in_markdown_fence_parsed(tmp_path: Path) -> None:
    """Agent responses wrapped in ```json ... ``` fences must still be parsed."""

    def _fenced_runner(
        prompt: str, *, cwd: Any, capabilities: Any, timeout_s: Any, harness: Any
    ) -> _FakeResult:
        payload = '{"verdict": "pass", "reason": "file found", "artefacts_shown": []}'
        return _FakeResult(exit_status=0, stdout=f"```json\n{payload}\n```")

    ctx = _ctx(tmp_path, runner=_fenced_runner)
    result = verify_judge(_JUDGE_CHECK, ctx)
    assert result.verdict == Verdict.passed
