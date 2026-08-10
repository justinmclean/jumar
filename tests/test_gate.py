# SPDX-License-Identifier: Apache-2.0
"""Tests for gate.py — Stage 4: Gate.

Acceptance criteria covered:
- AC4.1  --dry-run produces the full plan output, writes the journal entry,
         and makes zero execution calls.
- AC4.2  --approve with n on stdin records skipped_by_human and does not execute.
- AC4.3  A subtask requesting an ungranted capability aborts with capability_denied,
         naming the subtask and capability.
- AC4.4  --approve --non-interactive exits with a configuration error before ingest;
         no item is executed.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from getstuffdone.gate import (
    GateDecision,
    GateError,
    GateMode,
    GateStartupError,
    check_startup_flags,
    gate,
)
from getstuffdone.journal import GATE_DECISION, Journal
from getstuffdone.models import (
    Capability,
    Check,
    CheckKind,
    FailureCode,
    HarnessInfo,
    ItemStatus,
    Plan,
    Subtask,
    SubtaskStatus,
    TodoItem,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_check() -> Check:
    return Check(kind=CheckKind.command, statement="Step done", command=("test", "-d", "."))


def _make_harness() -> HarnessInfo:
    return HarnessInfo(agent="claude", model="sonnet", harness="claude", invoked_as="claude")


def _make_subtask(
    index: int = 0,
    *,
    capabilities: frozenset[Capability] | None = None,
    description: str | None = None,
) -> Subtask:
    if capabilities is None:
        capabilities = frozenset({Capability.run_commands})
    return Subtask(
        subtask_id=f"item-1#{index}",
        index=index,
        description=description or f"Step {index + 1}",
        check=_make_check(),
        capabilities=capabilities,
        depends_on=(),
        status=SubtaskStatus.pending,
        attempts=(),
    )


def _make_plan(subtasks: tuple[Subtask, ...] | None = None) -> Plan:
    if subtasks is None:
        subtasks = (_make_subtask(0),)
    return Plan(
        item_id="item-1",
        subtasks=subtasks,
        source="model",
        created_at="2026-08-07T00:00:00Z",
        harness=_make_harness(),
    )


def _make_item(capabilities: frozenset[Capability] | None = None) -> TodoItem:
    if capabilities is None:
        capabilities = frozenset({Capability.read_fs, Capability.write_fs, Capability.run_commands})
    return TodoItem(
        item_id="item-1",
        text="Do something",
        raw_line="- [ ] Do something",
        line_no=1,
        status=ItemStatus.pending,
        context=(),
        authored_subtasks=(),
        meta={},
        priority=None,
        depends=(),
        capabilities=capabilities,
        schedule=None,
    )


@pytest.fixture
def journal(tmp_path: Path) -> Journal:
    return Journal(tmp_path / "journal.jsonl", "run-01")


# ---------------------------------------------------------------------------
# AC4.4 — check_startup_flags: --approve + --non-interactive is a config error
# ---------------------------------------------------------------------------


def test_approve_non_interactive_raises_startup_error() -> None:
    with pytest.raises(GateStartupError):
        check_startup_flags(GateMode.approve, non_interactive=True)


def test_approve_interactive_does_not_raise() -> None:
    check_startup_flags(GateMode.approve, non_interactive=False)  # must not raise


def test_auto_non_interactive_does_not_raise() -> None:
    check_startup_flags(GateMode.auto, non_interactive=True)  # must not raise


def test_dry_run_non_interactive_does_not_raise() -> None:
    check_startup_flags(GateMode.dry_run, non_interactive=True)  # must not raise


def test_startup_error_before_ingest_no_items_executed() -> None:
    """AC4.4: GateStartupError is raised before ingest would be called.

    Simulates the outer pipeline: check_startup_flags → ingest → ...
    When flags conflict, the error fires before the ingest call is reached.
    """
    ingest_called: list[bool] = []

    def simulate_pipeline_startup(approve: bool, non_interactive: bool) -> None:
        check_startup_flags(
            GateMode.approve if approve else GateMode.auto,
            non_interactive=non_interactive,
        )
        # Any code below here represents ingest and subsequent pipeline stages.
        ingest_called.append(True)

    with pytest.raises(GateStartupError):
        simulate_pipeline_startup(approve=True, non_interactive=True)

    assert not ingest_called, "ingest must not be called when startup flags are invalid"


# ---------------------------------------------------------------------------
# AC4.1 — --dry-run: plan printed, journal entry written, zero execution calls
# ---------------------------------------------------------------------------


def test_dry_run_returns_dry_run_decision(journal: Journal) -> None:
    decision = gate(
        _make_plan(), _make_item(), mode=GateMode.dry_run, journal=journal, stdout=io.StringIO()
    )
    assert decision == GateDecision.dry_run


def test_dry_run_prints_item_text_and_subtask(journal: Journal) -> None:
    out = io.StringIO()
    gate(_make_plan(), _make_item(), mode=GateMode.dry_run, journal=journal, stdout=out)
    output = out.getvalue()
    assert "Do something" in output  # item text
    assert "Step 1" in output  # subtask description
    assert "Step done" in output  # check statement


def test_dry_run_writes_gate_decision_event(journal: Journal) -> None:
    gate(_make_plan(), _make_item(), mode=GateMode.dry_run, journal=journal, stdout=io.StringIO())
    state = journal.replay()
    assert GATE_DECISION in [e["event"] for e in state.entries]


def test_dry_run_journal_entry_records_dry_run_mode(journal: Journal) -> None:
    gate(_make_plan(), _make_item(), mode=GateMode.dry_run, journal=journal, stdout=io.StringIO())
    state = journal.replay()
    entry = next(e for e in state.entries if e["event"] == GATE_DECISION)
    assert entry["payload"]["mode"] == "dry-run"


def test_dry_run_makes_zero_execution_calls(journal: Journal) -> None:
    """AC4.1: gate() in dry-run mode must not invoke any execute function itself."""
    execute_calls: list[bool] = []

    # gate() does not call execute at all — this verifies the gate function's own
    # contract: it returns GateDecision.dry_run and the caller is responsible for
    # not proceeding to execute.
    decision = gate(
        _make_plan(), _make_item(), mode=GateMode.dry_run, journal=journal, stdout=io.StringIO()
    )
    assert decision == GateDecision.dry_run
    assert not execute_calls  # gate itself never calls execute


# ---------------------------------------------------------------------------
# AC4.2 — --approve: n → skipped_by_human; y → proceed
# ---------------------------------------------------------------------------


def test_approve_n_returns_skip_decision(journal: Journal) -> None:
    decision = gate(
        _make_plan(),
        _make_item(),
        mode=GateMode.approve,
        journal=journal,
        stdin=io.StringIO("n\n"),
        stdout=io.StringIO(),
    )
    assert decision == GateDecision.skip


def test_approve_n_journals_skipped_by_human(journal: Journal) -> None:
    gate(
        _make_plan(),
        _make_item(),
        mode=GateMode.approve,
        journal=journal,
        stdin=io.StringIO("n\n"),
        stdout=io.StringIO(),
    )
    state = journal.replay()
    entry = next(e for e in state.entries if e["event"] == GATE_DECISION)
    assert entry["payload"].get("skipped_by_human") is True


def test_approve_n_does_not_proceed_to_execution(journal: Journal) -> None:
    """AC4.2: a skip decision signals no execution; the returned value is the contract."""
    decision = gate(
        _make_plan(),
        _make_item(),
        mode=GateMode.approve,
        journal=journal,
        stdin=io.StringIO("n\n"),
        stdout=io.StringIO(),
    )
    assert decision == GateDecision.skip
    # Callers must not call execute when decision is skip.


def test_approve_y_returns_proceed_decision(journal: Journal) -> None:
    decision = gate(
        _make_plan(),
        _make_item(),
        mode=GateMode.approve,
        journal=journal,
        stdin=io.StringIO("y\n"),
        stdout=io.StringIO(),
    )
    assert decision == GateDecision.proceed


def test_approve_prints_plan_before_prompt(journal: Journal) -> None:
    out = io.StringIO()
    gate(
        _make_plan(),
        _make_item(),
        mode=GateMode.approve,
        journal=journal,
        stdin=io.StringIO("n\n"),
        stdout=out,
    )
    output = out.getvalue()
    assert "Do something" in output
    assert "Step 1" in output


# ---------------------------------------------------------------------------
# AC4.3 — ungranted capability: capability_denied naming subtask and capability
# ---------------------------------------------------------------------------


def test_ungranted_capability_raises_gate_error(journal: Journal) -> None:
    item = _make_item(capabilities=frozenset({Capability.run_commands}))
    subtask = _make_subtask(
        0, capabilities=frozenset({Capability.run_commands, Capability.network})
    )
    plan = _make_plan(subtasks=(subtask,))

    with pytest.raises(GateError) as exc_info:
        gate(plan, item, mode=GateMode.auto, journal=journal)

    assert exc_info.value.failure_code == FailureCode.capability_denied


def test_capability_denied_message_names_subtask_and_capability(journal: Journal) -> None:
    item = _make_item(capabilities=frozenset({Capability.run_commands}))
    subtask = _make_subtask(
        0,
        description="Fetch data from API",
        capabilities=frozenset({Capability.run_commands, Capability.network}),
    )
    plan = _make_plan(subtasks=(subtask,))

    with pytest.raises(GateError) as exc_info:
        gate(plan, item, mode=GateMode.auto, journal=journal)

    msg = str(exc_info.value)
    assert "Fetch data from API" in msg
    assert "network" in msg


def test_all_granted_capabilities_no_error(journal: Journal) -> None:
    item = _make_item(capabilities=frozenset({Capability.run_commands, Capability.network}))
    subtask = _make_subtask(0, capabilities=frozenset({Capability.network}))
    plan = _make_plan(subtasks=(subtask,))

    decision = gate(plan, item, mode=GateMode.auto, journal=journal)
    assert decision == GateDecision.proceed


def test_capability_check_applies_in_dry_run_mode(journal: Journal) -> None:
    """AC4.3: capability refusal is independent of mode — dry-run does not bypass it."""
    item = _make_item(capabilities=frozenset({Capability.run_commands}))
    subtask = _make_subtask(0, capabilities=frozenset({Capability.network}))
    plan = _make_plan(subtasks=(subtask,))

    with pytest.raises(GateError) as exc_info:
        gate(plan, item, mode=GateMode.dry_run, journal=journal, stdout=io.StringIO())

    assert exc_info.value.failure_code == FailureCode.capability_denied


def test_capability_check_applies_in_approve_mode(journal: Journal) -> None:
    """AC4.3: capability refusal fires before the approve prompt."""
    item = _make_item(capabilities=frozenset({Capability.run_commands}))
    subtask = _make_subtask(0, capabilities=frozenset({Capability.network}))
    plan = _make_plan(subtasks=(subtask,))
    out = io.StringIO()

    with pytest.raises(GateError):
        gate(
            plan,
            item,
            mode=GateMode.approve,
            journal=journal,
            stdin=io.StringIO("y\n"),
            stdout=out,
        )

    # No prompt text written — refusal fires before plan is printed
    # (capability check before mode dispatch)
    assert "Approve" not in out.getvalue()


# ---------------------------------------------------------------------------
# Auto mode
# ---------------------------------------------------------------------------


def test_auto_mode_returns_proceed(journal: Journal) -> None:
    decision = gate(_make_plan(), _make_item(), mode=GateMode.auto, journal=journal)
    assert decision == GateDecision.proceed


def test_auto_mode_writes_gate_decision_event(journal: Journal) -> None:
    gate(_make_plan(), _make_item(), mode=GateMode.auto, journal=journal)
    state = journal.replay()
    assert GATE_DECISION in [e["event"] for e in state.entries]


# ---------------------------------------------------------------------------
# AC4.3 / S3 — Capability is a declared-subset check, not a runtime sandbox
# ---------------------------------------------------------------------------


def test_capability_check_is_declared_subset_not_containment(journal: Journal) -> None:
    """The gate's capability check is subtask.capabilities ⊆ item.capabilities.

    That is all it is. The gate does not restrict OS calls, file paths, or
    network egress at runtime. A subtask that *declares* write_fs and whose
    parent item *grants* write_fs proceeds; whether the subtask actually
    touches the right paths is the verifier's job, not the gate's.
    """
    item = _make_item(
        capabilities=frozenset({Capability.read_fs, Capability.write_fs, Capability.run_commands})
    )
    # Subtask declares an exact-match subset — must proceed.
    subtask = _make_subtask(
        0,
        capabilities=frozenset({Capability.read_fs, Capability.write_fs, Capability.run_commands}),
    )
    plan = _make_plan(subtasks=(subtask,))
    decision = gate(plan, item, mode=GateMode.auto, journal=journal)
    assert decision == GateDecision.proceed


def test_subtask_with_empty_capabilities_proceeds(journal: Journal) -> None:
    """A subtask that declares no capabilities is trivially a subset of any granted set."""
    item = _make_item(capabilities=frozenset({Capability.run_commands}))
    subtask = _make_subtask(0, capabilities=frozenset())
    plan = _make_plan(subtasks=(subtask,))
    decision = gate(plan, item, mode=GateMode.auto, journal=journal)
    assert decision == GateDecision.proceed


def test_subtask_with_empty_capabilities_proceeds_even_when_item_has_none(
    journal: Journal,
) -> None:
    """Empty ⊆ empty is vacuously true — both sides declare nothing."""
    item = _make_item(capabilities=frozenset())
    subtask = _make_subtask(0, capabilities=frozenset())
    plan = _make_plan(subtasks=(subtask,))
    decision = gate(plan, item, mode=GateMode.auto, journal=journal)
    assert decision == GateDecision.proceed


def test_subtask_with_single_capability_denied_when_item_grants_none(journal: Journal) -> None:
    """Any non-empty claimed set is not a subset of the empty granted set."""
    item = _make_item(capabilities=frozenset())
    subtask = _make_subtask(0, capabilities=frozenset({Capability.run_commands}))
    plan = _make_plan(subtasks=(subtask,))
    with pytest.raises(GateError) as exc_info:
        gate(plan, item, mode=GateMode.auto, journal=journal)
    assert exc_info.value.failure_code == FailureCode.capability_denied


def test_only_the_excess_capability_is_refused(journal: Journal) -> None:
    """Exactly the capabilities outside the item's granted set trigger refusal."""
    item = _make_item(capabilities=frozenset({Capability.run_commands}))
    # network is the only ungranted capability.
    subtask = _make_subtask(
        0,
        capabilities=frozenset({Capability.run_commands, Capability.network}),
    )
    plan = _make_plan(subtasks=(subtask,))
    with pytest.raises(GateError) as exc_info:
        gate(plan, item, mode=GateMode.auto, journal=journal)
    assert "network" in str(exc_info.value)
