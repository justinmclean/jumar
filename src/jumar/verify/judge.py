# SPDX-License-Identifier: Apache-2.0
"""Stage 6 verifier — kind=judge.

An independent agent that determines whether the acceptance criterion has been
met, given only artefacts from the world the subtask left behind and the
acceptance statement itself.  The executing agent's transcript is never passed
to the judge (AC6.4).

Contract:
- Fresh context: prompt is assembled from check.statement, check.rationale, and
  explicitly listed artefact files only.  Executor transcript is never included.
- Default-fail framing: the prompt tells the judge its default answer is FAIL
  and requires specific evidence to return "pass".
- Structured response, pinned by schema: JUDGE_VERDICT_SCHEMA is sent to the
  harness (claude --json-schema, the openai harness's response_format) and
  every reply is revalidated locally against it. An unparseable reply, an
  out-of-enum verdict, a missing or extra field or an empty reason is
  inconclusive, never a pass. A judge run that failed, or an endpoint that
  rejects the schema, stays inconclusive: the call is never retried without
  the schema.
- A pass has to agree with its own reason, checked mechanically: the reason
  must quote text that is present in the artefact the judge was shown, and
  must not be byte-identical to an earlier attempt's judge reason for the same
  subtask. Otherwise the pass is downgraded to inconclusive -- the
  identical-verdict rubber stamp, caught without asking another model.
- Returns inconclusive when no harness is configured in VerifyContext.
- Absent preconditions are decided mechanically, before any model call: a
  missing or empty artefact is ``failed`` (the work it should hold is not
  there), and an artefact that exists but cannot be read is ``inconclusive``
  -- unchecked, because it could not be shown to the judge. A model is never
  asked to rule on prose saying the evidence is missing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..clock import stamp
from ..harness import SCHEMA_HARNESSES
from ..harness import run_agent as _default_run_agent
from ..models import Check, CheckKind, Verdict, VerificationResult

if TYPE_CHECKING:
    from . import VerifyContext


_JUDGE_PROMPT_TEMPLATE = (
    "You are a strict, independent verifier. Your task is to determine whether"
    " a specific acceptance criterion has been met, based ONLY on the evidence"
    " presented below.\n\n"
    "IMPORTANT:\n"
    "- Your default answer is FAIL. You must cite specific, observable evidence"
    ' to return "pass".\n'
    '- "Looks reasonable" or "probably worked" is not evidence.\n'
    "- You have NOT been shown the executing agent's reasoning or transcript —"
    " only the world it left behind.\n\n"
    "Acceptance criterion:\n{statement}\n\n"
    "Why an independent judge is required (rationale):\n{rationale}\n\n"
    "Artefacts for review:\n{artefacts_block}\n\n"
    "Return ONLY valid JSON with this exact shape:\n"
    '{{"verdict": "pass"|"fail"|"inconclusive",'
    ' "reason": "<one line citing specific evidence; for pass, quote in double'
    ' quotes the exact text copied from the artefact that shows the criterion is met>",'
    ' "artefacts_shown": ["<list of artefact names you examined>"]}}\n\n'
    '- "pass": the criterion is clearly met; quote, verbatim and in double quotes,'
    " the artefact text that shows it. A pass whose reason quotes nothing from the"
    " artefact is not accepted.\n"
    '- "fail": the criterion is not met or evidence is absent.\n'
    '- "inconclusive": cannot evaluate from the available artefacts alone.'
)


JUDGE_VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "reason", "artefacts_shown"],
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail", "inconclusive"]},
        "reason": {"type": "string", "minLength": 1},
        "artefacts_shown": {"type": "array", "items": {"type": "string"}},
    },
}

_VERDICTS = {"pass": Verdict.passed, "fail": Verdict.failed, "inconclusive": Verdict.inconclusive}


def schema_violations(raw: Any) -> list[str]:
    """Revalidate a judge reply against JUDGE_VERDICT_SCHEMA; [] means valid.

    Local and exact, whatever the harness promised: a schema the harness
    could not enforce must not turn a malformed reply into a verdict.
    """
    if not isinstance(raw, dict):
        return ["reply is not a JSON object"]
    problems: list[str] = []
    required = JUDGE_VERDICT_SCHEMA["required"]
    for key in required:
        if key not in raw:
            problems.append(f"missing field {key!r}")
    for key in sorted(set(raw) - set(required)):
        problems.append(f"unexpected field {key!r}")
    verdict = raw.get("verdict")
    if "verdict" in raw and verdict not in _VERDICTS:
        problems.append(f"verdict {verdict!r} is not one of pass, fail, inconclusive")
    reason = raw.get("reason")
    if "reason" in raw and not (isinstance(reason, str) and reason.strip()):
        problems.append("reason is empty or not a string")
    shown = raw.get("artefacts_shown")
    if "artefacts_shown" in raw and not (
        isinstance(shown, list) and all(isinstance(a, str) for a in shown)
    ):
        problems.append("artefacts_shown is not a list of strings")
    return problems


def _collect_artefacts(check: Check, cwd: Path, head_bytes: int) -> tuple[str, list[str]]:
    """Return (artefacts_block, artefact_names).  Never reads the executor transcript."""
    if check.path is None:
        return "No artefacts explicitly specified.", []

    raw = check.path
    file_path = Path(raw) if Path(raw).is_absolute() else cwd / raw

    if not file_path.exists():
        return f"Artefact not found: {raw}", [raw]

    try:
        content = file_path.read_text(errors="replace")
    except OSError as exc:
        return f"Could not read artefact {raw}: {exc}", [raw]

    excerpt = content[:head_bytes]
    truncated = len(content) > head_bytes
    marker = f" (first {head_bytes} bytes shown)" if truncated else ""
    block = f"--- {raw}{marker} ---\n{excerpt}\n--- end {raw} ---"
    return block, [raw]


# Quoted spans in a reason: "...", “...”, `...`, '...'. At least three
# characters, so a quoted letter or a bare "ok" does not count as citing.
_QUOTED = re.compile(r'"([^"]{3,})"|“([^”]{3,})”|`([^`]{3,})`|\'([^\']{3,})\'')


def _squash(text: str) -> str:
    return " ".join(text.split())


# A model quoting a multi-line excerpt inside a JSON string often writes the
# line break as the two characters backslash-n. Read those escapes as the
# characters they stand for before comparing; the text must still match.
_QUOTE_ESCAPES = {"\\n": "\n", "\\t": "\t", "\\r": "\r", '\\"': '"'}


def _unescape(span: str) -> str:
    for escaped, char in _QUOTE_ESCAPES.items():
        span = span.replace(escaped, char)
    return span


def grounded_quotes(reason: str, shown: str) -> list[str]:
    """The quoted spans of *reason* that appear verbatim in *shown*.

    Whitespace is normalised on both sides, so a quote that re-wraps a line
    still counts. Nothing else is: a paraphrase is not a quote.
    """
    haystack = _squash(shown)
    found: list[str] = []
    for match in _QUOTED.finditer(reason):
        span = _squash(_unescape(next(g for g in match.groups() if g is not None)))
        if len(span) >= 3 and span in haystack:
            found.append(span)
    return found


def _shown_excerpt(check: Check, ctx: VerifyContext) -> str:
    """Exactly the artefact text the judge was shown (the same head as the prompt)."""
    assert check.path is not None
    file_path = Path(check.path) if Path(check.path).is_absolute() else ctx.cwd / check.path
    try:
        return file_path.read_text(errors="replace")[: ctx.evidence_head_bytes]
    except OSError:
        return ""


def _earlier_reasons(ctx: VerifyContext) -> dict[int, str]:
    """attempt_no -> judge reason, for this subtask's earlier attempts in this run."""
    artefacts_dir = ctx.run_dir / "artifacts"
    safe_id = ctx.subtask_id.replace("#", "-")
    reasons: dict[int, str] = {}
    for n in range(ctx.attempt_no):
        path = artefacts_dir / f"{safe_id}-judge-{n}.json"
        try:
            recorded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(recorded, dict) and isinstance(recorded.get("reason"), str):
            reasons[n] = recorded["reason"]
    return reasons


