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
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from jumar.backoff import advance_failure_count
from jumar.config import Config
from jumar.decompose import _RULES, DecomposeError, _build_check, decompose, item_max_subtasks
from jumar.journal import HARNESS_ERROR, ITEM_SELECTED, PLAN_CREATED, PLAN_REJECTED, Journal
from jumar.models import (
    Capability,
    Check,
    CheckKind,
    FailureCode,
    HarnessInfo,
    ItemStatus,
    TodoItem,
)

# NOTE: every fake runner below takes `**_: Any`. `decompose()` passes
# `allow_tools=False` so planning cannot use tools, and a double that rejects
# kwargs it does not care about breaks the moment the seam grows a parameter —
# which is a property of the double, not of the code under test.

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
        **_: Any,
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


def test_harness_outage_journalled_as_harness_error(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    """A usage-limit signature in agent stdout is journalled as `harness_error`.

    Evidence: run 20260812-0525-c9f7's agent_stdout_head was "You've hit your
    session limit ..." — journalled only as an ordinary plan_rejected, which
    nothing inspects. This does not (yet) change the retry/failure-code path;
    it adds the missing observability.
    """
    call_count = [0]

    def runner(prompt: str, **_: Any) -> _FakeResult:
        call_count[0] += 1
        return _FakeResult(
            exit_status=1,
            stdout="You've hit your session limit for this session.",
        )

    with pytest.raises(DecomposeError):
        _decompose(_make_item(), runner, journal, cfg, tmp_path)

    state = journal.replay()
    harness_errors = [e for e in state.entries if e["event"] == HARNESS_ERROR]
    assert len(harness_errors) == 2  # journalled on both the attempt and its retry
    assert harness_errors[0]["payload"]["reason"] == "usage_limit"
    assert harness_errors[0]["payload"]["stage"] == "decompose"
    assert "session limit" in harness_errors[0]["payload"]["agent_stdout_head"].lower()


def test_regression_run_20260812_0525_c9f7_session_limit_not_a_plain_failure(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    """Regression test for run 20260812-0525-c9f7.

    What actually happened: the harness CLI printed "You've hit your session
    limit ..." to stdout instead of running the prompt. decompose() parsed
    that text as a rejected JSON plan, failed the item as unverifiable_plan,
    and cli.py's DecomposeError handler called advance_failure_count() —
    burning the item's @failed= budget on infrastructure, not on anything
    wrong with the item. Nothing inspected agent_stdout_head to catch it.

    This reproduces the scenario end-to-end — decompose() raising, then the
    same journal handed to advance_failure_count() exactly as cli.run_item()
    does in its DecomposeError handler — and asserts the session-limit text
    is classified as harness_error (not a plain failure) and the budget is
    left untouched.
    """
    item = _make_item(item_id="regression-item")
    todo_path = tmp_path / "todo.md"
    todo_path.write_text(f"{item.raw_line}\n")

    def runner(prompt: str, **_: Any) -> _FakeResult:
        return _FakeResult(
            exit_status=1,
            # The exact text from run 20260812-0525-c9f7's agent_stdout_head.
            stdout="You've hit your session limit for this session.",
        )

    # run_item() journals item_selected before calling decompose() (cli.py).
    journal.append(ITEM_SELECTED, item_id=item.item_id, payload={})

    with pytest.raises(DecomposeError) as exc_info:
        decompose(item, config=cfg, journal=journal, cwd=tmp_path, _run_agent=runner)

    # Today decompose() still fails the item as unverifiable_plan — that part
    # of the behaviour is unchanged...
    assert exc_info.value.failure_code == FailureCode.unverifiable_plan

    # ...but the session-limit text was classified as a harness_error, not
    # silently swallowed as an ordinary plan_rejected.
    state = journal.replay()
    harness_errors = [e for e in state.entries if e["event"] == HARNESS_ERROR]
    assert harness_errors, "session-limit output must be classified as harness_error"
    assert harness_errors[0]["payload"]["reason"] == "usage_limit"
    assert "session limit" in harness_errors[0]["payload"]["agent_stdout_head"].lower()

    # cli.run_item()'s DecomposeError handler: journal item_failed, then call
    # advance_failure_count() with the very same journal.
    advance_failure_count(todo_path, item, cfg, journal)

    # The regression: a harness_error on this attempt means the budget call
    # is a no-op — @failed= is never written, so the item cannot be parked by
    # infrastructure it had no way to fix.
    assert todo_path.read_text() == f"{item.raw_line}\n"


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


def test_authored_subtasks_inherit_parent_capabilities(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    """The model supplies checks for authored subtasks, not their authority.

    Regression coverage for run 20260827-0736-d324: an authored Iggy subtask
    needed to write a planning note, but the model returned only/read/no
    capabilities and Jumar accepted the under-grant. Since there is no
    per-authored-subtask capability syntax, authored subtasks inherit the
    parent item's explicit grants.
    """
    caps = frozenset({Capability.read_fs, Capability.write_fs, Capability.run_commands})
    item = _make_item(authored_subtasks=("Write the planning note",), capabilities=caps)
    response = json.dumps(
        {
            "subtasks": [
                {
                    "description": "ignored",
                    "check": {
                        "kind": "command",
                        "statement": "planning note exists",
                        "command": ["test", "-f", "planning_notes.md"],
                    },
                    "capabilities": ["read_fs"],
                    "depends_on": [],
                }
            ]
        }
    )
    runner = _fake_runner([response])
    plan = _decompose(item, runner, journal, cfg, tmp_path)

    assert plan.source == "authored"
    assert plan.subtasks[0].capabilities == caps


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
                        "path": "out/report.md",
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
    assert plan.subtasks[0].check.path == "out/report.md"
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


# ---------------------------------------------------------------------------
# M1 — per-stage model selection
# ---------------------------------------------------------------------------


def _capturing_runner(
    responses: list[str | None],
) -> tuple[Any, list[HarnessInfo]]:
    """Return a (runner, captured_harnesses) pair for inspecting the harness passed in."""
    captured: list[HarnessInfo] = []
    call_count = [0]

    def runner(
        prompt: str,
        *,
        cwd: Path,
        capabilities: Any,
        timeout_s: int,
        harness: HarnessInfo,
        **_: Any,
    ) -> _FakeResult:
        captured.append(harness)
        idx = call_count[0]
        call_count[0] += 1
        if idx >= len(responses):
            return _FakeResult(exit_status=1, stdout="")
        resp = responses[idx]
        if resp is None:
            return _FakeResult(exit_status=1, stdout="")
        return _FakeResult(exit_status=0, stdout=resp)

    return runner, captured


def test_decompose_stage_override_model_reaches_runner(journal: Journal, tmp_path: Path) -> None:
    """The model resolved for the 'decompose' stage is passed to the agent runner."""
    from jumar.config import HarnessConfig

    cfg = Config(harness=HarnessConfig(agent="claude", model="sonnet", decompose_model="opus"))
    runner, harnesses = _capturing_runner([_valid_response(1)])
    _decompose(_make_item(), runner, journal, cfg, tmp_path)

    assert len(harnesses) == 1
    assert harnesses[0].model == "opus"
    assert harnesses[0].agent == "claude"


def test_decompose_stage_override_model_reaches_journal(journal: Journal, tmp_path: Path) -> None:
    """The resolved model appears in the plan_created journal entry."""
    from jumar.config import HarnessConfig

    cfg = Config(harness=HarnessConfig(agent="claude", model="sonnet", decompose_model="opus"))
    runner = _fake_runner([_valid_response(1)])
    _decompose(_make_item(), runner, journal, cfg, tmp_path)

    state = journal.replay()
    plan_events = [e for e in state.entries if e["event"] == PLAN_CREATED]
    assert plan_events, "plan_created not journalled"
    assert plan_events[0]["payload"]["harness"]["model"] == "opus"


def test_no_decompose_override_falls_back_to_top_level(journal: Journal, tmp_path: Path) -> None:
    """Without a decompose override, the top-level model is used."""
    from jumar.config import HarnessConfig

    cfg = Config(harness=HarnessConfig(agent="claude", model="flash"))
    runner, harnesses = _capturing_runner([_valid_response(1)])
    _decompose(_make_item(), runner, journal, cfg, tmp_path)

    assert harnesses[0].model == "flash"


def test_decompose_override_does_not_affect_execute_stage(tmp_path: Path) -> None:
    """A decompose-model override must not change the model used in execute.py."""
    from jumar.config import HarnessConfig
    from jumar.execute import execute
    from jumar.models import Capability, Check, CheckKind, Subtask, SubtaskStatus

    captured: list[HarnessInfo] = []

    def runner(  # noqa: E501
        prompt: str,
        *,
        cwd: Path,
        capabilities: Any,
        timeout_s: int,
        harness: HarnessInfo,
        **_: Any,
    ) -> Any:
        captured.append(harness)
        return _FakeResult(exit_status=0, stdout="done", agent_claim="done")

    from jumar.journal import Journal as _J
    from jumar.models import ItemStatus, TodoItem

    jrn = _J(tmp_path / "j.jsonl", "r1")
    cfg = Config(harness=HarnessConfig(agent="claude", model="sonnet", decompose_model="opus"))

    item = TodoItem(
        item_id="x",
        text="t",
        raw_line="- [ ] t",
        line_no=1,
        status=ItemStatus.pending,
        context=(),
        authored_subtasks=(),
        meta={},
        priority=None,
        depends=(),
        capabilities=frozenset({Capability.run_commands}),
        schedule=None,
    )
    subtask = Subtask(
        subtask_id="x#0",
        index=0,
        description="do",
        check=Check(kind=CheckKind.command, statement="test dir", command=("test", "-d", ".")),
        capabilities=frozenset({Capability.run_commands}),
        depends_on=(),
        status=SubtaskStatus.pending,
        attempts=(),
    )

    execute(
        subtask,
        item=item,
        prior_evidence=[],
        config=cfg,
        journal=jrn,
        cwd=tmp_path,
        run_dir=tmp_path,
        _run_agent=runner,
    )

    assert len(captured) == 1
    assert captured[0].model == "sonnet"  # execute stage: no override → top-level


# ---------------------------------------------------------------------------
# C1 — thread-check-rejection-reason: retry prompt includes specific violation
# ---------------------------------------------------------------------------


def _prompt_capturing_runner(responses: list[str | None]) -> tuple[Any, list[str]]:
    """Return a (runner, captured_prompts) pair that records each prompt string."""
    captured: list[str] = []
    call_count = [0]

    def runner(
        prompt: str,
        *,
        cwd: Path,
        capabilities: Any,
        timeout_s: int,
        harness: HarnessInfo,
        **_: Any,
    ) -> _FakeResult:
        captured.append(prompt)
        idx = call_count[0]
        call_count[0] += 1
        if idx >= len(responses):
            return _FakeResult(exit_status=1, stdout="")
        resp = responses[idx]
        if resp is None:
            return _FakeResult(exit_status=1, stdout="")
        return _FakeResult(exit_status=0, stdout=resp)

    return runner, captured


def _shell_wrapper_response() -> str:
    return json.dumps(
        {
            "subtasks": [
                {
                    "description": "Step 1",
                    "check": {
                        "kind": "command",
                        "statement": "step done",
                        "command": ["bash", "-c", "grep -q foo out.txt"],
                        "expect_status": 0,
                    },
                    "capabilities": [],
                    "depends_on": [],
                }
            ]
        }
    )


def test_shell_wrapper_rejection_detail_in_retry_prompt(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    """A shell-wrapper check rejection is propagated into the retry prompt verbatim."""
    runner, prompts = _prompt_capturing_runner([_shell_wrapper_response(), _valid_response(1)])
    plan = _decompose(_make_item(), runner, journal, cfg, tmp_path)

    assert len(plan.subtasks) == 1
    assert len(prompts) == 2
    retry_prompt = prompts[1]
    assert "Retry because:" in retry_prompt
    # The specific rule text from Check.__post_init__ must appear.
    assert "shell wrapper" in retry_prompt


def test_shell_wrapper_detail_journalled(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    """The rejection_detail from a shell-wrapper check appears in the plan_rejected entry."""
    runner = _fake_runner([_shell_wrapper_response(), _valid_response(1)])
    _decompose(_make_item(), runner, journal, cfg, tmp_path)

    state = journal.replay()
    rejected = [e for e in state.entries if e["event"] == PLAN_REJECTED]
    assert rejected, "expected a plan_rejected event"
    assert "rejection_detail" in rejected[0]["payload"]
    assert "shell wrapper" in rejected[0]["payload"]["rejection_detail"]


def test_unknown_kind_detail_in_retry_prompt(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    """An unrecognised check.kind produces a retry prompt naming the bad kind."""
    bad = json.dumps(
        {
            "subtasks": [
                {
                    "description": "Step 1",
                    "check": {
                        "kind": "foobar",
                        "statement": "step done",
                    },
                    "capabilities": [],
                    "depends_on": [],
                }
            ]
        }
    )
    runner, prompts = _prompt_capturing_runner([bad, _valid_response(1)])
    plan = _decompose(_make_item(), runner, journal, cfg, tmp_path)

    assert len(plan.subtasks) == 1
    assert len(prompts) == 2
    retry_prompt = prompts[1]
    assert "Retry because:" in retry_prompt
    assert "foobar" in retry_prompt


def test_missing_check_field_retry_still_works(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    """A subtask with no check field at all still retries and succeeds (backwards-compat)."""
    bad = json.dumps(
        {"subtasks": [{"description": "Step 1", "capabilities": [], "depends_on": []}]}
    )
    runner, prompts = _prompt_capturing_runner([bad, _valid_response(1)])
    plan = _decompose(_make_item(), runner, journal, cfg, tmp_path)

    assert len(plan.subtasks) == 1
    assert len(prompts) == 2
    # Even a bare "no check field" case includes a retry reason.
    assert "Retry because:" in prompts[1]


def test_first_attempt_never_has_retry_prefix(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    """The first attempt must not contain the retry preamble."""
    runner, prompts = _prompt_capturing_runner([_valid_response(1)])
    _decompose(_make_item(), runner, journal, cfg, tmp_path)

    assert len(prompts) == 1
    assert "Retry because:" not in prompts[0]


# ---------------------------------------------------------------------------
# W9 — distinct rejection reasons: timed-out / empty-response / unparseable
# ---------------------------------------------------------------------------


def test_timed_out_response_journals_distinct_reason(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    """A timed-out agent call is rejected as `timed_out`, not `parse_error`."""

    def runner(prompt: str, **_: Any) -> _FakeResult:
        return _FakeResult(exit_status=-1, stdout="", stderr="", timed_out=True)

    with pytest.raises(DecomposeError) as exc_info:
        _decompose(_make_item(), runner, journal, cfg, tmp_path)

    assert exc_info.value.failure_code == FailureCode.unverifiable_plan
    state = journal.replay()
    rejected = [e for e in state.entries if e["event"] == PLAN_REJECTED]
    assert len(rejected) == 2  # initial + one retry
    assert all(e["payload"]["reason"] == "timed_out" for e in rejected)
    assert "timed out" in rejected[0]["payload"]["rejection_detail"]


def test_empty_response_journals_distinct_reason(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    """exit_status=0 with empty stdout is `empty_response`, not `parse_error`.

    This is the openai-harness case from run-in-use 2026-08-23: a model that
    answers into `reasoning_content` with an empty `content` returns a
    successful call that said nothing.
    """

    def runner(prompt: str, **_: Any) -> _FakeResult:
        return _FakeResult(exit_status=0, stdout="", stderr="")

    with pytest.raises(DecomposeError) as exc_info:
        _decompose(_make_item(), runner, journal, cfg, tmp_path)

    assert exc_info.value.failure_code == FailureCode.unverifiable_plan
    state = journal.replay()
    rejected = [e for e in state.entries if e["event"] == PLAN_REJECTED]
    assert len(rejected) == 2
    assert all(e["payload"]["reason"] == "empty_response" for e in rejected)


def test_prose_response_still_journals_parse_error(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    """exit_status=0 with non-empty, non-JSON stdout stays `parse_error`."""
    runner = _fake_runner(["this is not json at all", "still not json"])

    with pytest.raises(DecomposeError) as exc_info:
        _decompose(_make_item(), runner, journal, cfg, tmp_path)

    assert exc_info.value.failure_code == FailureCode.unverifiable_plan
    state = journal.replay()
    rejected = [e for e in state.entries if e["event"] == PLAN_REJECTED]
    assert len(rejected) == 2
    assert all(e["payload"]["reason"] == "parse_error" for e in rejected)


def test_timed_out_then_valid_response_retries_and_succeeds(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    """A timeout on the first attempt does not prevent the retry from succeeding."""
    call_count = [0]
    good = _valid_response(1)

    def runner(prompt: str, **_: Any) -> _FakeResult:
        call_count[0] += 1
        if call_count[0] == 1:
            return _FakeResult(exit_status=-1, stdout="", stderr="", timed_out=True)
        return _FakeResult(exit_status=0, stdout=good)

    plan = _decompose(_make_item(), runner, journal, cfg, tmp_path)
    assert len(plan.subtasks) == 1
    assert call_count[0] == 2


def test_agent_stderr_head_journalled_when_present(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    """The agent's stderr is journalled alongside the rejection when non-empty.

    Regression coverage for the openai harness's empty-message stderr note
    (see test_openai_agent.py) — the note is only diagnosable if it reaches
    the journal.
    """

    def runner(prompt: str, **_: Any) -> _FakeResult:
        return _FakeResult(
            exit_status=0,
            stdout="",
            stderr="assistant returned an empty message with no tool calls",
        )

    with pytest.raises(DecomposeError):
        _decompose(_make_item(), runner, journal, cfg, tmp_path)

    state = journal.replay()
    rejected = [e for e in state.entries if e["event"] == PLAN_REJECTED]
    assert rejected[0]["payload"]["agent_stderr_head"] == (
        "assistant returned an empty message with no tool calls"
    )


# ---------------------------------------------------------------------------
# Session id — P3 (reuse-the-execution-session)
# ---------------------------------------------------------------------------


def test_plan_has_session_id(journal: Journal, cfg: Config, tmp_path: Path) -> None:
    """decompose() returns a Plan whose session_id is a canonical dashed UUID.

    Not just non-empty: the claude CLI validates --resume as a UUID, so a
    format drift here (e.g. token_hex) passes every jumar-internal test and
    then fails on the first real multi-subtask item.
    """
    import uuid as _uuid

    runner = _fake_runner([_valid_response(1)])
    plan = _decompose(_make_item(), runner, journal, cfg, tmp_path)

    assert plan.session_id is not None
    assert str(_uuid.UUID(plan.session_id)) == plan.session_id


def test_plan_created_payload_contains_session_id(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    """The plan_created journal event must carry session_id so resume can recover it."""
    runner = _fake_runner([_valid_response(1)])
    plan = _decompose(_make_item(), runner, journal, cfg, tmp_path)

    state = journal.replay()
    created = next(e for e in state.entries if e["event"] == PLAN_CREATED)
    assert "session_id" in created["payload"]
    assert created["payload"]["session_id"] == plan.session_id


def test_session_id_is_unique_across_plans(tmp_path: Path, cfg: Config) -> None:
    """Each plan gets a distinct session_id (no repeated ids)."""
    ids: set[str] = set()
    for i in range(3):
        j = Journal(tmp_path / f"journal_{i}.jsonl", f"run-{i:02d}")
        runner = _fake_runner([_valid_response(1)])
        plan = _decompose(_make_item(), runner, j, cfg, tmp_path)
        assert plan.session_id not in ids
        ids.add(plan.session_id)


# ---------------------------------------------------------------------------
# @max-subtasks= — the per-item cap overrides config.max_subtasks
# ---------------------------------------------------------------------------


def _capped(cap: str, **kw: Any) -> TodoItem:
    return replace(_make_item(**kw), meta={"max-subtasks": cap})


def _recording_runner(responses: list[str], prompts: list[str]) -> Any:
    it = iter(responses)

    def runner(prompt: str, **_: Any) -> _FakeResult:
        prompts.append(prompt)
        return _FakeResult(exit_status=0, stdout=next(it))

    return runner


def test_item_cap_below_config_rejects_a_longer_plan(journal: Journal, tmp_path: Path) -> None:
    runner = _fake_runner([_valid_response(3)])
    with pytest.raises(DecomposeError) as exc_info:
        decompose(
            _capped("2"),
            config=Config(max_subtasks=12),
            journal=journal,
            cwd=tmp_path,
            _run_agent=runner,
        )
    assert exc_info.value.failure_code == FailureCode.plan_too_long
    assert runner.call_count[0] == 1  # not retriable
    rejected = [e for e in journal.replay().entries if e["event"] == PLAN_REJECTED]
    assert "maximum is 2" in rejected[0]["payload"]["rejection_detail"]


def test_item_cap_above_config_accepts_a_longer_plan(journal: Journal, tmp_path: Path) -> None:
    plan = decompose(
        _capped("20"),
        config=Config(max_subtasks=12),
        journal=journal,
        cwd=tmp_path,
        _run_agent=_fake_runner([_valid_response(13)]),
    )
    assert len(plan.subtasks) == 13


def test_prompt_shows_the_item_cap(journal: Journal, tmp_path: Path) -> None:
    prompts: list[str] = []
    decompose(
        _capped("5"),
        config=Config(max_subtasks=12),
        journal=journal,
        cwd=tmp_path,
        _run_agent=_recording_runner([_valid_response(1)], prompts),
    )
    assert "Maximum subtasks: 5" in prompts[0]
    assert "Maximum subtasks: 12" not in prompts[0]


def test_item_cap_applies_to_an_authored_list(journal: Journal, tmp_path: Path) -> None:
    item = _capped("1", authored_subtasks=("First", "Second"))
    with pytest.raises(DecomposeError) as exc_info:
        decompose(
            item,
            config=Config(max_subtasks=12),
            journal=journal,
            cwd=tmp_path,
            _run_agent=_fake_runner([_valid_response(2)]),
        )
    assert exc_info.value.failure_code == FailureCode.plan_too_long


@pytest.mark.parametrize("bad", ["lots", "0", "-3", "2.5"])
def test_unusable_item_cap_falls_back_to_config(journal: Journal, tmp_path: Path, bad: str) -> None:
    """Ingest warns about these; decompose must not treat them as a cap."""
    item = _capped(bad)
    cfg = Config(max_subtasks=2)
    assert item_max_subtasks(item, cfg) == 2
    prompts: list[str] = []
    with pytest.raises(DecomposeError) as exc_info:
        decompose(
            item,
            config=cfg,
            journal=journal,
            cwd=tmp_path,
            _run_agent=_recording_runner([_valid_response(3)], prompts),
        )
    assert exc_info.value.failure_code == FailureCode.plan_too_long
    assert "Maximum subtasks: 2" in prompts[0]


# ---------------------------------------------------------------------------
# W10 — author-written checks are pinned, never model-chosen
# ---------------------------------------------------------------------------


def _pinned(argv: tuple[str, ...]) -> Check:
    return Check(kind=CheckKind.command, statement="author check", command=argv)


def _pinned_item(
    subtasks: tuple[str, ...], checks: tuple[Check | None, ...], **kw: Any
) -> TodoItem:
    return replace(_make_item(authored_subtasks=subtasks, **kw), authored_checks=checks)


def _refusing_runner(*_a: Any, **_kw: Any) -> Any:
    raise AssertionError("the model was asked to plan an item whose checks are all authored")


def test_all_checks_authored_means_no_model_call(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    a, b = _pinned(("test", "-f", "a")), _pinned(("test", "-f", "b"))
    item = _pinned_item(("Make a", "Make b"), (a, b))

    plan = _decompose(item, _refusing_runner, journal, cfg, tmp_path)

    assert plan.source == "authored"
    assert [s.description for s in plan.subtasks] == ["Make a", "Make b"]
    assert plan.subtasks[0].check is a
    assert plan.subtasks[1].check is b
    assert [s.subtask_id for s in plan.subtasks] == ["test-item#0", "test-item#1"]
    assert all(s.capabilities == item.capabilities for s in plan.subtasks)

    created = [e for e in journal.replay().entries if e["event"] == PLAN_CREATED]
    assert len(created) == 1
    journalled = created[0]["payload"]["subtasks"]
    assert [s["check_source"] for s in journalled] == ["author", "author"]
    assert journalled[0]["check"]["command"] == ["test", "-f", "a"]


def test_model_is_asked_only_for_the_unpinned_subtasks(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    pinned = _pinned(("test", "-s", "data.json"))
    item = _pinned_item(
        ("Fetch the data", "Summarise it", "File the summary"), (pinned, None, None)
    )
    prompts: list[str] = []
    response = json.dumps(
        {
            "subtasks": [
                {
                    "description": "x",
                    "check": {
                        "kind": "file",
                        "statement": "summary written",
                        "path": "summary.md",
                        "pattern": "Summary",
                    },
                    "depends_on": [],
                },
                {
                    "description": "y",
                    "check": {
                        "kind": "file",
                        "statement": "summary filed",
                        "path": "filed/summary.md",
                    },
                    "depends_on": [0],
                },
            ]
        }
    )

    def runner(prompt: str, **_: Any) -> _FakeResult:
        prompts.append(prompt)
        return _FakeResult(exit_status=0, stdout=response)

    plan = _decompose(item, runner, journal, cfg, tmp_path)

    assert len(prompts) == 1
    assert "Fetch the data" not in prompts[0]
    assert "0. Summarise it" in prompts[0]
    assert "1. File the summary" in prompts[0]
    assert [s.description for s in plan.subtasks] == [
        "Fetch the data",
        "Summarise it",
        "File the summary",
    ]
    assert plan.subtasks[0].check is pinned
    assert plan.subtasks[1].check.path == "summary.md"
    assert plan.subtasks[2].check.path == "filed/summary.md"
    # depends_on from the model is relative to the subset; mapped back to the full list.
    assert plan.subtasks[2].depends_on == (1,)
    assert [s.index for s in plan.subtasks] == [0, 1, 2]
    created = next(e for e in journal.replay().entries if e["event"] == PLAN_CREATED)
    assert [s["check_source"] for s in created["payload"]["subtasks"]] == [
        "author",
        "model",
        "model",
    ]


def test_model_answering_for_a_pinned_subtask_is_rejected(
    journal: Journal, cfg: Config, tmp_path: Path
) -> None:
    """A response sized for the full list (i.e. re-checking the pinned step) is not accepted."""
    item = _pinned_item(("Pinned", "Open"), (_pinned(("test", "-f", "a")), None))
    runner = _fake_runner([_valid_response(2), _valid_response(2)])

    with pytest.raises(DecomposeError) as exc_info:
        _decompose(item, runner, journal, cfg, tmp_path)

    assert exc_info.value.failure_code == FailureCode.unverifiable_plan
    rejected = [e for e in journal.replay().entries if e["event"] == PLAN_REJECTED]
    assert "expected 1 subtasks" in rejected[0]["payload"]["rejection_detail"]


def test_authored_list_longer_than_max_subtasks_is_rejected_without_a_model_call(
    journal: Journal, tmp_path: Path
) -> None:
    checks = tuple(_pinned(("test", "-f", str(i))) for i in range(3))
    item = _pinned_item(("a", "b", "c"), checks)

    with pytest.raises(DecomposeError) as exc_info:
        decompose(
            item,
            config=Config(max_subtasks=2),
            journal=journal,
            cwd=tmp_path,
            _run_agent=_refusing_runner,
        )

    assert exc_info.value.failure_code == FailureCode.plan_too_long
    events = [e["event"] for e in journal.replay().entries]
    assert PLAN_REJECTED in events
    assert PLAN_CREATED not in events


# --- BSD grep portability -------------------------------------------------
#
# Checks run wherever jumar runs, and /usr/bin/grep on macOS is BSD grep,
# which has no -P and no -z. Runs 20260917-1433-61eb and 20260919-0905-b697
# lost seven items to `grep: invalid option -- P`: every artefact was correct
# and every repair attempt was spent rewriting work that was already right.


@pytest.mark.parametrize(
    "argv",
    [
        ("grep", "-Pzq", "(?s)Status:.*Evidence:.*Post:", "draft.md"),
        ("grep", "-Pq", "^Status:.*0\\.9\\.0", "draft.md"),
        ("grep", "-Pzo", "(?s)A.*?B", "draft.md"),
        ("grep", "-zq", "A", "draft.md"),
        ("/usr/bin/grep", "-P", "x", "draft.md"),
        ("egrep", "-z", "x", "draft.md"),
        ("grep", "--perl-regexp", "x", "draft.md"),
        ("grep", "--null-data", "x", "draft.md"),
        ("grep", "-A2", "-P", "x", "draft.md"),
    ],
)
def test_build_check_rejects_grep_flags_bsd_grep_lacks(argv: tuple[str, ...]) -> None:
    err = _build_check({"kind": "command", "statement": "s", "command": list(argv)})
    assert isinstance(err, str), f"{argv} should have been rejected"
    assert "BSD grep" in err
    assert "kind=file" in err, "the error must point at the portable alternative"


@pytest.mark.parametrize(
    "argv",
    [
        ("grep", "-q", "Article 50", "sources/ai-act.html"),
        ("grep", "-qE", "^Status:", "draft.md"),
        ("grep", "-e", "-P", "draft.md"),  # -P is the pattern, not a flag
        ("grep", "-qe", "-z", "draft.md"),
        ("grep", "--", "-Pz", "draft.md"),  # operands only after --
        ("grep", "--regexp=-P", "draft.md"),
        ("python3", "-c", "import sys; sys.exit(1)"),
        ("rg", "-P", "x", "draft.md"),  # ripgrep is not grep
    ],
)
def test_build_check_allows_portable_greps(argv: tuple[str, ...]) -> None:
    got = _build_check({"kind": "command", "statement": "s", "command": list(argv)})
    assert isinstance(got, Check), f"{argv} should have been accepted, got {got!r}"


def test_decompose_rules_steer_ordered_section_checks_to_kind_file() -> None:
    rules = "\n".join(_RULES)
    assert "-P" in rules and "BSD grep" in rules
    assert "kind=file" in rules
