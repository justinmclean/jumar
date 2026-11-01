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

import pytest

from jumar.journal import VERIFICATION, Journal
from jumar.models import Check, CheckKind, HarnessInfo, Verdict
from jumar.verify import VerifyContext, _registry, run_verify
from jumar.verify.judge import build_judge_prompt, verify_judge

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
    path="output.json",
)


def _without_path(check: Check) -> Check:
    """Return *check* with path stripped, bypassing the Check invariant.

    kind=judge requires a path (models.py), and dataclasses.replace re-runs
    __post_init__, so a pathless judge Check cannot be built the normal way.
    These tests cover the paths that still have to cope with one: a plan
    pickled or journalled before that invariant landed, and _collect_artefacts,
    which is exported and does not enforce it.
    """
    import copy

    stripped = copy.copy(check)
    object.__setattr__(stripped, "path", None)
    return stripped


def _ctx(
    tmp_path: Path,
    *,
    harness: HarnessInfo | None = _HARNESS,
    runner: Any | None = None,
    subtask_id: str = "item#0",
    attempt_no: int = 0,
    artefact: str | None = '{"result": 1}\n',
) -> VerifyContext:
    # _JUDGE_CHECK names output.json. Write it, so tests about the judge's
    # response handling run against an artefact that exists; a missing
    # artefact is decided before any model call (W18) and tested on its own.
    if artefact is not None and not (tmp_path / "output.json").exists():
        (tmp_path / "output.json").write_text(artefact)
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
        prompt: str, *, cwd: Any, capabilities: Any, timeout_s: Any, harness: Any, **_: Any
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
        prompt: str, *, cwd: Any, capabilities: Any, timeout_s: Any, harness: Any, **_: Any
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
        prompt: str, *, cwd: Any, capabilities: Any, timeout_s: Any, harness: Any, **_: Any
    ) -> _FakeResult:
        return _FakeResult(exit_status=0, stdout="I cannot determine this.")

    ctx = _ctx(tmp_path, runner=_garbage_runner)
    result = verify_judge(_JUDGE_CHECK, ctx)
    assert result.verdict == Verdict.inconclusive


def test_unparseable_response_is_not_passed(tmp_path: Path) -> None:
    def _garbage_runner(
        prompt: str, *, cwd: Any, capabilities: Any, timeout_s: Any, harness: Any, **_: Any
    ) -> _FakeResult:
        return _FakeResult(exit_status=0, stdout="looks good to me!")

    ctx = _ctx(tmp_path, runner=_garbage_runner)
    result = verify_judge(_JUDGE_CHECK, ctx)
    assert result.verdict is not Verdict.passed


def test_an_out_of_enum_verdict_is_inconclusive(tmp_path: Path) -> None:
    """W19: a verdict outside pass/fail/inconclusive is a schema failure, not a verdict."""
    ctx = _ctx(tmp_path, runner=_runner("maybe"))
    result = verify_judge(_JUDGE_CHECK, ctx)
    assert result.verdict == Verdict.inconclusive
    assert result.evidence["error"] == "schema_violation"
    assert any("'maybe'" in v for v in result.evidence["violations"])


