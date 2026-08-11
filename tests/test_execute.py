# SPDX-License-Identifier: Apache-2.0
"""Tests for execute.py — Stage 5: Execute one subtask.

Acceptance criteria covered:
- AC5.1  Exactly one subtask is dispatched per execute call.
- AC5.2  A timed-out attempt is terminated and recorded as timed_out.
- AC5.3  Prior subtask evidence is present in the prompt for the current subtask.
- AC5.4  attempt_started is journalled before attempt_finished.
- AC5.5  An agent claiming success never by itself produces a passed subtask.

All tests inject a fake runner — no live agent is invoked.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from getstuffdone.clock import stamp
from getstuffdone.config import Config
from getstuffdone.execute import execute
from getstuffdone.journal import ATTEMPT_FINISHED, ATTEMPT_STARTED, Journal
from getstuffdone.models import (
    Attempt,
    Capability,
    Check,
    CheckKind,
    HarnessInfo,
    ItemStatus,
    Subtask,
    SubtaskStatus,
    TodoItem,
    Verdict,
    VerificationResult,
)

# ---------------------------------------------------------------------------
# Fake harness infrastructure
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeResult:
    exit_status: int
    stdout: str
    stderr: str = ""
    timed_out: bool = False
    agent_claim: str | None = None


def _fake_runner(responses: list[_FakeResult]) -> Any:
    """Return a fake run_agent callable that replays *responses* in order."""
    calls: list[str] = []
    session_ids: list[str | None] = []

    def runner(
        prompt: str,
        *,
        cwd: Path,
        capabilities: Any,
        timeout_s: int,
        harness: HarnessInfo,
        session_id: str | None = None,
    ) -> _FakeResult:
        calls.append(prompt)
        session_ids.append(session_id)
        idx = len(calls) - 1
        if idx < len(responses):
            return responses[idx]
        return _FakeResult(exit_status=1, stdout="")

    runner.calls = calls  # type: ignore[attr-defined]
    runner.session_ids = session_ids  # type: ignore[attr-defined]
    return runner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_subtask(
    index: int = 0,
    description: str = "Do something",
    item_id: str = "item-01",
    capabilities: frozenset[Capability] | None = None,
) -> Subtask:
    if capabilities is None:
        capabilities = frozenset({Capability.run_commands})
    return Subtask(
        subtask_id=f"{item_id}#{index}",
        index=index,
        description=description,
        check=Check(
            kind=CheckKind.command,
            statement="Command exits zero",
            command=("test", "-d", "."),
        ),
        capabilities=capabilities,
        depends_on=(),
        status=SubtaskStatus.pending,
        attempts=(),
    )


def _make_item(
    text: str = "Write a report",
    context: tuple[str, ...] = (),
    item_id: str = "item-01",
) -> TodoItem:
    return TodoItem(
        item_id=item_id,
        text=text,
        raw_line=f"- [ ] {text}",
        line_no=1,
        status=ItemStatus.pending,
        context=context,
        authored_subtasks=(),
        meta={},
        priority=None,
        depends=(),
        capabilities=frozenset({Capability.run_commands}),
        schedule=None,
    )


def _make_prior_evidence(subtask_id: str = "item-01#0") -> VerificationResult:
    return VerificationResult(
        subtask_id=subtask_id,
        attempt_no=0,
        verdict=Verdict.passed,
        kind=CheckKind.command,
        evidence={
            "argv": ["true"],
            "exit_status": 0,
            "stdout_head": "hello from step 0",
            "stderr_head": "",
            "duration_s": 0.001,
        },
        summary="Command exited 0 as expected: true",
        evidence_path=None,
        checked_at=stamp(),
    )


@pytest.fixture
def journal(tmp_path: Path) -> Journal:
    return Journal(tmp_path / "journal.jsonl", "run-01")


@pytest.fixture
def cfg() -> Config:
    return Config()


# ---------------------------------------------------------------------------
# AC5.1 — exactly one subtask dispatched per execute call
# ---------------------------------------------------------------------------


def test_execute_dispatches_exactly_once(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    runner = _fake_runner([_FakeResult(exit_status=0, stdout="done", agent_claim="done")])
    subtask = _make_subtask()
    item = _make_item()

    execute(
        subtask,
        item=item,
        prior_evidence=[],
        config=cfg,
        journal=journal,
        cwd=tmp_path,
        run_dir=tmp_path,
        _run_agent=runner,
    )

    assert len(runner.calls) == 1  # type: ignore[attr-defined]


def test_execute_returns_attempt(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    runner = _fake_runner([_FakeResult(exit_status=0, stdout="done", agent_claim="done")])
    subtask = _make_subtask()
    item = _make_item()

    attempt = execute(
        subtask,
        item=item,
        prior_evidence=[],
        config=cfg,
        journal=journal,
        cwd=tmp_path,
        run_dir=tmp_path,
        _run_agent=runner,
    )

    assert isinstance(attempt, Attempt)
    assert attempt.attempt_no == 0
    assert attempt.exit_status == 0


# ---------------------------------------------------------------------------
# AC5.2 — timeout recorded as timed_out
# ---------------------------------------------------------------------------


def test_execute_timeout_recorded(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    runner = _fake_runner(
        [_FakeResult(exit_status=-1, stdout="", timed_out=True, agent_claim=None)]
    )
    subtask = _make_subtask()
    item = _make_item()

    attempt = execute(
        subtask,
        item=item,
        prior_evidence=[],
        config=cfg,
        journal=journal,
        cwd=tmp_path,
        run_dir=tmp_path,
        _run_agent=runner,
    )

    assert attempt.error == "timed_out"
    assert attempt.agent_claim is None


def test_execute_timeout_journalled(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    runner = _fake_runner(
        [_FakeResult(exit_status=-1, stdout="", timed_out=True, agent_claim=None)]
    )
    execute(
        _make_subtask(),
        item=_make_item(),
        prior_evidence=[],
        config=cfg,
        journal=journal,
        cwd=tmp_path,
        run_dir=tmp_path,
        _run_agent=runner,
    )

    state = journal.replay()
    finished = next(e for e in state.entries if e["event"] == ATTEMPT_FINISHED)
    assert finished["payload"]["timed_out"] is True


# ---------------------------------------------------------------------------
# AC5.3 — prior evidence present in the prompt
# ---------------------------------------------------------------------------


def test_prior_evidence_in_prompt(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    """The stdout from a prior verified subtask appears in the next subtask's prompt."""
    runner = _fake_runner([_FakeResult(exit_status=0, stdout="", agent_claim=None)])
    prior = _make_prior_evidence(subtask_id="item-01#0")
    subtask = _make_subtask(index=1)
    item = _make_item()

    execute(
        subtask,
        item=item,
        prior_evidence=[prior],
        config=cfg,
        journal=journal,
        cwd=tmp_path,
        run_dir=tmp_path,
        _run_agent=runner,
    )

    prompt = runner.calls[0]  # type: ignore[attr-defined]
    # The prior subtask's summary should appear in the prompt.
    assert "item-01#0" in prompt
    assert "hello from step 0" in prompt


