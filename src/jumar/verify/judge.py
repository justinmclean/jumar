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
- Structured response: agent must return {"verdict", "reason", "artefacts_shown"};
  any unparseable response is inconclusive (not a pass).
- Returns inconclusive when no harness is configured in VerifyContext.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..clock import stamp
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
    ' "reason": "<one-line explanation citing specific evidence>",'
    ' "artefacts_shown": ["<list of artefact names you examined>"]}}\n\n'
    '- "pass": the criterion is clearly met; cite the specific evidence.\n'
    '- "fail": the criterion is not met or evidence is absent.\n'
    '- "inconclusive": cannot evaluate from the available artefacts alone.'
)


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

    prompt, artefact_names = build_judge_prompt(check, ctx)

    runner = ctx.judge_run_agent if ctx.judge_run_agent is not None else _default_run_agent

    result = runner(
        prompt,
        cwd=ctx.cwd,
        capabilities=frozenset(),
        timeout_s=ctx.judge_timeout_s,
        harness=ctx.harness,
    )

    raw = _extract_verdict(result.stdout)
    if raw is None or "verdict" not in raw:
        return VerificationResult(
            subtask_id=ctx.subtask_id,
            attempt_no=ctx.attempt_no,
            verdict=Verdict.inconclusive,
            kind=CheckKind.judge,
            evidence={
                "error": "unparseable_response",
                "raw_head": result.stdout[:500],
                "artefacts_shown": artefact_names,
            },
            summary="judge verifier: could not parse structured response from agent",
            evidence_path=None,
            checked_at=checked_at,
        )

    verdict_str = str(raw.get("verdict", "")).lower()
    reason = str(raw.get("reason", ""))
    shown = raw.get("artefacts_shown", artefact_names)

    if verdict_str == "pass":
        verdict = Verdict.passed
    elif verdict_str == "inconclusive":
        verdict = Verdict.inconclusive
    else:
        # Default to failed — anything other than explicit "pass" or "inconclusive" fails.
        verdict = Verdict.failed

    summary = f"judge verdict={verdict_str}: {reason}" if reason else f"judge verdict={verdict_str}"

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
            "completion_tokens": getattr(result, "completion_tokens", None),
            "prompt_tokens": getattr(result, "prompt_tokens", None),
        },
        summary=summary,
        evidence_path=str(artefact_file),
        checked_at=checked_at,
    )
