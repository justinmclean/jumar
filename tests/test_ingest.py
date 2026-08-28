# SPDX-License-Identifier: Apache-2.0
"""Tests for ingest.py — AC1.1 through AC1.10."""

from __future__ import annotations

from pathlib import Path

import pytest

from jumar.config import Config, HarnessConfig
from jumar.ingest import IngestError, IngestResult, ingest
from jumar.models import ItemStatus, RecurUnit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cfg(**kwargs: object) -> Config:
    """Return a Config with the given overrides; everything else is default."""
    return Config(**kwargs)  # type: ignore[arg-type]


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _todo(tmp_path: Path, content: str, **cfg_kwargs: object) -> IngestResult:
    todo = tmp_path / "todo.md"
    _write(todo, content)
    return ingest(todo, _cfg(**cfg_kwargs))


# ---------------------------------------------------------------------------
# AC1.1 — N unchecked items → N pending TodoItems
# ---------------------------------------------------------------------------


def test_n_unchecked_yields_n_pending(tmp_path: Path) -> None:
    result = _todo(
        tmp_path,
        "- [ ] Task one\n- [ ] Task two\n- [ ] Task three\n",
    )
    assert len(result.items) == 3
    assert all(i.status is ItemStatus.pending for i in result.items)


def test_single_unchecked_item(tmp_path: Path) -> None:
    result = _todo(tmp_path, "- [ ] Only task\n")
    assert len(result.items) == 1
    assert result.items[0].text == "Only task"
    assert result.items[0].status is ItemStatus.pending


def test_empty_file_yields_no_items(tmp_path: Path) -> None:
    result = _todo(tmp_path, "")
    assert result.items == []
    assert result.warnings == []


def test_prose_only_file_yields_no_items(tmp_path: Path) -> None:
    result = _todo(tmp_path, "# Heading\n\nSome prose.\n")
    assert result.items == []


# ---------------------------------------------------------------------------
# AC1.2 — [x] items are parsed but marked done and never selected
# ---------------------------------------------------------------------------


def test_checked_items_marked_done(tmp_path: Path) -> None:
    result = _todo(tmp_path, "- [x] Done task\n- [ ] Pending task\n")
    done = [i for i in result.items if i.text == "Done task"]
    pending = [i for i in result.items if i.text == "Pending task"]
    assert len(done) == 1
    assert done[0].status is ItemStatus.done
    assert len(pending) == 1
    assert pending[0].status is ItemStatus.pending


def test_uppercase_X_is_done(tmp_path: Path) -> None:
    result = _todo(tmp_path, "- [X] Done with uppercase X\n")
    assert result.items[0].status is ItemStatus.done


def test_checked_items_are_still_parsed(tmp_path: Path) -> None:
    """Done items are present in the result; it is select's job to skip them."""
    result = _todo(tmp_path, "- [x] Completed\n- [x] Also done\n")
    assert len(result.items) == 2
    assert all(i.status is ItemStatus.done for i in result.items)


# ---------------------------------------------------------------------------
# AC1.3 — Indented task list → authored_subtasks, decomposition=authored
# ---------------------------------------------------------------------------


def test_indented_subtasks_become_authored_subtasks(tmp_path: Path) -> None:
    result = _todo(
        tmp_path,
        "- [ ] Main task\n  - [ ] Sub one\n  - [ ] Sub two\n",
    )
    assert len(result.items) == 1
    item = result.items[0]
    assert item.text == "Main task"
    assert item.authored_subtasks == ("Sub one", "Sub two")


def test_authored_subtasks_implies_authored_decomposition(tmp_path: Path) -> None:
    """decomposition is derived: authored when authored_subtasks is non-empty."""
    result = _todo(tmp_path, "- [ ] Main\n  - [ ] Sub\n")
    item = result.items[0]
    assert len(item.authored_subtasks) > 0


def test_no_subtasks_means_empty_authored_subtasks(tmp_path: Path) -> None:
    result = _todo(tmp_path, "- [ ] Solo task\n")
    assert result.items[0].authored_subtasks == ()