def test_no_prior_evidence_prompt_has_no_evidence_block(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    runner = _fake_runner([_FakeResult(exit_status=0, stdout="")])
    execute(
        _make_subtask(),
        item=_make_item(),
        prior_evidence=[],
        config=cfg,
        journal=journal,
        cwd=tmp_path,
        run_dir=tmp_path,
        _run_agent=runner,
    )

    prompt = runner.calls[0]  # type: ignore[attr-defined]
    assert "Verified prior subtasks" not in prompt


# ---------------------------------------------------------------------------
# AC5.4 — attempt_started journalled before attempt_finished
# ---------------------------------------------------------------------------


def test_attempt_started_before_finished(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    runner = _fake_runner([_FakeResult(exit_status=0, stdout="")])
    execute(
        _make_subtask(),
        item=_make_item(),
        prior_evidence=[],
        config=cfg,
        journal=journal,
        cwd=tmp_path,
        run_dir=tmp_path,
        _run_agent=runner,
    )

    state = journal.replay()
    events = [e["event"] for e in state.entries]
    started_idx = events.index(ATTEMPT_STARTED)
    finished_idx = events.index(ATTEMPT_FINISHED)
    assert started_idx < finished_idx


def test_attempt_started_seq_before_finished_seq(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    runner = _fake_runner([_FakeResult(exit_status=0, stdout="")])
    execute(
        _make_subtask(),
        item=_make_item(),
        prior_evidence=[],
        config=cfg,
        journal=journal,
        cwd=tmp_path,
        run_dir=tmp_path,
        _run_agent=runner,
    )

    state = journal.replay()
    started = next(e for e in state.entries if e["event"] == ATTEMPT_STARTED)
    finished = next(e for e in state.entries if e["event"] == ATTEMPT_FINISHED)
    assert started["seq"] < finished["seq"]


# ---------------------------------------------------------------------------
# AC5.5 — agent claiming success never produces a passed subtask
# ---------------------------------------------------------------------------


def test_agent_claim_stored_not_verdict(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    """The agent's 'I'm done' is an agent_claim, never the outcome."""
    runner = _fake_runner([_FakeResult(exit_status=0, stdout="done", agent_claim="Task complete!")])
    attempt = execute(
        _make_subtask(),
        item=_make_item(),
        prior_evidence=[],
        config=cfg,
        journal=journal,
        cwd=tmp_path,
        run_dir=tmp_path,
        _run_agent=runner,
    )

    # The Attempt stores the claim — verdict comes from the verifier (Stage 6).
    assert attempt.agent_claim == "Task complete!"
    # The Attempt itself has no verdict field; it is not a VerificationResult.
    assert not hasattr(attempt, "verdict")


# ---------------------------------------------------------------------------
# Transcript artefact written
# ---------------------------------------------------------------------------


def test_transcript_written_to_run_dir(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    runner = _fake_runner([_FakeResult(exit_status=0, stdout="hello", stderr="err")])
    attempt = execute(
        _make_subtask(),
        item=_make_item(),
        prior_evidence=[],
        config=cfg,
        journal=journal,
        cwd=tmp_path,
        run_dir=tmp_path,
        _run_agent=runner,
    )

    transcript = Path(attempt.transcript_path)
    assert transcript.exists()
    content = transcript.read_text()
    assert "hello" in content
    assert "err" in content


# ---------------------------------------------------------------------------
# Session-id threading — P3 (reuse-the-execution-session)
# ---------------------------------------------------------------------------


def test_session_id_none_by_default(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    """When session_id is omitted, the runner receives None (first subtask / fresh context)."""
    runner = _fake_runner([_FakeResult(exit_status=0, stdout="")])
    execute(
        _make_subtask(index=0),
        item=_make_item(),
        prior_evidence=[],
        config=cfg,
        journal=journal,
        cwd=tmp_path,
        run_dir=tmp_path,
        _run_agent=runner,
    )
    assert runner.session_ids[0] is None  # type: ignore[attr-defined]


def test_session_id_passed_to_runner(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    """When session_id is provided, the runner receives it so it can use --resume."""
    runner = _fake_runner([_FakeResult(exit_status=0, stdout="")])
    execute(
        _make_subtask(index=1),
        item=_make_item(),
        prior_evidence=[],
        config=cfg,
        journal=journal,
        cwd=tmp_path,
        run_dir=tmp_path,
        session_id="deadbeef",
        _run_agent=runner,
    )
    assert runner.session_ids[0] == "deadbeef"  # type: ignore[attr-defined]


def test_session_id_passed_on_repair(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    """session_id is forwarded to a repair attempt so the agent continues the session."""
    runner = _fake_runner([_FakeResult(exit_status=0, stdout="")])
    # Repairs use attempt_no >= 1.
    execute(
        _make_subtask(index=2),
        item=_make_item(),
        prior_evidence=[],
        config=cfg,
        journal=journal,
        cwd=tmp_path,
        run_dir=tmp_path,
        attempt_no=1,
        session_id="cafef00d",
        _run_agent=runner,
    )
    assert runner.session_ids[0] == "cafef00d"  # type: ignore[attr-defined]
