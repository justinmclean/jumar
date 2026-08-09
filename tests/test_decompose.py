# SPDX-License-Identifier: Apache-2.0
"""Tests for decompose.py — Stage 3: Decompose.

Acceptance criteria covered:
- AC3.1  missing check → retry once → unverifiable_plan
- AC3.2  plan_too_long → immediate rejection, no retry
- AC3.3  authored subtasks used verbatim; source == "authored"
- AC3.4  judge check without rationale → rejected (retriable)
- AC3.5  cyclic depends_on → invalid_plan, no retry
- AC3.6  plan_created journalled before any execution; plan_rejected journalled
         on bad attempts

All tests inject a fake runner so no live agent is invoked.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from getstuffdone.config import Config
from getstuffdone.decompose import DecomposeError, decompose
from getstuffdone.journal import PLAN_CREATED, PLAN_REJECTED, Journal
from getstuffdone.models import (
    Capability,
    FailureCode,
    HarnessInfo,
    ItemStatus,
    TodoItem,
)

# ---------------------------------------------------------------------------
# Fake agent infrastructure
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeResult:
    exit_status: int
    stdout: str
    stderr: str = ""
    timed_out: bool = False
    agent_claim: str | None = None


def _fake_runner(responses: list[str | None]) -> Any:
    """Return a deterministic fake run_agent callable.

    Each element of *responses* is either:
    - A str   — returned as stdout with exit_status=0.
    - None    — simulates a harness error (exit_status=1, empty stdout).

    After the list is exhausted every call returns exit_status=1.
    """
    call_count = [0]

    def runner(
        prompt: str,
        *,
        cwd: Path,
        capabilities: Any,
        timeout_s: int,
        harness: HarnessInfo,
    ) -> _FakeResult:
        idx = call_count[0]
        call_count[0] += 1
        if idx >= len(responses):
            return _FakeResult(exit_status=1, stdout="")
        resp = responses[idx]
        if resp is None:
            return _FakeResult(exit_status=1, stdout="")
        return _FakeResult(exit_status=0, stdout=resp)

    runner.call_count = call_count  # type: ignore[attr-defined]
    return runner


# ---------------------------------------------------------------------------
# Test fixtures and helpers
# ---------------------------------------------------------------------------


def _make_item(
    *,
    text: str = "Do something",
    authored_subtasks: tuple[str, ...] = (),
    capabilities: frozenset[Capability] | None = None,
    item_id: str = "test-item",
) -> TodoItem:
    if capabilities is None:
        capabilities = frozenset({Capability.read_fs, Capability.write_fs, Capability.run_commands})
    return TodoItem(
        item_id=item_id,
        text=text,
        raw_line=f"- [ ] {text}",
        line_no=1,
        status=ItemStatus.pending,
        context=(),
        authored_subtasks=authored_subtasks,
        meta={},
        priority=None,
        depends=(),
        capabilities=capabilities,
        schedule=None,
    )


def _valid_response(n: int = 1) -> str:
    """Return a valid JSON decomposition with *n* command-check subtasks."""
    subtasks = [
        {
            "description": f"Step {i + 1}",
            "check": {
                "kind": "command",
                "statement": f"Step {i + 1} exits zero",
                "command": ["test", "-d", "."],
                "expect_status": 0,
            },
            "capabilities": ["run_commands"],
            "depends_on": [],
        }
        for i in range(n)
    ]
    return json.dumps({"subtasks": subtasks})


@pytest.fixture
def journal(tmp_path: Path) -> Journal:
    return Journal(tmp_path / "journal.jsonl", "run-01")


@pytest.fixture
def cfg() -> Config:
    return Config(max_subtasks=12)


def _decompose(item: TodoItem, runner: Any, jrn: Journal, cfg: Config, tmp_path: Path) -> Any:
    return decompose(item, config=cfg, journal=jrn, cwd=tmp_path, _run_agent=runner)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_valid_plan_returned(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    runner = _fake_runner([_valid_response(2)])
    plan = _decompose(_make_item(), runner, journal, cfg, tmp_path)

    assert plan.item_id == "test-item"
    assert plan.source == "model"
    assert len(plan.subtasks) == 2
    for s in plan.subtasks:
        assert s.check is not None
        assert s.check.statement


def test_subtask_ids_are_indexed(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    runner = _fake_runner([_valid_response(3)])
    plan = _decompose(_make_item(), runner, journal, cfg, tmp_path)

    assert plan.subtasks[0].subtask_id == "test-item#0"
    assert plan.subtasks[2].subtask_id == "test-item#2"


def test_capabilities_clamped_to_item_grants(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    response = json.dumps(
        {
            "subtasks": [
                {
                    "description": "Step 1",
                    "check": {
                        "kind": "command",
                        "statement": "done",
                        "command": ["test", "-d", "."],
                    },
                    "capabilities": ["run_commands", "network"],  # network not granted
                    "depends_on": [],
                }
            ]
        }
    )
    runner = _fake_runner([response])
    # item does not have network capability
    item = _make_item(capabilities=frozenset({Capability.run_commands}))
    plan = _decompose(item, runner, journal, cfg, tmp_path)
    assert Capability.network not in plan.subtasks[0].capabilities


# ---------------------------------------------------------------------------
# AC3.6 — plan_created journalled before any execution
# ---------------------------------------------------------------------------


def test_plan_created_event_journalled(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    runner = _fake_runner([_valid_response(1)])
    _decompose(_make_item(), runner, journal, cfg, tmp_path)

    state = journal.replay()
    events = [e["event"] for e in state.entries]
    assert PLAN_CREATED in events


def test_plan_created_payload_has_subtasks(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    runner = _fake_runner([_valid_response(2)])
    _decompose(_make_item(), runner, journal, cfg, tmp_path)

    state = journal.replay()
    created = next(e for e in state.entries if e["event"] == PLAN_CREATED)
    assert created["payload"]["subtask_count"] == 2


# ---------------------------------------------------------------------------
# AC3.1 — missing check → retry once → unverifiable_plan
# ---------------------------------------------------------------------------


def test_missing_check_both_attempts_fail(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    bad = json.dumps(
        {"subtasks": [{"description": "Step 1", "capabilities": [], "depends_on": []}]}
    )
    runner = _fake_runner([bad, bad])

    with pytest.raises(DecomposeError) as exc_info:
        _decompose(_make_item(), runner, journal, cfg, tmp_path)

    assert exc_info.value.failure_code == FailureCode.unverifiable_plan
    assert runner.call_count[0] == 2  # initial + one retry


def test_missing_check_retried_exactly_once(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    """If the first attempt fails with missing_check, the second is tried."""
    bad = json.dumps(
        {"subtasks": [{"description": "Step 1", "capabilities": [], "depends_on": []}]}
    )
    good = _valid_response(1)
    runner = _fake_runner([bad, good])

    plan = _decompose(_make_item(), runner, journal, cfg, tmp_path)
    assert len(plan.subtasks) == 1
    assert runner.call_count[0] == 2


def test_plan_rejected_event_on_retry(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    bad = json.dumps({"subtasks": [{"description": "x", "capabilities": [], "depends_on": []}]})
    good = _valid_response(1)
    runner = _fake_runner([bad, good])
    _decompose(_make_item(), runner, journal, cfg, tmp_path)

    state = journal.replay()
    events = [e["event"] for e in state.entries]
    assert PLAN_REJECTED in events
    assert PLAN_CREATED in events


def test_plan_rejected_journalled_twice_on_double_failure(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    bad = json.dumps({"subtasks": [{"description": "x", "capabilities": [], "depends_on": []}]})
    runner = _fake_runner([bad, bad])

    with pytest.raises(DecomposeError):
        _decompose(_make_item(), runner, journal, cfg, tmp_path)

    state = journal.replay()
    rejected = [e for e in state.entries if e["event"] == PLAN_REJECTED]
    assert len(rejected) == 2


def test_parse_error_retried_once(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    runner = _fake_runner(["not json", "also not json"])

    with pytest.raises(DecomposeError) as exc_info:
        _decompose(_make_item(), runner, journal, cfg, tmp_path)

    assert exc_info.value.failure_code == FailureCode.unverifiable_plan
    assert runner.call_count[0] == 2


def test_harness_error_retried_once(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    """exit_status != 0 is treated as a parse_error and retried once."""
    runner = _fake_runner([None, None])

    with pytest.raises(DecomposeError) as exc_info:
        _decompose(_make_item(), runner, journal, cfg, tmp_path)

    assert exc_info.value.failure_code == FailureCode.unverifiable_plan
    assert runner.call_count[0] == 2


def test_placeholder_statement_rejected_as_missing_check(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    for i, placeholder in enumerate(("n/a", "none", "TODO", "verify manually")):
        jrn = Journal(journal._path.parent / f"j{i}.jsonl", f"run-{i}")  # type: ignore[attr-defined]
        bad = json.dumps(
            {
                "subtasks": [
                    {
                        "description": "Step",
                        "check": {
                            "kind": "command",
                            "statement": placeholder,
                            "command": ["test", "-d", "."],
                        },
                        "capabilities": [],
                        "depends_on": [],
                    }
                ]
            }
        )
        runner = _fake_runner([bad, bad])
        with pytest.raises(DecomposeError) as exc:
            decompose(  # type: ignore[attr-defined]
                _make_item(item_id=f"it-{i}"),
                config=cfg,
                journal=jrn,
                cwd=journal._path.parent,  # type: ignore[attr-defined]
                _run_agent=runner,
            )
        assert exc.value.failure_code == FailureCode.unverifiable_plan


# ---------------------------------------------------------------------------
# AC3.2 — plan_too_long → immediate rejection, no retry
# ---------------------------------------------------------------------------


def test_plan_too_long_rejected_immediately(journal: Journal, tmp_path: Path) -> None:
    small_cfg = Config(max_subtasks=2)
    long_resp = _valid_response(3)  # 3 > max_subtasks=2
    runner = _fake_runner([long_resp])

    with pytest.raises(DecomposeError) as exc_info:
        decompose(_make_item(), config=small_cfg, journal=journal, cwd=tmp_path, _run_agent=runner)

    assert exc_info.value.failure_code == FailureCode.plan_too_long
    assert runner.call_count[0] == 1  # no retry


def test_plan_at_max_subtasks_accepted(journal: Journal, tmp_path: Path) -> None:
    small_cfg = Config(max_subtasks=3)
    runner = _fake_runner([_valid_response(3)])
    plan = decompose(
        _make_item(), config=small_cfg, journal=journal, cwd=tmp_path, _run_agent=runner
    )
    assert len(plan.subtasks) == 3


# ---------------------------------------------------------------------------
# AC3.3 — authored subtasks used verbatim
# ---------------------------------------------------------------------------


def test_authored_descriptions_override_model_descriptions(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    authored = ("First step", "Second step")
    item = _make_item(authored_subtasks=authored)
    response = json.dumps(
        {
            "subtasks": [
                {
                    "description": "IGNORED BY DECOMPOSE",
                    "check": {
                        "kind": "command",
                        "statement": "First done",
                        "command": ["test", "-d", "."],
                    },
                    "capabilities": [],
                    "depends_on": [],
                },
                {
                    "description": "ALSO IGNORED",
                    "check": {
                        "kind": "command",
                        "statement": "Second done",
                        "command": ["test", "-d", "."],
                    },
                    "capabilities": [],
                    "depends_on": [0],
                },
            ]
        }
    )
    runner = _fake_runner([response])
    plan = _decompose(item, runner, journal, cfg, tmp_path)

    assert plan.source == "authored"
    assert plan.subtasks[0].description == "First step"
    assert plan.subtasks[1].description == "Second step"


def test_authored_source_label(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    item = _make_item(authored_subtasks=("Step A",))
    response = json.dumps(
        {
            "subtasks": [
                {
                    "description": "x",
                    "check": {
                        "kind": "command",
                        "statement": "done",
                        "command": ["test", "-d", "."],
                    },
                    "capabilities": [],
                    "depends_on": [],
                }
            ]
        }
    )
    runner = _fake_runner([response])
    plan = _decompose(item, runner, journal, cfg, tmp_path)
    assert plan.source == "authored"


def test_authored_count_mismatch_is_retriable(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    """Model returns wrong number of subtasks for authored list → parse_error (retriable)."""
    item = _make_item(authored_subtasks=("Step A", "Step B"))
    one_only = json.dumps(
        {
            "subtasks": [
                {
                    "description": "x",
                    "check": {
                        "kind": "command",
                        "statement": "done",
                        "command": ["test", "-d", "."],
                    },
                    "capabilities": [],
                    "depends_on": [],
                }
            ]
        }
    )
    correct = json.dumps(
        {
            "subtasks": [
                {
                    "description": "x",
                    "check": {
                        "kind": "command",
                        "statement": "A done",
                        "command": ["test", "-d", "."],
                    },
                    "capabilities": [],
                    "depends_on": [],
                },
                {
                    "description": "x",
                    "check": {
                        "kind": "command",
                        "statement": "B done",
                        "command": ["test", "-d", "."],
                    },
                    "capabilities": [],
                    "depends_on": [0],
                },
            ]
        }
    )
    runner = _fake_runner([one_only, correct])
    plan = _decompose(item, runner, journal, cfg, tmp_path)
    assert len(plan.subtasks) == 2
    assert runner.call_count[0] == 2


# ---------------------------------------------------------------------------
# AC3.4 — judge without rationale → rejected (retriable)
# ---------------------------------------------------------------------------


def test_judge_without_rationale_rejected(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    bad = json.dumps(
        {
            "subtasks": [
                {
                    "description": "Verify quality",
                    "check": {
                        "kind": "judge",
                        "statement": "Output looks correct",
                        # rationale is missing
                    },
                    "capabilities": [],
                    "depends_on": [],
                }
            ]
        }
    )
    runner = _fake_runner([bad, bad])

    with pytest.raises(DecomposeError) as exc_info:
        _decompose(_make_item(), runner, journal, cfg, tmp_path)

    assert exc_info.value.failure_code == FailureCode.unverifiable_plan
    assert runner.call_count[0] == 2  # retried once


def test_judge_without_rationale_retried_then_succeeds(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    bad = json.dumps(
        {
            "subtasks": [
                {
                    "description": "Verify",
                    "check": {"kind": "judge", "statement": "Looks good"},
                    "capabilities": [],
                    "depends_on": [],
                }
            ]
        }
    )
    good = _valid_response(1)
    runner = _fake_runner([bad, good])
    plan = _decompose(_make_item(), runner, journal, cfg, tmp_path)
    assert len(plan.subtasks) == 1
    assert runner.call_count[0] == 2


def test_judge_with_rationale_accepted(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    response = json.dumps(
        {
            "subtasks": [
                {
                    "description": "Verify correctness",
                    "check": {
                        "kind": "judge",
                        "statement": "The output meets the spec",
                        "rationale": "No executable check exists for subjective correctness",
                    },
                    "capabilities": [],
                    "depends_on": [],
                }
            ]
        }
    )
    runner = _fake_runner([response])
    plan = _decompose(_make_item(), runner, journal, cfg, tmp_path)
    assert plan.subtasks[0].check.rationale
    assert runner.call_count[0] == 1


# ---------------------------------------------------------------------------
# AC3.5 — cyclic depends_on → invalid_plan, no retry
# ---------------------------------------------------------------------------


def test_two_node_cycle_rejected(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    cyclic = json.dumps(
        {
            "subtasks": [
                {
                    "description": "Step A",
                    "check": {
                        "kind": "command",
                        "statement": "A done",
                        "command": ["test", "-d", "."],
                    },
                    "capabilities": [],
                    "depends_on": [1],  # A → B
                },
                {
                    "description": "Step B",
                    "check": {
                        "kind": "command",
                        "statement": "B done",
                        "command": ["test", "-d", "."],
                    },
                    "capabilities": [],
                    "depends_on": [0],  # B → A  → cycle
                },
            ]
        }
    )
    runner = _fake_runner([cyclic])

    with pytest.raises(DecomposeError) as exc_info:
        _decompose(_make_item(), runner, journal, cfg, tmp_path)

    assert exc_info.value.failure_code == FailureCode.invalid_plan
    assert runner.call_count[0] == 1  # no retry


def test_self_cycle_rejected(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    self_cycle = json.dumps(
        {
            "subtasks": [
                {
                    "description": "Step A",
                    "check": {
                        "kind": "command",
                        "statement": "A done",
                        "command": ["test", "-d", "."],
                    },
                    "capabilities": [],
                    "depends_on": [0],  # self-reference
                }
            ]
        }
    )
    runner = _fake_runner([self_cycle])

    with pytest.raises(DecomposeError) as exc_info:
        _decompose(_make_item(), runner, journal, cfg, tmp_path)

    assert exc_info.value.failure_code == FailureCode.invalid_plan
    assert runner.call_count[0] == 1


def test_diamond_dag_accepted(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    """A diamond DAG (0→1, 0→2, 1→3, 2→3) has no cycle."""
    diamond = json.dumps(
        {
            "subtasks": [
                {
                    "description": "Root",
                    "check": {
                        "kind": "command",
                        "statement": "Root done",
                        "command": ["test", "-d", "."],
                    },
                    "capabilities": [],
                    "depends_on": [],
                },
                {
                    "description": "Left",
                    "check": {
                        "kind": "command",
                        "statement": "Left done",
                        "command": ["test", "-d", "."],
                    },
                    "capabilities": [],
                    "depends_on": [0],
                },
                {
                    "description": "Right",
                    "check": {
                        "kind": "command",
                        "statement": "Right done",
                        "command": ["test", "-d", "."],
                    },
                    "capabilities": [],
                    "depends_on": [0],
                },
                {
                    "description": "Merge",
                    "check": {
                        "kind": "command",
                        "statement": "Merge done",
                        "command": ["test", "-d", "."],
                    },
                    "capabilities": [],
                    "depends_on": [1, 2],
                },
            ]
        }
    )
    runner = _fake_runner([diamond])
    plan = _decompose(_make_item(), runner, journal, cfg, tmp_path)
    assert len(plan.subtasks) == 4


# ---------------------------------------------------------------------------
# JSON extraction flexibility
# ---------------------------------------------------------------------------


def test_json_inside_markdown_fence_extracted(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    fenced = "Here is the plan:\n```json\n" + _valid_response(1) + "\n```\nDone."
    runner = _fake_runner([fenced])
    plan = _decompose(_make_item(), runner, journal, cfg, tmp_path)
    assert len(plan.subtasks) == 1


def test_json_with_prose_prefix_extracted(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    prefixed = "Sure! Here is your plan:\n\n" + _valid_response(1)
    runner = _fake_runner([prefixed])
    plan = _decompose(_make_item(), runner, journal, cfg, tmp_path)
    assert len(plan.subtasks) == 1
