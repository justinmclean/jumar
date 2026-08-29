# SPDX-License-Identifier: Apache-2.0
"""Stage 5 — Execute: run exactly one subtask via the agent harness.

Public API
----------
execute()  – dispatch one subtask, journal attempt_started + attempt_finished,
             and return an Attempt.  Never marks a subtask successful — the
             verdict comes from Stage 6 (verify).

The agent's own "I'm done" is stored as ``agent_claim`` on the attempt, never
as the outcome.  Only a passing verification produces a passed subtask (AC5.5).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .clock import stamp
from .config import Config, resolve_harness
from .harness import AgentResult, detect_harness_error
from .harness import run_agent as _default_run_agent
from .journal import ATTEMPT_FINISHED, ATTEMPT_STARTED, HARNESS_ERROR, Journal
from .models import Attempt, HarnessInfo, Subtask, TodoItem, VerificationResult

# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def _prior_evidence_block(prior_evidence: list[VerificationResult]) -> str:
    """Format prior subtask evidence for the execution prompt (AC5.3)."""
    if not prior_evidence:
        return ""
    lines = ["\nVerified prior subtasks (evidence from earlier steps):"]
    for vr in prior_evidence:
        lines.append(f"\n  [{vr.subtask_id}] {vr.verdict.value} — {vr.summary}")
        ev = vr.evidence
        stdout_head = str(ev.get("stdout_head", ""))
        if stdout_head:
            lines.append(f"    stdout: {stdout_head[:200]}")
    return "\n".join(lines)


def _build_prompt(
    subtask: Subtask,
    item: TodoItem,
    prior_evidence: list[VerificationResult],
) -> str:
    ctx_block = ("\nContext:\n" + "\n".join(item.context)) if item.context else ""
    evidence_block = _prior_evidence_block(prior_evidence)
    return (
        "You are executing exactly one subtask of a larger todo item.\n\n"
        f"Todo item: {item.text}{ctx_block}\n\n"
        f"Subtask {subtask.index + 1}: {subtask.description}\n\n"
        "Execute ONLY this subtask. Do not proceed to subsequent subtasks."
        f"{evidence_block}\n\n"
        "When done, summarise what you did in one sentence."
    )


# ---------------------------------------------------------------------------
# Helper: best-effort list of files touched by git
# ---------------------------------------------------------------------------


def _files_touched(cwd: Path) -> tuple[str, ...]:
    try:
        proc = subprocess.run(  # noqa: S603
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
            check=False,
        )
        files: list[str] = []
        for line in proc.stdout.splitlines():
            if line.strip():
                files.append(line[3:].strip())
        return tuple(files)
    except (OSError, subprocess.TimeoutExpired):
        return ()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def execute(
    subtask: Subtask,
    *,
    item: TodoItem,
    prior_evidence: list[VerificationResult],
    config: Config,
    journal: Journal,
    cwd: Path,
    run_dir: Path,
    attempt_no: int = 0,
    session_id: str | None = None,
    session_is_new: bool = False,
    _run_agent: Any | None = None,
    prompt_override: str | None = None,
) -> Attempt:
    """Execute *subtask* via the agent harness.

    Journals ``attempt_started`` before the harness is invoked and
    ``attempt_finished`` after it returns (AC5.4).  Returns an :class:`Attempt`
    whose ``agent_claim`` is the last non-empty stdout line — a claim only,
    never a verdict (AC5.5).

    Parameters
    ----------
    subtask:         The one subtask to execute.
    item:            Parent todo item (used to build the prompt context).
    prior_evidence:  Verified results from earlier subtasks (AC5.3).
    config:          Resolved run config (harness, timeout, …).
    journal:         Append-only run journal.
    cwd:             Working directory for the agent subprocess.
    run_dir:         Directory for artefact files (transcripts).
    attempt_no:      0 for the initial attempt; 1..n for repairs.
    session_id:      When set, passed to the harness so all of an item's
                     subtasks share one conversation.  ``session_is_new=True``
                     creates the session (``--session-id <id>`` — first subtask
                     only); False resumes it (``--resume <id>``).  None for
                     harnesses that do not support session resumption.
    _run_agent:      Injectable harness callable for tests (default: harness.run_agent).
    prompt_override: When set, replaces the default prompt (used by repair.py).
    """
    runner = _run_agent if _run_agent is not None else _default_run_agent

    resolved = resolve_harness(config, item.meta.get("harness")).for_stage("execute")
    harness_info = HarnessInfo(
        agent=resolved.agent,
        model=resolved.model,
        harness=resolved.agent,
        invoked_as=resolved.agent,
        base_url=resolved.base_url,
        api_key_env=resolved.api_key_env,
        commands_allow=config.commands.allow,
        commands_deny=config.commands.deny,
    )

    prompt = (
        prompt_override
        if prompt_override is not None
        else _build_prompt(subtask, item, prior_evidence)
    )
    started_at = stamp()

    # Journal attempt_started BEFORE the agent runs (AC5.4).
    journal.append(
        ATTEMPT_STARTED,
        subtask_id=subtask.subtask_id,
        item_id=item.item_id,
        payload={
            "attempt_no": attempt_no,
            "started_at": started_at,
            "harness": {
                "agent": harness_info.agent,
                "model": harness_info.model,
            },
        },
    )

    result: AgentResult = runner(
        prompt,
        cwd=cwd,
        capabilities=subtask.capabilities,
        timeout_s=config.subtask_timeout_s,
        harness=harness_info,
        session_id=session_id,
        session_is_new=session_is_new,
    )

    finished_at = stamp()
    touched = _files_touched(cwd)

    # A harness-level outage (usage limit, auth failure, missing binary) is
    # not the agent failing the subtask — the CLI never ran the prompt.
    # Journal it so the evidence is inspectable; see harness.detect_harness_error.
    harness_error = detect_harness_error(result)
    if harness_error is not None:
        journal.append(
            HARNESS_ERROR,
            subtask_id=subtask.subtask_id,
            item_id=item.item_id,
            payload={
                "stage": "execute",
                "attempt_no": attempt_no,
                "reason": harness_error,
                "exit_status": result.exit_status,
                "agent_stdout_head": result.stdout[:500],
                "agent_stderr_head": result.stderr[:500],
            },
        )

    # Write agent transcript as an artefact file.
    artefacts_dir = run_dir / "artifacts"
    artefacts_dir.mkdir(parents=True, exist_ok=True)
    transcript_name = f"{subtask.subtask_id.replace('#', '-')}-attempt-{attempt_no}.txt"
    transcript_path = artefacts_dir / transcript_name
    transcript_path.write_text(
        f"=== stdout ===\n{result.stdout}\n=== stderr ===\n{result.stderr}\n",
        encoding="utf-8",
    )

    error: str | None = None
    if harness_error is not None:
        error = "harness_error"
    elif result.timed_out:
        error = "timed_out"
    elif result.exit_status not in (0, None):
        error = f"exit_status={result.exit_status}"

    # Throughput, when the harness measured it. getattr rather than attribute
    # access: only the in-process harness populates these, and a caller may
    # supply any object satisfying the AgentResult contract.
    tokens: int | None = getattr(result, "completion_tokens", None)
    prompt_tokens: int | None = getattr(result, "prompt_tokens", None)
    gen_s: float | None = getattr(result, "generation_seconds", None)

    journal.append(
        ATTEMPT_FINISHED,
        subtask_id=subtask.subtask_id,
        item_id=item.item_id,
        payload={
            "attempt_no": attempt_no,
            "finished_at": finished_at,
            "exit_status": result.exit_status,
            "timed_out": result.timed_out,
            "agent_claim": result.agent_claim,
            "completion_tokens": tokens,
            "prompt_tokens": prompt_tokens,
            "generation_seconds": round(gen_s, 1) if gen_s is not None else None,
            "tokens_per_second": round(tokens / gen_s, 1) if tokens and gen_s else None,
            "files_touched": list(touched),
            "error": error,
        },
    )

    return Attempt(
        attempt_no=attempt_no,
        started_at=started_at,
        finished_at=finished_at,
        harness=harness_info,
        agent_claim=result.agent_claim,
        transcript_path=str(transcript_path),
        exit_status=result.exit_status,
        files_touched=touched,
        error=error,
    )