def test_empty_stdout_yields_inconclusive(tmp_path: Path) -> None:
    def _empty_runner(
        prompt: str, *, cwd: Any, capabilities: Any, timeout_s: Any, harness: Any, **_: Any
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


def test_a_missing_artefact_fails_without_a_model_call(tmp_path: Path) -> None:
    """W18: the judge is never asked to rule on prose saying the evidence is missing."""
    check = Check(
        kind=CheckKind.judge,
        statement="File must be present.",
        rationale="Quality check.",
        path="missing_file.txt",
    )
    runner, captured = _capturing_runner()
    result = verify_judge(check, _ctx(tmp_path, runner=runner))
    assert captured == []
    assert result.verdict == Verdict.failed
    assert result.evidence["error"] == "artefact_missing"
    assert result.evidence["path"] == "missing_file.txt"
    assert result.evidence["model_called"] is False


def test_an_empty_artefact_fails_without_a_model_call(tmp_path: Path) -> None:
    (tmp_path / "output.json").write_bytes(b"")
    runner, captured = _capturing_runner()
    result = verify_judge(_JUDGE_CHECK, _ctx(tmp_path, runner=runner))
    assert captured == []
    assert result.verdict == Verdict.failed
    assert result.evidence["error"] == "artefact_empty"


def test_an_unreadable_artefact_is_unchecked_without_a_model_call(tmp_path: Path) -> None:
    """Present but unreadable: not a pass, not a fail -- unchecked, with the reason."""
    (tmp_path / "output.json").mkdir()  # exists, but read_bytes raises
    runner, captured = _capturing_runner()
    result = verify_judge(_JUDGE_CHECK, _ctx(tmp_path, runner=runner))
    assert captured == []
    assert result.verdict == Verdict.inconclusive
    assert result.evidence["error"] == "artefact_unreadable"
    assert "unchecked, because" in result.summary


def test_a_present_artefact_still_reaches_the_judge(tmp_path: Path) -> None:
    runner, captured = _capturing_runner()
    verify_judge(_JUDGE_CHECK, _ctx(tmp_path, runner=runner))
    assert len(captured) == 1
    assert '{"result": 1}' in captured[0]


def test_collect_artefacts_says_no_artefacts_when_path_is_none(tmp_path: Path) -> None:
    """_collect_artefacts is a pure function and still handles path=None.

    kind=judge now requires a path (models.Check), so verify_judge short-
    circuits before this branch is reached — see the test below. The branch
    is kept, and covered here directly, because _collect_artefacts is
    exported and does not itself enforce the Check invariant.
    """
    from jumar.verify.judge import _collect_artefacts

    check = Check(
        kind=CheckKind.judge,
        statement="Something is true.",
        rationale="Quality check.",
        path="present.txt",
    )
    pathless = _without_path(check)
    block, names = _collect_artefacts(pathless, tmp_path, 4096)
    assert "No artefacts explicitly specified" in block
    assert names == []


def test_pathless_judge_check_is_inconclusive_without_a_model_call(
    tmp_path: Path,
) -> None:
    """A judge check with no artefact path must not spend an agent call.

    The judge runs with capabilities=frozenset() and is shown only
    check.path, so a pathless check can never pass. On 6 Sep 2026 two items
    burned their whole repair budget being told "fail" by a judge that had
    been given nothing to read. Reachable only for a plan built before the
    kind=judge path invariant landed, hence the dataclasses.replace here.
    """
    check = _without_path(_JUDGE_CHECK)
    runner, captured = _capturing_runner()
    ctx = _ctx(tmp_path, runner=runner)

    result = verify_judge(check, ctx)

    assert captured == [], "no agent call should have been made"
    assert result.verdict is Verdict.inconclusive
    assert result.evidence["error"] == "no_artefact_path"


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


_NO_SCHEMA_HARNESS = HarnessInfo(agent="codex", model="", harness="codex", invoked_as="codex")


def test_response_in_markdown_fence_parsed(tmp_path: Path) -> None:
    """A harness that cannot enforce the schema may wrap its reply in a fence."""

    def _fenced_runner(
        prompt: str, *, cwd: Any, capabilities: Any, timeout_s: Any, harness: Any, **_: Any
    ) -> _FakeResult:
        payload = '{"verdict": "pass", "reason": "file found", "artefacts_shown": []}'
        return _FakeResult(exit_status=0, stdout=f"```json\n{payload}\n```")

    ctx = _ctx(tmp_path, runner=_fenced_runner, harness=_NO_SCHEMA_HARNESS)
    result = verify_judge(_JUDGE_CHECK, ctx)
    assert result.verdict == Verdict.passed
    assert result.evidence["schema_enforced"] is False


def test_invalid_json_in_fence_falls_through(tmp_path: Path) -> None:
    """A code-fence block with broken JSON is skipped; the result is inconclusive."""

    def _bad_fence_runner(
        prompt: str, *, cwd: Any, capabilities: Any, timeout_s: Any, harness: Any, **_: Any
    ) -> _FakeResult:
        return _FakeResult(exit_status=0, stdout="```json\n{not valid json}\n```")

    ctx = _ctx(tmp_path, runner=_bad_fence_runner)
    result = verify_judge(_JUDGE_CHECK, ctx)
    assert result.verdict == Verdict.inconclusive


def test_bare_json_after_text_prefix_parsed(tmp_path: Path) -> None:
    """Without schema enforcement, JSON after prefix text is found by brace search."""

    def _prefixed_runner(
        prompt: str, *, cwd: Any, capabilities: Any, timeout_s: Any, harness: Any, **_: Any
    ) -> _FakeResult:
        payload = '{"verdict": "pass", "reason": "looks good", "artefacts_shown": []}'
        return _FakeResult(exit_status=0, stdout=f"The result is: {payload}")

    ctx = _ctx(tmp_path, runner=_prefixed_runner, harness=_NO_SCHEMA_HARNESS)
    result = verify_judge(_JUDGE_CHECK, ctx)
    assert result.verdict == Verdict.passed


def test_with_the_schema_enforced_text_around_the_object_is_not_scraped(
    tmp_path: Path,
) -> None:
    """W19: claude prints exactly the object; anything else is not a verdict."""

    def _prefixed_runner(prompt: str, **_: Any) -> _FakeResult:
        payload = '{"verdict": "pass", "reason": "looks good", "artefacts_shown": []}'
        return _FakeResult(exit_status=0, stdout=f"Example reply: {payload}")

    result = verify_judge(_JUDGE_CHECK, _ctx(tmp_path, runner=_prefixed_runner))
    assert result.verdict == Verdict.inconclusive
    assert result.evidence["error"] == "unparseable_response"
    assert result.evidence["schema_enforced"] is True


def test_brace_search_with_broken_json_is_inconclusive(tmp_path: Path) -> None:
    """Brace found but JSON parse fails → inconclusive."""

    def _broken_brace_runner(
        prompt: str, *, cwd: Any, capabilities: Any, timeout_s: Any, harness: Any, **_: Any
    ) -> _FakeResult:
        return _FakeResult(exit_status=0, stdout="result: { broken json here }")

    ctx = _ctx(tmp_path, runner=_broken_brace_runner)
    result = verify_judge(_JUDGE_CHECK, ctx)
    assert result.verdict == Verdict.inconclusive


def test_artefact_read_oserror_does_not_crash(tmp_path: Path) -> None:
    """If the artefact path is a directory (read_text raises OSError), the verifier continues."""
    # Create a directory at the artefact path — read_text() on a directory raises IsADirectoryError.
    artefact_dir = tmp_path / "mydir"
    artefact_dir.mkdir()

    check = Check(
        kind=CheckKind.judge,
        statement="Artefact is readable.",
        rationale="Check content.",
        path="mydir",
    )
    runner, _ = _capturing_runner()
    ctx = _ctx(tmp_path, runner=runner)
    result = verify_judge(check, ctx)
    assert result.kind == CheckKind.judge  # did not crash


# ---------------------------------------------------------------------------
# W19 — the verdict schema is sent, and every reply is revalidated locally
# ---------------------------------------------------------------------------


def _replying(payload: object, *, exit_status: int = 0, timed_out: bool = False) -> Any:
    def _run(prompt: str, **_: Any) -> _FakeResult:
        stdout = payload if isinstance(payload, str) else json.dumps(payload)
        return _FakeResult(exit_status=exit_status, stdout=stdout, timed_out=timed_out)

    return _run


@pytest.mark.parametrize(
    ("reply", "violation"),
    [
        ({"verdict": "pass", "reason": "", "artefacts_shown": []}, "reason is empty"),
        ({"verdict": "pass", "reason": "   ", "artefacts_shown": []}, "reason is empty"),
        ({"verdict": "pass", "artefacts_shown": []}, "missing field 'reason'"),
        ({"verdict": "pass", "reason": "ok"}, "missing field 'artefacts_shown'"),
        ({"reason": "ok", "artefacts_shown": []}, "missing field 'verdict'"),
        ({"verdict": "PASS", "reason": "ok", "artefacts_shown": []}, "'PASS'"),
        ({"verdict": "yes", "reason": "ok", "artefacts_shown": []}, "'yes'"),
        ({"verdict": "pass", "reason": "ok", "artefacts_shown": "x"}, "artefacts_shown"),
        ({"verdict": "pass", "reason": "ok", "artefacts_shown": [], "confidence": 1}, "unexpected"),
        ([{"verdict": "pass"}], "not a JSON object"),
    ],
)
def test_a_reply_that_breaks_the_schema_is_inconclusive(
    tmp_path: Path, reply: object, violation: str
) -> None:
    result = verify_judge(_JUDGE_CHECK, _ctx(tmp_path, runner=_replying(reply)))
    assert result.verdict == Verdict.inconclusive
    if result.evidence["error"] == "schema_violation":
        assert any(violation in v for v in result.evidence["violations"])
    else:  # a top-level array never parses as the object
        assert result.evidence["error"] == "unparseable_response"


def test_an_empty_reason_never_passes_even_with_a_pass_verdict(tmp_path: Path) -> None:
    reply = {"verdict": "pass", "reason": "", "artefacts_shown": []}
    result = verify_judge(_JUDGE_CHECK, _ctx(tmp_path, runner=_replying(reply)))
    assert result.verdict != Verdict.passed


def test_a_valid_reply_still_passes(tmp_path: Path) -> None:
    reply = {"verdict": "pass", "reason": "output.json has a result key", "artefacts_shown": []}
    result = verify_judge(_JUDGE_CHECK, _ctx(tmp_path, runner=_replying(reply)))
    assert result.verdict == Verdict.passed
    assert result.evidence["schema_enforced"] is True


@pytest.mark.parametrize("result_kw", [{"exit_status": 1}, {"exit_status": -1, "timed_out": True}])
def test_a_judge_run_that_failed_is_inconclusive_whatever_it_printed(
    tmp_path: Path, result_kw: dict[str, Any]
) -> None:
    reply = {"verdict": "pass", "reason": "fine", "artefacts_shown": []}
    result = verify_judge(_JUDGE_CHECK, _ctx(tmp_path, runner=_replying(reply, **result_kw)))
    assert result.verdict == Verdict.inconclusive
    assert result.evidence["error"] == "judge_run_failed"


def test_the_schema_is_sent_to_the_runner(tmp_path: Path) -> None:
    from jumar.verify.judge import JUDGE_VERDICT_SCHEMA

    seen: dict[str, Any] = {}

    def _run(prompt: str, **kw: Any) -> _FakeResult:
        seen.update(kw)
        return _FakeResult(
            exit_status=0,
            stdout=json.dumps({"verdict": "fail", "reason": "no key", "artefacts_shown": []}),
        )

    verify_judge(_JUDGE_CHECK, _ctx(tmp_path, runner=_run))
    assert seen["response_schema"] == JUDGE_VERDICT_SCHEMA
    schema = seen["response_schema"]
    assert schema["properties"]["verdict"]["enum"] == ["pass", "fail", "inconclusive"]
    assert set(schema["required"]) == {"verdict", "reason", "artefacts_shown"}
    assert schema["additionalProperties"] is False
    assert schema["properties"]["reason"]["minLength"] == 1


def test_claude_argv_carries_the_schema_as_json_schema() -> None:
    from jumar.harness import build_argv
    from jumar.verify.judge import JUDGE_VERDICT_SCHEMA

    argv = build_argv(
        agent_bin="claude",
        harness_name="claude",
        model=None,
        capabilities=frozenset(),
        prompt="x",
        response_schema=JUDGE_VERDICT_SCHEMA,
    )
    assert json.loads(argv[argv.index("--json-schema") + 1]) == JUDGE_VERDICT_SCHEMA
    plain = build_argv(
        agent_bin="claude", harness_name="claude", model=None, capabilities=frozenset(), prompt="x"
    )
    assert "--json-schema" not in plain


def test_an_endpoint_that_rejects_the_schema_leaves_the_judge_inconclusive(
    tmp_path: Path,
) -> None:
    """Never fail open: one request, with the schema, and no retry without it."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    received: list[dict[str, Any]] = []

    class _Rejecting(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            received.append(json.loads(body))
            msg = b'{"error": {"message": "response_format json_schema is not supported"}}'
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), _Rejecting)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        host, port = server.server_address[:2]
        harness = HarnessInfo(
            agent="openai",
            model="m",
            harness="openai",
            invoked_as="openai",
            base_url=f"http://{host}:{port}/v1",
        )
        # judge_run_agent=None: the real harness.run_agent dispatch.
        ctx = _ctx(tmp_path, harness=harness, runner=None)
        result = verify_judge(_JUDGE_CHECK, ctx)
    finally:
        server.shutdown()

    assert result.verdict == Verdict.inconclusive
    assert result.evidence["error"] == "judge_run_failed"
    assert len(received) == 1
    assert received[0]["response_format"]["type"] == "json_schema"
    assert received[0]["response_format"]["json_schema"]["strict"] is True
