# SPDX-License-Identifier: Apache-2.0
"""Tests for ``usage.py`` and the token roll-up in ``report`` and ``status``.

What is exercised
-----------------
- Decompose, execute and judge calls are summed per item and per run.
- An execute attempt with a tier is also summed under that tier.
- A call whose harness reported no counts is counted as unreported, never as zero.
- A judge verdict with no model call (missing artefact) is not a call at all.
- ``jumar report`` prints a Tokens line and a Usage section only when a model was called.
- ``jumar status`` sums usage over every run, and ``--by-usage`` ranks items by it.
- JSON output carries the same numbers.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jumar.journal import (
    ATTEMPT_FINISHED,
    ITEM_COMPLETED,
    ITEM_SELECTED,
    PLAN_CREATED,
    PLAN_REJECTED,
    RUN_FINISHED,
    RUN_STARTED,
    VERIFICATION,
    Journal,
)
from jumar.report import build_report, format_markdown, format_report_json, format_summary
from jumar.status import build_status, format_status, format_status_json
from jumar.usage import Usage, format_usage, usage_from_entries

_RUN_STARTED = {"now": "2026-08-07T09:00:00Z", "tz": "UTC", "mode": "auto", "todo_path": "todo.md"}


def _journal(tmp_path: Path, run_id: str, now: str = "2026-08-07T09:00:00Z") -> Journal:
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    journal = Journal(run_dir / "journal.jsonl", run_id)
    journal.append(RUN_STARTED, payload={**_RUN_STARTED, "now": now})
    return journal


def _entries(journal_path: Path) -> list[dict]:
    return [json.loads(line) for line in journal_path.read_text().splitlines() if line]


def _plan(item_id: str, prompt: int | None, completion: int | None) -> dict:
    return {
        "subtasks": [
            {
                "subtask_id": f"{item_id}#0",
                "index": 0,
                "description": "Do the thing",
                "check": {"kind": "file", "statement": "thing exists"},
            }
        ],
        "prompt_tokens": prompt,
        "completion_tokens": completion,
    }


def _attempt(prompt: int | None, completion: int | None, tier: str | None = None) -> dict:
    payload: dict = {
        "attempt_no": 0,
        "exit_status": 0,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "termination": "clean",
    }
    if tier is not None:
        payload.update({"tier": tier, "tier_index": 0, "tiers": [tier]})
    return payload


def _judge(prompt: int | None, completion: int | None) -> dict:
    return {
        "attempt_no": 0,
        "verdict": "passed",
        "kind": "judge",
        "summary": "ok",
        "evidence": {"prompt_tokens": prompt, "completion_tokens": completion},
    }


# ---------------------------------------------------------------------------
# usage_from_entries
# ---------------------------------------------------------------------------


def test_sums_every_stage_per_item_and_per_run(tmp_path: Path) -> None:
    j = _journal(tmp_path, "r1")
    j.append(ITEM_SELECTED, item_id="a", payload={"text": "A"})
    j.append(PLAN_REJECTED, item_id="a", payload={"prompt_tokens": 100, "completion_tokens": 10})
    j.append(PLAN_CREATED, item_id="a", payload=_plan("a", 200, 20))
    j.append(ATTEMPT_FINISHED, item_id="a", subtask_id="a#0", payload=_attempt(1000, 300, "haiku"))
    j.append(ATTEMPT_FINISHED, item_id="a", subtask_id="a#0", payload=_attempt(2000, 400, "sonnet"))
    j.append(VERIFICATION, item_id="a", subtask_id="a#0", payload=_judge(50, 5))
    j.append(ITEM_SELECTED, item_id="b", payload={"text": "B"})
    j.append(ATTEMPT_FINISHED, item_id="b", subtask_id="b#0", payload=_attempt(10, 1))

    run = usage_from_entries(_entries(tmp_path / "runs" / "r1" / "journal.jsonl"))

    a = run.items["a"]
    assert a.total.prompt_tokens == 3350
    assert a.total.completion_tokens == 735
    assert a.total.reported_calls == 5
    assert a.by_stage["decompose"].prompt_tokens == 300
    assert a.by_stage["execute"].completion_tokens == 700
    assert a.by_stage["judge"].total_tokens == 55
    assert a.by_tier["haiku"].total_tokens == 1300
    assert a.by_tier["sonnet"].total_tokens == 2400
    assert run.items["b"].by_tier == {}
    assert run.total.prompt_tokens == 3360
    assert run.total.completion_tokens == 736


def test_unreported_call_is_counted_not_zeroed(tmp_path: Path) -> None:
    j = _journal(tmp_path, "r1")
    j.append(ITEM_SELECTED, item_id="a", payload={"text": "A"})
    j.append(PLAN_CREATED, item_id="a", payload=_plan("a", None, None))
    j.append(ATTEMPT_FINISHED, item_id="a", subtask_id="a#0", payload=_attempt(None, None))
    j.append(ATTEMPT_FINISHED, item_id="a", subtask_id="a#0", payload=_attempt(40, 4))

    total = usage_from_entries(_entries(tmp_path / "runs" / "r1" / "journal.jsonl")).total
    assert total.unreported_calls == 2
    assert total.reported_calls == 1
    assert total.total_tokens == 44
    assert "1 call (2 more unreported)" in format_usage(total)


def test_judge_fail_without_a_model_call_is_not_a_call(tmp_path: Path) -> None:
    j = _journal(tmp_path, "r1")
    j.append(ITEM_SELECTED, item_id="a", payload={"text": "A"})
    j.append(
        VERIFICATION,
        item_id="a",
        subtask_id="a#0",
        payload={"verdict": "failed", "kind": "judge", "summary": "missing", "evidence": {}},
    )
    j.append(
        VERIFICATION,
        item_id="a",
        subtask_id="a#1",
        payload={"verdict": "passed", "kind": "command", "summary": "ok", "evidence": {}},
    )
    run = usage_from_entries(_entries(tmp_path / "runs" / "r1" / "journal.jsonl"))
    assert run.total.calls == 0
    assert run.items == {}


def test_entries_without_an_item_are_ignored() -> None:
    run = usage_from_entries([{"event": "attempt_finished", "payload": {"prompt_tokens": 5}}])
    assert run.total.calls == 0


def test_non_numeric_counts_are_unreported() -> None:
    usage = Usage()
    usage.add_call("12", True)
    assert usage.unreported_calls == 1
    usage.add_call(1.0, 2.0)
    assert usage.total_tokens == 3


def test_format_usage_phrases() -> None:
    assert format_usage(Usage()) == "no model calls"
    assert format_usage(Usage(unreported_calls=1)) == "tokens not reported by the harness (1 call)"
    assert format_usage(Usage(unreported_calls=3)) == "tokens not reported by the harness (3 calls)"
    assert (
        format_usage(Usage(prompt_tokens=1234, completion_tokens=5, reported_calls=1))
        == "1,234 prompt + 5 completion tokens over 1 call"
    )


# ---------------------------------------------------------------------------
# jumar report
# ---------------------------------------------------------------------------


def _reported_run(tmp_path: Path) -> Path:
    j = _journal(tmp_path, "r1")
    j.append(ITEM_SELECTED, item_id="a", payload={"text": "Write the thing"})
    j.append(PLAN_CREATED, item_id="a", payload=_plan("a", 200, 20))
    j.append(ATTEMPT_FINISHED, item_id="a", subtask_id="a#0", payload=_attempt(1000, 300, "haiku"))
    j.append(VERIFICATION, item_id="a", subtask_id="a#0", payload=_judge(50, 5))
    j.append(ITEM_COMPLETED, item_id="a")
    j.append(RUN_FINISHED)
    return tmp_path / "runs" / "r1"


def test_report_summary_and_markdown_carry_usage(tmp_path: Path) -> None:
    report = build_report(_reported_run(tmp_path))

    assert report.usage.total.total_tokens == 1575
    assert report.items[0].usage is not None
    assert report.items[0].usage.by_tier["haiku"].total_tokens == 1300

    summary = format_summary(report)
    assert "Tokens: 1,250 prompt + 325 completion tokens over 3 calls" in summary

    md = format_markdown(report)
    assert "## Usage" in md
    assert "| Write the thing | 220 | 1,300 | 55 | 1,575 |" in md
    assert "haiku: 1,000 prompt + 300 completion tokens over 1 call" in md

    doc = json.loads(format_report_json(report))
    assert doc["usage"]["total"]["prompt_tokens"] == 1250
    assert doc["items"][0]["usage"]["by_stage"]["judge"]["completion_tokens"] == 5


def test_report_without_model_calls_has_no_usage_section(tmp_path: Path) -> None:
    j = _journal(tmp_path, "r1")
    j.append(ITEM_SELECTED, item_id="a", payload={"text": "A"})
    j.append(ITEM_COMPLETED, item_id="a")
    j.append(RUN_FINISHED)
    report = build_report(tmp_path / "runs" / "r1")
    assert "Tokens:" not in format_summary(report)
    assert "## Usage" not in format_markdown(report)
    assert report.items[0].usage is None


def test_report_marks_unreported_harness_in_table(tmp_path: Path) -> None:
    j = _journal(tmp_path, "r1")
    j.append(ITEM_SELECTED, item_id="a", payload={"text": "A"})
    j.append(ATTEMPT_FINISHED, item_id="a", subtask_id="a#0", payload=_attempt(None, None))
    j.append(ITEM_COMPLETED, item_id="a")
    j.append(RUN_FINISHED)
    md = format_markdown(build_report(tmp_path / "runs" / "r1"))
    assert "| A | – | not reported | – | not reported |" in md
    assert "By tier:" not in md


# ---------------------------------------------------------------------------
# jumar status
# ---------------------------------------------------------------------------


def _config(tmp_path: Path) -> object:
    from jumar.config import load_config

    return load_config(cli_overrides={"todo_path": str(tmp_path / "todo.md"), "timezone": "UTC"})


def _status_fixture(tmp_path: Path) -> tuple[Path, Path]:
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] Cheap @id=cheap\n- [ ] Dear @id=dear\n- [ ] Untouched @id=none\n")
    j1 = _journal(tmp_path, "r1", "2026-08-07T09:00:00Z")
    j1.append(ITEM_SELECTED, item_id="dear", payload={"text": "Dear"})
    j1.append(ATTEMPT_FINISHED, item_id="dear", subtask_id="dear#0", payload=_attempt(5000, 500))
    j1.append(RUN_FINISHED)
    j2 = _journal(tmp_path, "r2", "2026-08-08T09:00:00Z")
    j2.append(ITEM_SELECTED, item_id="dear", payload={"text": "Dear"})
    j2.append(ATTEMPT_FINISHED, item_id="dear", subtask_id="dear#0", payload=_attempt(7000, 700))
    j2.append(ITEM_SELECTED, item_id="cheap", payload={"text": "Cheap"})
    j2.append(ATTEMPT_FINISHED, item_id="cheap", subtask_id="cheap#0", payload=_attempt(10, 1))
    j2.append(RUN_FINISHED)
    return todo, tmp_path / "runs"


def test_status_sums_usage_across_runs(tmp_path: Path) -> None:
    todo, runs = _status_fixture(tmp_path)
    report = build_status(todo, runs, _config(tmp_path), datetime(2026, 8, 9, tzinfo=UTC))
    rows = {row.item_id: row for row in report.items}

    assert rows["dear"].usage is not None
    assert rows["dear"].usage.total.prompt_tokens == 12000
    assert rows["dear"].usage_runs == 2
    assert rows["cheap"].usage_runs == 1
    assert rows["none"].usage is None

    text = format_status(report)
    assert "usage: 12,000 prompt + 1,200 completion tokens over 2 calls across 2 runs" in text
    assert "usage: 10 prompt + 1 completion tokens over 1 call across 1 run" in text

    doc = json.loads(format_status_json(report))
    by_id = {row["item_id"]: row for row in doc["items"]}
    assert by_id["dear"]["usage"]["total"]["completion_tokens"] == 1200
    assert by_id["none"]["usage"] is None


def test_status_by_usage_ranks_costliest_first(tmp_path: Path) -> None:
    todo, runs = _status_fixture(tmp_path)
    now = datetime(2026, 8, 9, tzinfo=UTC)
    in_file_order = build_status(todo, runs, _config(tmp_path), now)
    assert [r.item_id for r in in_file_order.items] == ["cheap", "dear", "none"]
    ranked = build_status(todo, runs, _config(tmp_path), now, by_usage=True)
    assert [r.item_id for r in ranked.items] == ["dear", "cheap", "none"]


def test_cli_status_by_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    todo, runs = _status_fixture(tmp_path)
    from jumar.cli import main

    argv = ["status", "--by-usage", "--json", "--todo", str(todo), "--runs-dir", str(runs)]
    assert main(argv) == 0
    doc = json.loads(capsys.readouterr().out)
    assert [row["item_id"] for row in doc["items"]] == ["dear", "cheap", "none"]