def test_subtask_metadata_stripped_from_subtask_text(tmp_path: Path) -> None:
    result = _todo(
        tmp_path,
        "- [ ] Main\n  - [ ] Sub @check=command\n",
    )
    item = result.items[0]
    assert item.authored_subtasks == ("Sub",)


def test_multiple_items_each_have_own_subtasks(tmp_path: Path) -> None:
    result = _todo(
        tmp_path,
        "- [ ] Item A\n  - [ ] A-sub\n- [ ] Item B\n  - [ ] B-sub\n",
    )
    assert len(result.items) == 2
    assert result.items[0].authored_subtasks == ("A-sub",)
    assert result.items[1].authored_subtasks == ("B-sub",)


# ---------------------------------------------------------------------------
# AC1.4 — @key=value tokens parsed into meta, raw values kept
# ---------------------------------------------------------------------------


def test_priority_parsed_into_meta_and_priority_field(tmp_path: Path) -> None:
    result = _todo(tmp_path, "- [ ] Task @priority=1\n")
    item = result.items[0]
    assert item.meta["priority"] == "1"
    assert item.priority == 1


def test_depends_parsed_into_meta_and_depends_field(tmp_path: Path) -> None:
    result = _todo(tmp_path, "- [ ] Task @depends=abc\n- [ ] Dep @id=abc\n")
    item = result.items[0]
    assert item.meta["depends"] == "abc"
    assert item.depends == ("abc",)


def test_multiple_meta_tokens(tmp_path: Path) -> None:
    result = _todo(tmp_path, "- [ ] Task @priority=1 @depends=abc\n- [ ] Dep @id=abc\n")
    item = result.items[0]
    assert item.meta["priority"] == "1"
    assert item.meta["depends"] == "abc"
    assert item.priority == 1
    assert item.depends == ("abc",)


def test_unknown_meta_keys_preserved(tmp_path: Path) -> None:
    result = _todo(tmp_path, "- [ ] Task @foo=bar\n")
    item = result.items[0]
    assert item.meta["foo"] == "bar"


def test_meta_tokens_stripped_from_text(tmp_path: Path) -> None:
    result = _todo(tmp_path, "- [ ] Write docs @priority=2\n")
    assert result.items[0].text == "Write docs"


def test_depends_comma_separated(tmp_path: Path) -> None:
    result = _todo(
        tmp_path,
        "- [ ] Task @depends=a,b,c\n- [ ] A @id=a\n- [ ] B @id=b\n- [ ] C @id=c\n",
    )
    assert result.items[0].depends == ("a", "b", "c")


def test_id_token_used_as_item_id(tmp_path: Path) -> None:
    result = _todo(tmp_path, "- [ ] My task @id=my-custom-id\n")
    assert result.items[0].item_id == "my-custom-id"


# ---------------------------------------------------------------------------
# AC1.5 — item_id stable across re-runs; changes when text changes
# ---------------------------------------------------------------------------