def _parse_whole(text: str) -> dict[str, Any] | None:
    """The whole reply as one JSON object, or None."""
    try:
        result = json.loads(text.strip())
    except json.JSONDecodeError:
        return None
    return result if isinstance(result, dict) else None


def _extract_verdict(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from agent output."""
    text = text.strip()
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass
    for m in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", text):
        try:
            result = json.loads(m.group(1).strip())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    if start >= 0:
        try:
            result = json.loads(text[start:])
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
    return None


def build_judge_prompt(check: Check, ctx: VerifyContext) -> tuple[str, list[str]]:
    """Return (prompt, artefact_names) for one judge invocation.

    Exported so tests can inspect the assembled prompt without launching an
    agent (AC6.4 assertion helper).  This function never reads any executor
    transcript file.
    """
    artefacts_block, artefact_names = _collect_artefacts(check, ctx.cwd, ctx.evidence_head_bytes)
    prompt = _JUDGE_PROMPT_TEMPLATE.format(
        statement=check.statement,
        rationale=check.rationale or "(no rationale provided)",
        artefacts_block=artefacts_block,
    )
    return prompt, artefact_names


def _artefact_precondition(check: Check, ctx: VerifyContext) -> VerificationResult | None:
    """Decide a missing, empty or unreadable artefact without a model call.

    Returns None when the artefact is there and readable, so the judge runs.
    """
    assert check.path is not None
    raw = check.path
    file_path = Path(raw) if Path(raw).is_absolute() else ctx.cwd / raw
    checked_at = stamp()

    def _result(verdict: Verdict, error: str, summary: str, **extra: Any) -> VerificationResult:
        return VerificationResult(
            subtask_id=ctx.subtask_id,
            attempt_no=ctx.attempt_no,
            verdict=verdict,
            kind=CheckKind.judge,
            evidence={"error": error, "path": raw, "model_called": False, **extra},
            summary=summary,
            evidence_path=None,
            checked_at=checked_at,
        )

    if not file_path.exists():
        return _result(
            Verdict.failed,
            "artefact_missing",
            f"judge verifier: artefact {raw!r} does not exist; failed without asking the judge",
        )
    try:
        content = file_path.read_bytes()
    except OSError as exc:
        return _result(
            Verdict.inconclusive,
            "artefact_unreadable",
            f"judge verifier: unchecked, because artefact {raw!r} could not be read ({exc})",
            reason=str(exc),
        )
    if not content:
        return _result(
            Verdict.failed,
            "artefact_empty",
            f"judge verifier: artefact {raw!r} is empty; failed without asking the judge",
            size_bytes=0,
        )
    return None


def verify_judge(check: Check, ctx: VerifyContext) -> VerificationResult:
    """Run an adversarial judge agent and return a VerificationResult.

    Fresh context: prompt assembled from check.statement, check.rationale, and
    explicitly listed artefact files only.  The executing agent's transcript is
    never read or passed to the judge (AC6.4).

    Returns inconclusive if ctx.harness is None or if the agent's response
    cannot be parsed as structured JSON.  Inconclusive is never a pass.
    """
    assert check.kind is CheckKind.judge, "verify_judge called with non-judge check"

    checked_at = stamp()

    if ctx.harness is None:
        return VerificationResult(
            subtask_id=ctx.subtask_id,
            attempt_no=ctx.attempt_no,
            verdict=Verdict.inconclusive,
            kind=CheckKind.judge,
            evidence={"error": "no_harness_configured"},
            summary="judge verifier: no harness configured in VerifyContext",
            evidence_path=None,
            checked_at=checked_at,
        )

    if check.path is None:
        # Defence in depth behind the kind=judge path invariant in models.py.
        # The judge runs with no capabilities and sees only check.path, so a
        # pathless check cannot be answered from evidence -- the model would
        # be guessing. Say so instead of spending a call to be told "fail".
        # Reachable only for a plan built before that invariant landed, or by
        # a caller that bypasses Check construction.
        return VerificationResult(
            subtask_id=ctx.subtask_id,
            attempt_no=ctx.attempt_no,
            verdict=Verdict.inconclusive,
            kind=CheckKind.judge,
            evidence={
                "error": "no_artefact_path",
                "reason": (
                    "kind=judge check names no artefact path; the judge has no "
                    "read_fs or run_commands capability and can only be shown "
                    "check.path, so this check can never pass"
                ),
            },
            summary="judge verifier: check names no artefact path, not run",
            evidence_path=None,
            checked_at=checked_at,
        )

    precondition = _artefact_precondition(check, ctx)
    if precondition is not None:
        return precondition

    prompt, artefact_names = build_judge_prompt(check, ctx)

    runner = ctx.judge_run_agent if ctx.judge_run_agent is not None else _default_run_agent

    result = runner(
        prompt,
        cwd=ctx.cwd,
        capabilities=frozenset(),
        timeout_s=ctx.judge_timeout_s,
        harness=ctx.harness,
        response_schema=JUDGE_VERDICT_SCHEMA,
    )
    schema_enforced = ctx.harness.harness in SCHEMA_HARNESSES

    def _inconclusive(error: str, summary: str, **extra: Any) -> VerificationResult:
        return VerificationResult(
            subtask_id=ctx.subtask_id,
            attempt_no=ctx.attempt_no,
            verdict=Verdict.inconclusive,
            kind=CheckKind.judge,
            evidence={
                "error": error,
                "raw_head": result.stdout[:500],
                "artefacts_shown": artefact_names,
                "schema_enforced": schema_enforced,
                **extra,
            },
            summary=summary,
            evidence_path=None,
            checked_at=checked_at,
        )

    # A judge run that did not finish cleanly has no verdict to read, however
    # its output looks. Never fall back to a looser call.
    if result.timed_out or result.exit_status not in (0, None):
        return _inconclusive(
            "judge_run_failed",
            "judge verifier: the judge run did not finish cleanly "
            f"(exit_status={result.exit_status}, timed_out={result.timed_out})",
            stderr_head=(result.stderr or "")[:500],
        )

    # With the schema enforced the reply IS the object: no scraping for the
    # first "{", which can land on an example the model quoted.
    raw = _parse_whole(result.stdout) if schema_enforced else _extract_verdict(result.stdout)
    if raw is None:
        return _inconclusive(
            "unparseable_response", "judge verifier: could not parse structured response from agent"
        )
    violations = schema_violations(raw)
    if violations:
        return _inconclusive(
            "schema_violation",
            "judge verifier: reply does not match the verdict schema: " + "; ".join(violations),
            violations=violations,
        )

    verdict_str = str(raw["verdict"])
    reason = str(raw["reason"]).strip()
    shown = raw["artefacts_shown"]
    verdict = _VERDICTS[verdict_str]
    summary = f"judge verdict={verdict_str}: {reason}"

    # A pass must agree with its own reason (W20). Both tests are mechanical:
    # no second model is asked whether the first one was right.
    downgrade: dict[str, Any] = {}
    if verdict is Verdict.passed:
        quotes = grounded_quotes(reason, _shown_excerpt(check, ctx))
        repeated = [n for n, earlier in _earlier_reasons(ctx).items() if earlier == reason]
        if not quotes:
            downgrade = {
                "downgraded": "reason_not_grounded",
                "detail": "the pass reason quotes no text present in the artefact shown",
            }
        elif repeated:
            downgrade = {
                "downgraded": "reason_repeated",
                "detail": f"the pass reason is identical to attempt {max(repeated)}'s",
                "repeats_attempt": max(repeated),
            }
        else:
            downgrade = {"grounded_quotes": quotes}
        if "downgraded" in downgrade:
            verdict = Verdict.inconclusive
            summary = (
                f"judge said pass, downgraded to inconclusive: {downgrade['detail']} ({reason})"
            )

    artefacts_dir = ctx.run_dir / "artifacts"
    artefacts_dir.mkdir(parents=True, exist_ok=True)
    safe_id = ctx.subtask_id.replace("#", "-")
    artefact_file = artefacts_dir / f"{safe_id}-judge-{ctx.attempt_no}.json"
    artefact_file.write_text(
        json.dumps(
            {
                "verdict": verdict_str,
                "reason": reason,
                "artefacts_shown": shown,
                "raw_response": result.stdout,
                **downgrade,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return VerificationResult(
        subtask_id=ctx.subtask_id,
        attempt_no=ctx.attempt_no,
        verdict=verdict,
        kind=CheckKind.judge,
        evidence={
            "verdict": verdict_str,
            "reason": reason,
            "artefacts_shown": shown,
            "schema_enforced": schema_enforced,
            **downgrade,
            "completion_tokens": getattr(result, "completion_tokens", None),
            "prompt_tokens": getattr(result, "prompt_tokens", None),
        },
        summary=summary,
        evidence_path=str(artefact_file),
        checked_at=checked_at,
    )
