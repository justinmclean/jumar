# SPDX-License-Identifier: Apache-2.0
"""Tests for execute-stage model tiers (W24): start cheap, climb on failure."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import pytest

from jumar import cli
from jumar.config import Config, HarnessConfig, load_config
from jumar.harness import AgentResult
from jumar.models import Capability, Check, CheckKind, Subtask, SubtaskStatus
from jumar.tiers import NO_TIERS, ladder_for, starting_tier

_TOML = """
[jumar]
max_repairs = {repairs}

[jumar.harness]
model = "base-model"
tiers = {tiers}

[jumar.harness.judge]
model = "judge-model"

[jumar.harness.profiles.cheap]
model = "haiku"

[jumar.harness.profiles.mid]
model = "sonnet"

[jumar.harness.profiles.top]
model = "opus"
"""


def _workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    tiers: list[str],
    repairs: int = 2,
    item: str = "- [ ] Write the marker @capability=write_fs\n",
) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "jumar.toml").write_text(
        textwrap.dedent(_TOML).format(tiers=json.dumps(tiers), repairs=repairs)
    )
    (tmp_path / "todo.md").write_text(item)
    return tmp_path


_FILE_PLAN = json.dumps(
    {
        "subtasks": [
            {
                "description": "Write marker.txt containing OK",
                "check": {
                    "kind": "file",
                    "statement": "marker says OK",
                    "path": "marker.txt",
                    "pattern": "OK",
                },
            }
        ]
    }
)


def _ok(stdout: str) -> AgentResult:
    return AgentResult(exit_status=0, stdout=stdout, stderr="", timed_out=False, agent_claim="done")


class _Agent:
    """Plans one subtask; does the work only once it runs on `succeed_on`."""

    def __init__(self, succeed_on: str | None, plan: str = _FILE_PLAN) -> None:
        self.succeed_on = succeed_on
        self.plan = plan
        self.execute_models: list[str] = []
        self.judge_models: list[str] = []

    def __call__(self, prompt: str, *, cwd: Path, harness: Any = None, **_: Any) -> AgentResult:
        if prompt.startswith("Decompose the following todo item"):
            return _ok(self.plan)
        if prompt.startswith("You are a strict"):
            self.judge_models.append(harness.model)
            return _ok(
                json.dumps(
                    {
                        "verdict": "pass",
                        "reason": 'notes say "a summary"',
                        "artefacts_shown": ["notes.md"],
                    }
                )
            )
        self.execute_models.append(harness.model)
        if harness.model == self.succeed_on:
            (Path(cwd) / "marker.txt").write_text("OK\n")
            (Path(cwd) / "notes.md").write_text("a summary\n")
        return _ok("worked on it")


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
        self.harness_profile: str | None = None
        self.__dict__.update(kw)


def _journal(ws: Path) -> list[dict[str, Any]]:
    run_dir = next(d for d in (ws / "runs").iterdir() if d.is_dir())
    return [json.loads(ln) for ln in (run_dir / "journal.jsonl").read_text().splitlines() if ln]


# ---------------------------------------------------------------------------
# W24 validation
# ---------------------------------------------------------------------------


def test_a_failure_on_the_lowest_tier_retries_on_the_next_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _workspace(tmp_path, monkeypatch, tiers=["cheap", "mid", "top"])
    agent = _Agent(succeed_on="sonnet")
    assert cli._cmd_run(_Args(), _run_agent=agent) == 0
    assert agent.execute_models == ["haiku", "sonnet"]
    assert "- [x] Write the marker" in (ws / "todo.md").read_text()


def test_the_top_tier_retries_in_place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _workspace(tmp_path, monkeypatch, tiers=["cheap", "top"], repairs=3)
    agent = _Agent(succeed_on=None)  # never does the work
    assert cli._cmd_run(_Args(), _run_agent=agent) == 1
    assert agent.execute_models == ["haiku", "opus", "opus", "opus"]  # max_repairs still bounds it


def test_a_harness_outage_does_not_escalate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch, tiers=["cheap", "mid", "top"])
    seen: list[str] = []

    def outage(prompt: str, *, harness: Any = None, **_: Any) -> AgentResult:
        if prompt.startswith("Decompose the following todo item"):
            return _ok(_FILE_PLAN)
        seen.append(harness.model)
        return AgentResult(
            exit_status=1,
            stdout="",
            stderr="You've hit your usage limit",
            timed_out=False,
            agent_claim=None,
        )

    assert cli._cmd_run(_Args(), _run_agent=outage) == 1
    assert seen == ["haiku"]


def test_an_items_harness_token_pins_the_line_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(
        tmp_path,
        monkeypatch,
        tiers=["cheap", "mid", "top"],
        item="- [ ] Write the marker @capability=write_fs @harness=mid\n",
    )
    agent = _Agent(succeed_on=None)
    cli._cmd_run(_Args(), _run_agent=agent)
    assert agent.execute_models == ["sonnet", "sonnet", "sonnet"]


def test_a_harness_profile_flag_pins_the_line_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch, tiers=["cheap", "mid", "top"])
    agent = _Agent(succeed_on=None)
    cli._cmd_run(_Args(harness_profile="cheap"), _run_agent=agent)
    assert agent.execute_models == ["haiku", "haiku", "haiku"]


def test_the_journal_records_each_attempts_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _workspace(tmp_path, monkeypatch, tiers=["cheap", "mid", "top"])
    cli._cmd_run(_Args(), _run_agent=_Agent(succeed_on="opus"))
    lines = _journal(ws)
    started = [
        (ln["payload"]["tier"], ln["payload"]["harness"]["model"])
        for ln in lines
        if ln["event"] == "attempt_started"
    ]
    finished = [ln["payload"]["tier_index"] for ln in lines if ln["event"] == "attempt_finished"]
    assert started == [("cheap", "haiku"), ("mid", "sonnet"), ("top", "opus")]
    assert finished == [0, 1, 2]


def test_no_tiers_configured_keeps_the_old_behaviour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _workspace(tmp_path, monkeypatch, tiers=[])
    agent = _Agent(succeed_on=None)
    cli._cmd_run(_Args(), _run_agent=agent)
    assert agent.execute_models == ["base-model"] * 3
    assert all(
        "tier" not in ln["payload"] for ln in _journal(ws) if ln["event"] == "attempt_started"
    )


def test_the_judge_keeps_its_own_model_while_execute_escalates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch, tiers=["cheap", "mid", "top"])
    judge_plan = json.dumps(
        {
            "subtasks": [
                {
                    "description": "Write notes.md",
                    "check": {
                        "kind": "judge",
                        "statement": "notes.md reads as a summary",
                        "rationale": "prose quality",
                        "path": "notes.md",
                    },
                }
            ]
        }
    )
    agent = _Agent(succeed_on="sonnet", plan=judge_plan)
    assert cli._cmd_run(_Args(), _run_agent=agent) == 0
    assert agent.execute_models == ["sonnet"]  # a judge check starts one tier up
    assert agent.judge_models and set(agent.judge_models) == {"judge-model"}


# ---------------------------------------------------------------------------
# Ladder and starting tier
# ---------------------------------------------------------------------------


def _config(**kw: Any) -> Config:
    profiles = {n: HarnessConfig(model=n) for n in ("a", "b", "c")}
    return Config(harness_profiles=profiles, harness_tiers=("a", "b", "c"), **kw)


def test_the_ladder_is_off_without_tiers_or_when_pinned() -> None:
    assert ladder_for(Config(), None) is NO_TIERS
    assert ladder_for(_config(harness_profile="b"), None) is NO_TIERS
    assert ladder_for(_config(), "b") is NO_TIERS
    ladder = ladder_for(_config(), None)
    assert ladder.names == ("a", "b", "c")
    assert ladder.harness(9).model == "c"  # capped at the top


def _subtask(check: Check) -> Subtask:
    return Subtask(
        subtask_id="i#0",
        index=0,
        description="d",
        check=check,
        capabilities=frozenset({Capability.write_fs}),
        depends_on=(),
        status=SubtaskStatus.pending,
        attempts=(),
    )


def test_starting_tier_follows_mechanical_signals(tmp_path: Path) -> None:
    ladder = ladder_for(_config(), None)
    file_check = Check(kind=CheckKind.file, statement="s", path="x.md", pattern="Title")
    judge_check = Check(kind=CheckKind.judge, statement="s", path="x.md", rationale="prose")
    for name in ("a.csv", "b.csv", "c.csv"):
        (tmp_path / name).write_text("x\n")
    broad = Check(
        kind=CheckKind.command, statement="s", command=("diff", "a.csv", "b.csv", "c.csv")
    )
    assert starting_tier(_subtask(file_check), tmp_path, ladder) == 0
    assert starting_tier(_subtask(judge_check), tmp_path, ladder) == 1
    assert starting_tier(_subtask(broad), tmp_path, ladder) == 1
    assert starting_tier(_subtask(file_check), tmp_path, NO_TIERS) == 0


def test_tiers_must_name_defined_profiles(tmp_path: Path) -> None:
    (tmp_path / "jumar.toml").write_text('[jumar.harness]\ntiers = ["ghost"]\n')
    with pytest.raises(ValueError, match="undefined profile"):
        load_config(root=tmp_path)