def test_item_id_stable_across_reruns(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    _write(todo, "- [ ] Stable task\n")
    cfg = _cfg()
    id1 = ingest(todo, cfg).items[0].item_id
    id2 = ingest(todo, cfg).items[0].item_id
    assert id1 == id2


def test_item_id_changes_when_text_changes(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    cfg = _cfg()
    _write(todo, "- [ ] Task A\n")
    id_a = ingest(todo, cfg).items[0].item_id
    _write(todo, "- [ ] Task B\n")
    id_b = ingest(todo, cfg).items[0].item_id
    assert id_a != id_b


def test_item_id_stable_when_order_changes(tmp_path: Path) -> None:
    """Re-ordering items must not orphan state — item_id depends on text, not position."""
    todo = tmp_path / "todo.md"
    cfg = _cfg()
    _write(todo, "- [ ] First task\n- [ ] Second task\n")
    ids_original = [i.item_id for i in ingest(todo, cfg).items]
    _write(todo, "- [ ] Second task\n- [ ] First task\n")
    ids_reordered = [i.item_id for i in ingest(todo, cfg).items]
    assert set(ids_original) == set(ids_reordered)


# ---------------------------------------------------------------------------
# AC1.6 — malformed line → parse warning, remaining items still parse
# ---------------------------------------------------------------------------


def test_empty_task_item_generates_warning(tmp_path: Path) -> None:
    result = _todo(tmp_path, "- [ ] Valid one\n- [ ]\n- [ ] Valid two\n")
    # The two valid items should parse
    valid = [i for i in result.items if i.text in ("Valid one", "Valid two")]
    assert len(valid) == 2
    # A warning was generated for the empty item
    assert any(
        "empty" in w.message.lower() or "skipped" in w.message.lower() for w in result.warnings
    )


def test_invalid_priority_generates_warning_and_parsing_continues(tmp_path: Path) -> None:
    result = _todo(tmp_path, "- [ ] Task one @priority=high\n- [ ] Task two\n")
    assert len(result.items) == 2
    assert result.items[0].priority is None  # bad priority ignored
    assert any("priority" in w.message.lower() for w in result.warnings)


def test_warnings_do_not_abort_parsing(tmp_path: Path) -> None:
    result = _todo(tmp_path, "- [ ] A\n- [ ]\n- [ ] B\n- [ ]\n- [ ] C\n")
    texts = {i.text for i in result.items}
    assert {"A", "B", "C"} <= texts


def test_unparseable_task_marker_generates_warning(tmp_path: Path) -> None:
    """A line that attempts task-list syntax with a marker jumar doesn't accept
    (`*` instead of `-`) fails _TASK_RE entirely — it must not be silently
    absorbed as context; it's a parse warning naming the line (AC1.6)."""
    result = _todo(tmp_path, "- [ ] Valid one\n* [ ] Bad marker\n- [ ] Valid two\n")
    valid = [i for i in result.items if i.text in ("Valid one", "Valid two")]
    assert len(valid) == 2
    warning = next(w for w in result.warnings if w.line_no == 2)
    assert "malformed" in warning.message.lower()
    assert "Bad marker" in warning.message


def test_unparseable_checkbox_char_generates_warning(tmp_path: Path) -> None:
    """A line with an invalid checkbox character also fails _TASK_RE entirely
    and must be flagged rather than silently swallowed as context."""
    result = _todo(tmp_path, "- [z] Bad checkbox\n- [ ] Still parses\n")
    assert [i.text for i in result.items] == ["Still parses"]
    warning = next(w for w in result.warnings if w.line_no == 1)
    assert "malformed" in warning.message.lower()


def test_markdown_link_bullet_is_not_flagged(tmp_path: Path) -> None:
    """An ordinary markdown link bullet — `- [text](url)` — fails _TASK_RE just
    like a malformed task line does, but it is legitimate prose: todo files
    routinely carry reference links. The malformed-line check must stay quiet on
    it and leave it as context (negative case for AC1.6)."""
    result = _todo(
        tmp_path,
        "Reference links:\n"
        "- [the spec](https://example.com/spec)\n"
        "- [design doc](./design.md)\n"
        "  - [nested link](x.md)\n"
        "- [ ] Real task\n",
    )
    assert [i.text for i in result.items] == ["Real task"]
    assert result.warnings == []


# ---------------------------------------------------------------------------
# AC1.7 — missing file is a clean actionable error, not a traceback
# ---------------------------------------------------------------------------


def test_missing_file_raises_ingest_error(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.md"
    with pytest.raises(IngestError, match="not found"):
        ingest(missing, _cfg())


def test_ingest_error_is_not_file_not_found_error(tmp_path: Path) -> None:
    """We wrap OSError in IngestError so callers get an actionable message."""
    missing = tmp_path / "ghost.md"
    with pytest.raises(IngestError):
        ingest(missing, _cfg())


# ---------------------------------------------------------------------------
# AC1.8 — @not-before, @due, @every parse into item.schedule
# ---------------------------------------------------------------------------


def test_not_before_parses_into_schedule(tmp_path: Path) -> None:
    result = _todo(
        tmp_path,
        "- [ ] Task @not-before=2026-08-11\n",
        timezone="UTC",
    )
    item = result.items[0]
    assert item.schedule is not None
    assert item.schedule.not_before is not None
    assert item.schedule.not_before_literal == "2026-08-11"


def test_due_parses_into_schedule(tmp_path: Path) -> None:
    result = _todo(tmp_path, "- [ ] Task @due=2026-08-14\n", timezone="UTC")
    item = result.items[0]
    assert item.schedule is not None
    assert item.schedule.due is not None
    assert item.schedule.due_literal == "2026-08-14"


def test_every_weekday_parses_into_schedule(tmp_path: Path) -> None:
    result = _todo(tmp_path, "- [ ] Task @every=weekday\n", timezone="UTC")
    item = result.items[0]
    assert item.schedule is not None
    recur = item.schedule.recur
    assert recur is not None
    assert recur.unit is RecurUnit.weekday
    assert recur.interval == 1


def test_every_interval_days_parses(tmp_path: Path) -> None:
    result = _todo(tmp_path, "- [ ] Task @every=2w\n", timezone="UTC")
    recur = result.items[0].schedule.recur  # type: ignore[union-attr]
    assert recur is not None
    assert recur.unit is RecurUnit.week
    assert recur.interval == 2


def test_every_dow_parses(tmp_path: Path) -> None:
    result = _todo(tmp_path, "- [ ] Task @every=mon,thu\n", timezone="UTC")
    recur = result.items[0].schedule.recur  # type: ignore[union-attr]
    assert recur is not None
    assert recur.unit is RecurUnit.dow
    assert "mon" in recur.days
    assert "thu" in recur.days


def test_schedule_original_literals_retained(tmp_path: Path) -> None:
    result = _todo(
        tmp_path,
        "- [ ] Task @not-before=2026-08-11T09:00 @due=2026-08-14\n",
        timezone="UTC",
    )
    s = result.items[0].schedule
    assert s is not None
    assert s.not_before_literal == "2026-08-11T09:00"
    assert s.due_literal == "2026-08-14"


def test_schedule_stores_configured_tz(tmp_path: Path) -> None:
    result = _todo(tmp_path, "- [ ] Task @due=2026-08-14\n", timezone="UTC")
    assert result.items[0].schedule.tz == "UTC"  # type: ignore[union-attr]


def test_no_schedule_tokens_yields_none_schedule(tmp_path: Path) -> None:
    result = _todo(tmp_path, "- [ ] Plain task\n")
    assert result.items[0].schedule is None


# ---------------------------------------------------------------------------
# AC1.9 — unparseable schedule token → item blocked, bad_schedule, run continues
# ---------------------------------------------------------------------------


def test_bad_due_token_blocks_item(tmp_path: Path) -> None:
    result = _todo(tmp_path, "- [ ] Task @due=next-tuesday-ish\n")
    item = result.items[0]
    assert item.status is ItemStatus.blocked


def test_bad_schedule_generates_warning_naming_token(tmp_path: Path) -> None:
    result = _todo(tmp_path, "- [ ] Task @due=next-tuesday-ish\n")
    assert any(
        "due" in w.message.lower() or "schedule" in w.message.lower() for w in result.warnings
    )


def test_bad_schedule_does_not_abort_run(tmp_path: Path) -> None:
    result = _todo(
        tmp_path,
        "- [ ] Good task\n- [ ] Bad task @due=not-a-date\n- [ ] Also good\n",
    )
    good = [i for i in result.items if i.status is ItemStatus.pending]
    assert len(good) == 2


def test_bad_not_before_blocks_item(tmp_path: Path) -> None:
    result = _todo(tmp_path, "- [ ] Task @not-before=whenever\n")
    assert result.items[0].status is ItemStatus.blocked


def test_bad_every_blocks_item(tmp_path: Path) -> None:
    result = _todo(tmp_path, "- [ ] Task @every=sometimes\n")
    assert result.items[0].status is ItemStatus.blocked


def test_blocked_item_meta_records_bad_schedule(tmp_path: Path) -> None:
    result = _todo(tmp_path, "- [ ] Task @due=not-a-date\n")
    item = result.items[0]
    assert "_bad_schedule" in item.meta


# ---------------------------------------------------------------------------
# AC1.10 — bare date resolves to 00:00 in configured tz; fixed tz + frozen clock
# ---------------------------------------------------------------------------


def test_bare_date_resolves_to_midnight_utc(tmp_path: Path) -> None:
    """UTC timezone: midnight 2026-08-11 UTC → 2026-08-11T00:00:00+00:00."""
    result = _todo(tmp_path, "- [ ] Task @not-before=2026-08-11\n", timezone="UTC")
    not_before = result.items[0].schedule.not_before  # type: ignore[union-attr]
    assert not_before == "2026-08-11T00:00:00+00:00"


def test_bare_date_resolves_to_midnight_local_sydney(tmp_path: Path) -> None:
    """Australia/Sydney in August is AEST (UTC+10): midnight local = 14:00 UTC prior day."""
    result = _todo(
        tmp_path,
        "- [ ] Task @not-before=2026-08-11\n",
        timezone="Australia/Sydney",
    )
    not_before = result.items[0].schedule.not_before  # type: ignore[union-attr]
    # 2026-08-11 00:00 AEST (UTC+10) = 2026-08-10 14:00 UTC
    assert not_before == "2026-08-10T14:00:00+00:00"


def test_bare_date_literal_retained_verbatim(tmp_path: Path) -> None:
    result = _todo(tmp_path, "- [ ] Task @not-before=2026-08-11\n", timezone="UTC")
    assert result.items[0].schedule.not_before_literal == "2026-08-11"  # type: ignore[union-attr]


def test_datetime_with_time_uses_local_zone(tmp_path: Path) -> None:
    """@not-before=2026-08-11T09:00 with UTC timezone → 2026-08-11T09:00:00+00:00."""
    result = _todo(
        tmp_path,
        "- [ ] Task @not-before=2026-08-11T09:00\n",
        timezone="UTC",
    )
    not_before = result.items[0].schedule.not_before  # type: ignore[union-attr]
    assert not_before == "2026-08-11T09:00:00+00:00"


def test_datetime_with_time_sydney_tz(tmp_path: Path) -> None:
    """@not-before=2026-08-11T09:00 in Australia/Sydney → UTC offset by -10h."""
    result = _todo(
        tmp_path,
        "- [ ] Task @not-before=2026-08-11T09:00\n",
        timezone="Australia/Sydney",
    )
    not_before = result.items[0].schedule.not_before  # type: ignore[union-attr]
    # 09:00 AEST (UTC+10) = 23:00 UTC previous day
    assert not_before == "2026-08-10T23:00:00+00:00"


def test_explicit_utc_suffix_z(tmp_path: Path) -> None:
    """Explicit Z suffix means UTC regardless of configured tz."""
    result = _todo(
        tmp_path,
        "- [ ] Task @not-before=2026-08-11T09:00Z\n",
        timezone="Australia/Sydney",
    )
    not_before = result.items[0].schedule.not_before  # type: ignore[union-attr]
    assert not_before == "2026-08-11T09:00:00+00:00"


# ---------------------------------------------------------------------------
# Context attachment
# ---------------------------------------------------------------------------


def test_heading_becomes_context_for_following_item(tmp_path: Path) -> None:
    result = _todo(tmp_path, "## Section\n\n- [ ] Task\n")
    item = result.items[0]
    assert any("Section" in line for line in item.context)


def test_context_resets_between_items(tmp_path: Path) -> None:
    result = _todo(tmp_path, "## A\n\n- [ ] Item A\n\n## B\n\n- [ ] Item B\n")
    assert len(result.items) == 2
    assert any("A" in line for line in result.items[0].context)
    assert any("B" in line for line in result.items[1].context)


def test_no_context_for_first_item_without_preceding_prose(tmp_path: Path) -> None:
    result = _todo(tmp_path, "- [ ] First item\n")
    assert result.items[0].context == ()


# ---------------------------------------------------------------------------
# line_no and raw_line preservation
# ---------------------------------------------------------------------------


def test_line_no_is_correct(tmp_path: Path) -> None:
    result = _todo(tmp_path, "\n\n- [ ] Third line task\n")
    assert result.items[0].line_no == 3


def test_raw_line_preserved(tmp_path: Path) -> None:
    line = "- [ ] My task @priority=1\n"
    result = _todo(tmp_path, line)
    assert result.items[0].raw_line == line


# ---------------------------------------------------------------------------
# AC2.12 — @depends= targets validated at ingest (proposed)
# ---------------------------------------------------------------------------


def test_unresolvable_depends_raises_ingest_error(tmp_path: Path) -> None:
    with pytest.raises(IngestError, match="does not exist"):
        _todo(tmp_path, "- [ ] Task @depends=missing-id\n")


def test_unresolvable_depends_names_missing_id(tmp_path: Path) -> None:
    with pytest.raises(IngestError, match="missing-id"):
        _todo(tmp_path, "- [ ] Task @depends=missing-id\n")


def test_unresolvable_depends_names_line_number(tmp_path: Path) -> None:
    with pytest.raises(IngestError, match=r"Line 2"):
        _todo(tmp_path, "# Header\n- [ ] Task @depends=ghost\n")


def test_unresolvable_depends_suggests_close_match(tmp_path: Path) -> None:
    # "setup-task" is a close match for "setur-task"
    with pytest.raises(IngestError, match="did you mean"):
        _todo(
            tmp_path,
            "- [ ] Task @depends=setup-taks\n- [ ] Setup @id=setup-task\n",
        )


def test_resolvable_depends_does_not_raise(tmp_path: Path) -> None:
    # Both items present — must parse cleanly.
    result = _todo(
        tmp_path,
        "- [ ] First @id=first\n- [ ] Second @depends=first\n",
    )
    assert len(result.items) == 2


def test_forward_reference_resolves(tmp_path: Path) -> None:
    # Item B appears before item A in the file (forward reference); must still resolve.
    result = _todo(
        tmp_path,
        "- [ ] B @depends=item-a\n- [ ] A @id=item-a\n",
    )
    assert len(result.items) == 2
    assert result.items[0].depends == ("item-a",)


def test_depends_on_completed_item_is_valid(tmp_path: Path) -> None:
    # A dependency on a [x] checked item exists in the file; not an error.
    result = _todo(
        tmp_path,
        "- [x] Done @id=done-task\n- [ ] Next @depends=done-task\n",
    )
    assert len(result.items) == 2


def test_unresolvable_depends_blocks_before_any_agent_call(tmp_path: Path) -> None:
    # Error is raised by ingest(), which runs before select() or any execution stage.
    with pytest.raises(IngestError):
        _todo(tmp_path, "- [ ] Task @id=my-task @depends=nonexistent\n")


# ---------------------------------------------------------------------------
# @harness= names a profile that must exist
# ---------------------------------------------------------------------------


def test_unknown_harness_profile_on_an_item_fails_ingest(tmp_path: Path) -> None:
    """A typo must fail before any model call, not fall back to the default.

    Mid-run is too late: the item has already spent a decompose call, and a
    silent fall-back would run the wrong model with nothing in the journal to
    say so.
    """
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] do a thing @id=t1 @harness=nosuch\n")
    with pytest.raises(IngestError) as exc:
        ingest(todo, Config())
    assert "nosuch" in str(exc.value)
    assert "t1" in str(exc.value)


def test_known_harness_profile_on_an_item_ingests(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] do a thing @id=t1 @harness=heavy\n")
    config = Config(harness_profiles={"heavy": HarnessConfig(model="qwen")})
    result = ingest(todo, config)
    assert result.items[0].meta["harness"] == "heavy"
