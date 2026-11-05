# SPDX-License-Identifier: Apache-2.0
"""Tests for ``prune.py`` and ``jumar prune``.

What is exercised
-----------------
- No selector is a usage error: the default keeps every run.
- ``--older-than`` removes finished runs started before the cutoff.
- ``--keep N`` keeps the newest N real runs and removes every dry run.
- ``--keep-dry-runs`` makes dry runs count like any other run.
- ``--dry-runs`` removes only dry runs, whatever their age.
- A run with no ``run_finished`` is kept unless ``--include-unfinished``.
- The latest run of an open todo item is kept; a done item's is not.
- A folder with no readable ``run_started`` is kept.
- ``--list`` removes nothing; a real prune removes folders and index rows.
- A bad age string is a usage error.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jumar.index import FILENAME as INDEX_FILENAME
from jumar.prune import (
    PruneError,
    apply_prune,
    parse_age,
    plan_prune,
    protected_run_ids,
    scan_runs,
)

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


def _run(
    runs: Path,
    run_id: str,
    *,
    age: timedelta,
    mode: str = "auto",
    finished: bool = True,
    item_id: str | None = None,
    done: bool = True,
) -> None:
    started = (NOW - age).isoformat()
    events: list[dict] = [
        {
            "event": "run_started",
            "seq": 1,
            "run_id": run_id,
            "ts": started,
            "payload": {"now": started, "tz": "UTC", "mode": mode, "todo_path": "todo.md"},
        }
    ]
    if item_id:
        events.append(
            {"event": "item_selected", "seq": 2, "item_id": item_id, "payload": {"text": item_id}}
        )
        events.append(
            {
                "event": "item_completed" if done else "item_failed",
                "seq": 3,
                "item_id": item_id,
                "payload": {},
            }
        )
    if finished:
        events.append(
            {"event": "run_finished", "seq": 9, "run_id": run_id, "ts": started, "payload": {}}
        )
    d = runs / run_id
    d.mkdir(parents=True)
    (d / "journal.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    with (runs / INDEX_FILENAME).open("a") as fh:
        fh.write(f"{run_id}\t{started}\t{item_id or ''}\tcompleted\n")


def _ids(decisions, action: str) -> list[str]:
    return sorted(d.run_id for d in decisions if d.action == action)


def test_no_selector_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(PruneError, match="default keeps every run"):
        plan_prune(scan_runs(tmp_path), now=NOW)


def test_parse_age() -> None:
    assert parse_age("12h") == timedelta(hours=12)
    assert parse_age("30d") == timedelta(days=30)
    assert parse_age("2w") == timedelta(weeks=2)
    for bad in ("0d", "3", "3m", "soon"):
        with pytest.raises(PruneError):
            parse_age(bad)


def test_older_than_removes_only_runs_before_the_cutoff(tmp_path: Path) -> None:
    _run(tmp_path, "old", age=timedelta(days=40))
    _run(tmp_path, "new", age=timedelta(days=2))
    decisions = plan_prune(scan_runs(tmp_path), now=NOW, older_than=timedelta(days=30))
    assert _ids(decisions, "remove") == ["old"]
    assert _ids(decisions, "keep") == ["new"]


def test_keep_counts_real_runs_and_removes_dry_runs(tmp_path: Path) -> None:
    _run(tmp_path, "r1", age=timedelta(days=5))
    _run(tmp_path, "r2", age=timedelta(days=4))
    _run(tmp_path, "r3", age=timedelta(days=3))
    _run(tmp_path, "d1", age=timedelta(hours=1), mode="dry-run")
    decisions = plan_prune(scan_runs(tmp_path), now=NOW, keep=2)
    assert _ids(decisions, "remove") == ["d1", "r1"]
    assert _ids(decisions, "keep") == ["r2", "r3"]
    reasons = {d.run_id: d.reason for d in decisions}
    assert reasons["d1"] == "dry run (never counted by --keep)"
    assert reasons["r1"] == "older than the newest 2"


def test_keep_dry_runs_makes_them_count(tmp_path: Path) -> None:
    _run(tmp_path, "r1", age=timedelta(days=5))
    _run(tmp_path, "d1", age=timedelta(hours=1), mode="dry-run")
    decisions = plan_prune(scan_runs(tmp_path), now=NOW, keep=1, keep_dry_runs=True)
    assert _ids(decisions, "remove") == ["r1"]
    assert _ids(decisions, "keep") == ["d1"]


def test_dry_runs_selector_removes_only_dry_runs(tmp_path: Path) -> None:
    _run(tmp_path, "r1", age=timedelta(days=50))
    _run(tmp_path, "d1", age=timedelta(hours=1), mode="dry-run")
    decisions = plan_prune(scan_runs(tmp_path), now=NOW, dry_runs_only=True)
    assert _ids(decisions, "remove") == ["d1"]
    assert _ids(decisions, "keep") == ["r1"]


def test_unfinished_run_is_kept_unless_included(tmp_path: Path) -> None:
    _run(tmp_path, "cut", age=timedelta(days=40), finished=False)
    kept = plan_prune(scan_runs(tmp_path), now=NOW, older_than=timedelta(days=1))
    assert _ids(kept, "keep") == ["cut"]
    assert kept[0].reason.startswith("no run_finished")
    removed = plan_prune(
        scan_runs(tmp_path), now=NOW, older_than=timedelta(days=1), include_unfinished=True
    )
    assert _ids(removed, "remove") == ["cut"]


def test_folder_without_run_started_is_kept(tmp_path: Path) -> None:
    (tmp_path / "junk").mkdir()
    (tmp_path / "junk" / "journal.jsonl").write_text("{not json\n")
    (tmp_path / "empty").mkdir()
    decisions = plan_prune(scan_runs(tmp_path), now=NOW, keep=0)
    assert _ids(decisions, "keep") == ["empty", "junk"]
    assert all("age unknown" in d.reason for d in decisions)


def test_protected_runs_are_the_latest_of_open_items(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] Open @id=open\n- [x] Closed @id=closed\n")
    runs = tmp_path / "runs"
    _run(runs, "open-old", age=timedelta(days=9), item_id="open", done=False)
    _run(runs, "open-new", age=timedelta(days=8), item_id="open", done=False)
    _run(runs, "closed-run", age=timedelta(days=7), item_id="closed")
    from jumar.config import load_config

    config = load_config(cli_overrides={"todo_path": str(todo), "timezone": "UTC"})
    protected = protected_run_ids(todo, runs, config, NOW)
    assert protected == {"open-new"}

    decisions = plan_prune(scan_runs(runs), now=NOW, keep=0, protected=protected)
    assert _ids(decisions, "remove") == ["closed-run", "open-old"]
    assert {d.reason for d in decisions if d.action == "keep"} == {"latest run of an open item"}


def test_apply_prune_removes_folders_and_index_rows(tmp_path: Path) -> None:
    _run(tmp_path, "old", age=timedelta(days=40))
    _run(tmp_path, "new", age=timedelta(days=1))
    decisions = plan_prune(scan_runs(tmp_path), now=NOW, older_than=timedelta(days=30))
    assert apply_prune(tmp_path, decisions) == ["old"]
    assert not (tmp_path / "old").exists()
    assert (tmp_path / "new" / "journal.jsonl").exists()
    index = (tmp_path / INDEX_FILENAME).read_text()
    assert "old\t" not in index
    assert index.startswith("new\t")


def test_apply_prune_without_index_file(tmp_path: Path) -> None:
    _run(tmp_path, "old", age=timedelta(days=40))
    (tmp_path / INDEX_FILENAME).unlink()
    decisions = plan_prune(scan_runs(tmp_path), now=NOW, older_than=timedelta(days=30))
    assert apply_prune(tmp_path, decisions) == ["old"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli_fixture(tmp_path: Path) -> tuple[Path, Path]:
    todo = tmp_path / "todo.md"
    todo.write_text("- [x] Done @id=done\n")
    runs = tmp_path / "runs"
    _run(runs, "old", age=timedelta(days=40), item_id="done")
    _run(runs, "new", age=timedelta(days=1), item_id="done")
    _run(runs, "dry", age=timedelta(hours=2), mode="dry-run")
    return todo, runs


def test_cli_prune_without_selector_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    todo, runs = _cli_fixture(tmp_path)
    from jumar.cli import main

    assert main(["prune", "--todo", str(todo), "--runs-dir", str(runs)]) == 2
    assert "default keeps every run" in capsys.readouterr().err
    assert (runs / "old").exists()


def test_cli_prune_list_removes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    todo, runs = _cli_fixture(tmp_path)
    from jumar.cli import main

    code = main(["prune", "--keep", "1", "--list", "--todo", str(todo), "--runs-dir", str(runs)])
    assert code == 0
    out = capsys.readouterr().out
    assert out.startswith("Would remove 2 of 3 runs")
    assert "- old" in out and "- dry" in out
    assert (runs / "old").exists() and (runs / "dry").exists()


def test_cli_prune_json_and_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    todo, runs = _cli_fixture(tmp_path)
    from jumar.cli import main

    code = main(
        ["prune", "--older-than", "30d", "--json", "--todo", str(todo), "--runs-dir", str(runs)]
    )
    assert code == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["listed_only"] is False
    assert [d["run_id"] for d in doc["removed"]] == ["old"]
    assert not (runs / "old").exists()
    assert (runs / "new").exists() and (runs / "dry").exists()


def test_cli_prune_bad_age_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    todo, runs = _cli_fixture(tmp_path)
    from jumar.cli import main

    assert (
        main(["prune", "--older-than", "soon", "--todo", str(todo), "--runs-dir", str(runs)]) == 2
    )
    assert "cannot read age" in capsys.readouterr().err


def test_cli_prune_missing_runs_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "todo.md").write_text("- [ ] x @id=x\n")
    from jumar.cli import main

    code = main(
        [
            "prune",
            "--keep",
            "5",
            "--todo",
            str(tmp_path / "todo.md"),
            "--runs-dir",
            str(tmp_path / "nope"),
        ]
    )
    assert code == 0
    assert "Removed 0 of 0 runs" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_journal_head_tolerates_blank_corrupt_and_non_object_lines(tmp_path: Path) -> None:
    d = tmp_path / "messy"
    d.mkdir()
    started = (NOW - timedelta(days=3)).isoformat()
    (d / "journal.jsonl").write_text(
        "\n{broken\n[1, 2]\n"
        + json.dumps({"event": "run_started", "payload": {"now": started, "mode": None}})
        + "\n"
        + json.dumps({"event": "run_started", "payload": {"now": "later", "mode": "auto"}})
        + "\n"
        + json.dumps({"event": "run_finished"})
        + "\n"
    )
    [run] = scan_runs(tmp_path)
    assert run.started == NOW - timedelta(days=3)
    assert run.finished is True
    assert run.mode is None


def test_unreadable_instants_leave_age_unknown(tmp_path: Path) -> None:
    for name, now_value in (
        ("bad", "not a date"),
        ("num", 12345),
        ("naive", "2026-01-01T00:00:00"),
    ):
        d = tmp_path / name
        d.mkdir()
        (d / "journal.jsonl").write_text(
            json.dumps({"event": "run_started", "payload": {"now": now_value}}) + "\n"
        )
    runs = {r.run_id: r for r in scan_runs(tmp_path)}
    assert runs["bad"].started is None
    assert runs["num"].started is None
    assert runs["naive"].started == datetime(2026, 1, 1, tzinfo=UTC)


def test_negative_keep_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(PruneError, match="zero or more"):
        plan_prune([], now=NOW, keep=-1)


def test_apply_prune_skips_a_folder_it_cannot_remove(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(tmp_path, "old", age=timedelta(days=40))
    _run(tmp_path, "older", age=timedelta(days=50))
    import shutil

    real_rmtree = shutil.rmtree

    def flaky(path, *a, **kw):  # type: ignore[no-untyped-def]
        if Path(path).name == "old":
            raise OSError("busy")
        real_rmtree(path, *a, **kw)

    monkeypatch.setattr(shutil, "rmtree", flaky)
    decisions = plan_prune(scan_runs(tmp_path), now=NOW, older_than=timedelta(days=30))
    assert apply_prune(tmp_path, decisions) == ["older"]
    index = (tmp_path / INDEX_FILENAME).read_text()
    assert "old\t" in index and "older\t" not in index


def test_apply_prune_tolerates_unreadable_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(tmp_path, "old", age=timedelta(days=40))
    index = tmp_path / INDEX_FILENAME
    monkeypatch.setattr(Path, "read_text", lambda self, **kw: (_ for _ in ()).throw(OSError("no")))
    decisions = plan_prune(scan_runs(tmp_path), now=NOW, older_than=timedelta(days=30))
    assert apply_prune(tmp_path, decisions) == ["old"]
    assert index.exists()


def test_cli_prune_names_protected_runs_and_failed_removals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] Open @id=open\n")
    runs = tmp_path / "runs"
    _run(runs, "latest", age=timedelta(days=40), item_id="open", done=False)
    _run(runs, "stuck", age=timedelta(days=41))
    import shutil

    monkeypatch.setattr(shutil, "rmtree", lambda *a, **kw: (_ for _ in ()).throw(OSError("busy")))
    from jumar.cli import main

    assert main(["prune", "--keep", "0", "--todo", str(todo), "--runs-dir", str(runs)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Removed 0 of 2 runs")
    assert "Kept 2 runs a selector would have removed:" in out
    assert "latest  (latest run of an open item)" in out
    assert "stuck  (could not be removed)" in out
