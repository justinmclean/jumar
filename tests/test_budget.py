# SPDX-License-Identifier: Apache-2.0
"""Tests for ``budget.py`` — ``@budget=`` parks an item once it has spent its ceiling.

What is exercised
-----------------
- ``200k``, ``1.5m``, ``500000`` are token ceilings; ``12calls`` is a call ceiling.
- A malformed value is a ValueError, an ingest parse warning, and no ceiling.
- Spend below the ceiling leaves the file untouched and journals nothing.
- Spend at or over the ceiling journals ``budget_exceeded`` first, then appends
  ``@paused=budget`` to exactly one line.
- The spend is summed over every run, the current one included.
- A call ceiling binds even when the harness reported no token counts.
- An item already parked for failures keeps that reason.
- ``dry_run`` journals the outcome but leaves the file byte-identical.
- A ``@paused=budget`` item is parked for select and status.
- The run loop in ``cli._run_item`` applies the check when an item ends.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from jumar.budget import (
    UNIT_CALLS,
    UNIT_TOKENS,
    enforce_budget,
    parse_budget,
)
from jumar.harness import AgentResult
from jumar.journal import (
    ATTEMPT_FINISHED,
    BUDGET_EXCEEDED,
    ITEM_SELECTED,
    RUN_STARTED,
    Journal,
)
from jumar.models import Capability, ItemStatus, TodoItem

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


def _item(raw_line: str, line_no: int = 1, item_id: str = "it") -> TodoItem:
    from jumar.ingest import _parse_meta

    _, meta = _parse_meta(raw_line.strip()[6:])
    return TodoItem(
        item_id=item_id,
        text="Task",
        raw_line=raw_line,
        line_no=line_no,
        status=ItemStatus.pending,
        context=(),
        authored_subtasks=(),
        meta=meta,
        priority=None,
        depends=(),
        capabilities=frozenset({Capability.read_fs}),
    )


def _spend(
    runs: Path, run_id: str, item_id: str, prompt: int | None, completion: int | None
) -> Journal:
    run_dir = runs / run_id
    run_dir.mkdir(parents=True)
    j = Journal(run_dir / "journal.jsonl", run_id)
    j.append(RUN_STARTED, payload={"now": NOW.isoformat(), "mode": "auto", "todo_path": "todo.md"})
    j.append(ITEM_SELECTED, item_id=item_id, payload={"text": "Task"})
    j.append(
        ATTEMPT_FINISHED,
        item_id=item_id,
        subtask_id=f"{item_id}#0",
        payload={"attempt_no": 0, "prompt_tokens": prompt, "completion_tokens": completion},
    )
    return j


def _events(j: Journal) -> list[str]:
    return [e["event"] for e in j.replay().entries]


# ---------------------------------------------------------------------------
# parse_budget
# ---------------------------------------------------------------------------


def test_parse_token_and_call_forms() -> None:
    assert parse_budget("200k").limit == parse_budget("200K").limit == 200_000
    assert parse_budget("200k").unit == UNIT_TOKENS
    assert parse_budget("1.5m").limit == 1_500_000
    assert parse_budget("500000").limit == 500_000
    assert parse_budget("12calls").limit == 12
    assert parse_budget("12calls").unit == UNIT_CALLS
    assert parse_budget("1call").limit == 1


@pytest.mark.parametrize("bad", ["", "lots", "0", "0k", "-5", "1.5calls", "2kcalls", "3m2"])
def test_parse_rejects_unreadable_budgets(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_budget(bad)


def test_ingest_warns_on_a_bad_budget_and_keeps_the_item(tmp_path: Path) -> None:
    from jumar.config import load_config
    from jumar.ingest import ingest

    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] Task @id=it @budget=lots\n- [ ] Fine @id=ok @budget=10k\n")
    config = load_config(cli_overrides={"todo_path": str(todo), "timezone": "UTC"})
    result = ingest(todo, config)
    assert [i.item_id for i in result.items] == ["it", "ok"]
    assert len(result.warnings) == 1
    assert "@budget='lots' ignored" in result.warnings[0].message


# ---------------------------------------------------------------------------
# enforce_budget
# ---------------------------------------------------------------------------


def test_no_budget_or_unreadable_budget_binds_nothing(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    runs = tmp_path / "runs"
    j = _spend(runs, "r1", "it", 10_000, 1_000)
    for line in ("- [ ] Task @id=it\n", "- [ ] Task @id=it @budget=lots\n"):
        todo.write_text(line)
        assert enforce_budget(todo, _item(line), j, runs) is False
        assert todo.read_text() == line
    assert BUDGET_EXCEEDED not in _events(j)


def test_under_budget_leaves_file_and_journal_alone(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    line = "- [ ] Task @id=it @budget=20k\n"
    todo.write_text(line)
    runs = tmp_path / "runs"
    j = _spend(runs, "r1", "it", 10_000, 1_000)
    assert enforce_budget(todo, _item(line), j, runs) is False
    assert todo.read_text() == line
    assert BUDGET_EXCEEDED not in _events(j)


def test_over_budget_journals_then_parks_one_line(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    before = "# List\n\n- [ ] Other @id=other\n- [ ] Task @id=it @budget=10k\n- [x] Done\n"
    todo.write_text(before)
    runs = tmp_path / "runs"
    _spend(runs, "r1", "it", 6_000, 500)
    j = _spend(runs, "r2", "it", 3_000, 500)  # 10,000 in total: at the ceiling counts

    assert enforce_budget(todo, _item("- [ ] Task @id=it @budget=10k\n", line_no=4), j, runs)

    after = todo.read_text()
    assert after == before.replace("@budget=10k\n", "@budget=10k @paused=budget\n")
    events = j.replay().entries
    exceeded = [e for e in events if e["event"] == BUDGET_EXCEEDED]
    assert len(exceeded) == 1
    payload = exceeded[0]["payload"]
    assert payload["budget"] == "10k"
    assert payload["limit"] == 10_000
    assert payload["unit"] == "tokens"
    assert payload["spent"] == 10_000
    assert payload["runs"] == 2
    assert payload["parked"] is True


def test_call_budget_binds_without_token_counts(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    line = "- [ ] Task @id=it @budget=2calls\n"
    todo.write_text(line)
    runs = tmp_path / "runs"
    _spend(runs, "r1", "it", None, None)
    j = _spend(runs, "r2", "it", None, None)
    assert enforce_budget(todo, _item(line), j, runs)
    assert "@paused=budget" in todo.read_text()
    payload = [e for e in j.replay().entries if e["event"] == BUDGET_EXCEEDED][0]["payload"]
    assert payload["unit"] == "calls"
    assert payload["spent"] == 2
    assert payload["unreported_calls"] == 2


def test_token_budget_cannot_bind_on_unreported_counts(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    line = "- [ ] Task @id=it @budget=1k\n"
    todo.write_text(line)
    runs = tmp_path / "runs"
    j = _spend(runs, "r1", "it", None, None)
    assert enforce_budget(todo, _item(line), j, runs) is False
    assert todo.read_text() == line


def test_already_parked_item_keeps_its_reason(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    line = "- [ ] Task @id=it @budget=1k @failed=3 @paused=auto-failures\n"
    todo.write_text(line)
    runs = tmp_path / "runs"
    j = _spend(runs, "r1", "it", 5_000, 500)
    assert enforce_budget(todo, _item(line), j, runs) is True
    assert todo.read_text() == line
    payload = [e for e in j.replay().entries if e["event"] == BUDGET_EXCEEDED][0]["payload"]
    assert payload["parked"] is False


def test_dry_run_journals_but_leaves_file_byte_identical(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    line = "- [ ] Task @id=it @budget=1k\n"
    todo.write_bytes(line.encode())
    runs = tmp_path / "runs"
    j = _spend(runs, "r1", "it", 5_000, 500)
    assert enforce_budget(todo, _item(line), j, runs, dry_run=True) is True
    assert todo.read_bytes() == line.encode()
    assert BUDGET_EXCEEDED in _events(j)


def test_budget_parked_item_is_parked_for_select_and_status(tmp_path: Path) -> None:
    from jumar.config import load_config
    from jumar.ingest import ingest
    from jumar.select import select_next
    from jumar.status import build_status

    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] Task @id=it @budget=1k @paused=budget\n")
    config = load_config(cli_overrides={"todo_path": str(todo), "timezone": "UTC"})
    items = ingest(todo, config).items
    sel = select_next(items, NOW)
    assert sel.selected is None
    assert [(i.item_id, r) for i, r in sel.parked] == [("it", "budget")]
    row = build_status(todo, tmp_path / "runs", config, NOW).items[0]
    assert row.state == "parked"
    assert row.paused_reason == "budget"


# ---------------------------------------------------------------------------
# Through the run loop
# ---------------------------------------------------------------------------

_PLAN = json.dumps(
    {
        "subtasks": [
            {
                "description": "Write marker.txt containing OK",
                "capabilities": ["write_fs"],
                "depends_on": [],
                "check": {
                    "kind": "file",
                    "statement": "marker.txt exists and contains OK",
                    "path": "marker.txt",
                    "pattern": "OK",
                },
            }
        ]
    }
)


def _metered(stdout: str, claim: str = "done") -> AgentResult:
    """An agent reply that reports 1,000 prompt and 100 completion tokens."""
    return AgentResult(
        exit_status=0,
        stdout=stdout,
        stderr="",
        timed_out=False,
        agent_claim=claim,
        prompt_tokens=1_000,
        completion_tokens=100,
    )


def _lying_metered_agent(prompt: str, *, cwd: Path, **_: Any) -> AgentResult:
    if prompt.startswith("Decompose the following todo item"):
        return _metered(_PLAN)
    return _metered("All done!", claim="All done!")


def _honest_metered_agent(prompt: str, *, cwd: Path, **_: Any) -> AgentResult:
    if prompt.startswith("Decompose the following todo item"):
        return _metered(_PLAN)
    (Path(cwd) / "marker.txt").write_text("OK\n")
    return _metered("wrote marker.txt")


class _Args:
    def __init__(self, **kw: Any) -> None:
        self.todo: str | None = None
        self.dry_run = False
        self.approve = False
        self.non_interactive = True
        self.until_empty = False
        self.run_id: str | None = None
        self.runs_dir: str | None = None
        self.retry_failed = False
        self.__dict__.update(kw)


def test_run_parks_a_failing_item_that_spends_its_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plan + attempt + two repairs is four metered calls; a 4k ceiling is reached."""
    from jumar import cli

    monkeypatch.chdir(tmp_path)
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] Create the marker @id=it @capability=write_fs @budget=4k\n")

    assert cli._cmd_run(_Args(), _run_agent=_lying_metered_agent) == 1

    line = todo.read_text()
    assert line.startswith("- [ ] Create the marker @id=it @capability=write_fs @budget=4k")
    assert "@failed=1" in line
    assert "@paused=budget" in line
    journal = next((tmp_path / "runs").glob("*/journal.jsonl"))
    events = [json.loads(ln) for ln in journal.read_text().splitlines() if ln]
    exceeded = [e for e in events if e["event"] == BUDGET_EXCEEDED]
    assert len(exceeded) == 1
    assert exceeded[0]["payload"]["spent"] >= 4_000
    # The failure count was advanced before the budget check, in journal order too.
    names = [e["event"] for e in events]
    assert names.index("failure_count_advanced") < names.index(BUDGET_EXCEEDED)


def test_run_does_not_park_a_completed_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ticked box is finished work: the ceiling no longer binds."""
    from jumar import cli

    monkeypatch.chdir(tmp_path)
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] Create the marker @id=it @capability=write_fs @budget=1k\n")

    assert cli._cmd_run(_Args(), _run_agent=_honest_metered_agent) == 0

    line = todo.read_text()
    assert line.startswith("- [x] Create the marker")
    assert "@paused" not in line
