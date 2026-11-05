# SPDX-License-Identifier: Apache-2.0
"""Token usage rolled up from a run journal.

Every model call jumar makes journals the token counts its harness reported:
``plan_created`` and ``plan_rejected`` for the decompose stage,
``attempt_finished`` for the execute stage (with the tier it ran on when the
ladder is active) and the ``verification`` event of a ``judge`` check for the
judge stage. Nothing summed them. This module does, per item and per run, so
``jumar report`` can say what a run cost and ``jumar status`` can rank items
by what they have cost across every run.

Counts are tokens, not money: jumar does not know what a token costs on the
endpoint behind a harness. Only the in-process ``openai`` harness reports
counts; a subprocess CLI such as ``claude`` reports none, and those calls are
counted as *unreported* rather than as zero so a total never understates
itself silently.

Public API
----------
Usage              – prompt and completion token sums plus how many calls reported them.
ItemUsage          – one item's Usage, split by stage and by tier.
RunUsage           – a whole run's Usage with its per-item breakdown.
usage_from_entries() – build a RunUsage from parsed journal entries.
merge_usage()      – add one Usage into another.
format_usage()     – one short human-readable phrase for a Usage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

STAGE_DECOMPOSE = "decompose"
STAGE_EXECUTE = "execute"
STAGE_JUDGE = "judge"


@dataclass
class Usage:
    """Token sums over some set of model calls."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    # Calls whose harness reported token counts, and calls whose harness did
    # not. A total with reported == 0 is "not reported", never "free".
    reported_calls: int = 0
    unreported_calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def calls(self) -> int:
        return self.reported_calls + self.unreported_calls

    def add_call(self, prompt: Any, completion: Any) -> None:
        """Count one model call from its journalled token fields."""
        p = _as_int(prompt)
        c = _as_int(completion)
        if p is None and c is None:
            self.unreported_calls += 1
            return
        self.prompt_tokens += p or 0
        self.completion_tokens += c or 0
        self.reported_calls += 1


@dataclass
class ItemUsage:
    """One item's usage across the calls made on its behalf."""

    item_id: str
    total: Usage = field(default_factory=Usage)
    by_stage: dict[str, Usage] = field(default_factory=dict)
    by_tier: dict[str, Usage] = field(default_factory=dict)

    def record(self, stage: str, prompt: Any, completion: Any, tier: str | None = None) -> None:
        self.total.add_call(prompt, completion)
        self.by_stage.setdefault(stage, Usage()).add_call(prompt, completion)
        if tier is not None:
            self.by_tier.setdefault(tier, Usage()).add_call(prompt, completion)


@dataclass
class RunUsage:
    """A run's usage: the sum over every item, and each item's share."""

    total: Usage = field(default_factory=Usage)
    items: dict[str, ItemUsage] = field(default_factory=dict)


def _as_int(value: Any) -> int | None:
    """A journalled token count, or None when the harness reported nothing."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def merge_usage(into: Usage, other: Usage) -> None:
    """Add *other* into *into* in place."""
    into.prompt_tokens += other.prompt_tokens
    into.completion_tokens += other.completion_tokens
    into.reported_calls += other.reported_calls
    into.unreported_calls += other.unreported_calls


def merge_item_usage(into: ItemUsage, other: ItemUsage) -> None:
    """Add *other* into *into* in place, stage by stage and tier by tier."""
    merge_usage(into.total, other.total)
    for stage, usage in other.by_stage.items():
        merge_usage(into.by_stage.setdefault(stage, Usage()), usage)
    for tier, usage in other.by_tier.items():
        merge_usage(into.by_tier.setdefault(tier, Usage()), usage)


def usage_from_entries(entries: list[dict[str, Any]]) -> RunUsage:
    """Sum the token counts journalled in *entries* (one run's journal)."""
    run = RunUsage()

    def item_for(item_id: str) -> ItemUsage:
        return run.items.setdefault(item_id, ItemUsage(item_id=item_id))

    for entry in entries:
        event = entry.get("event", "")
        payload: dict[str, Any] = entry.get("payload") or {}
        item_id = entry.get("item_id") or payload.get("item_id")
        if not item_id:
            continue
        item_id = str(item_id)

        if event in ("plan_created", "plan_rejected"):
            item_for(item_id).record(
                STAGE_DECOMPOSE, payload.get("prompt_tokens"), payload.get("completion_tokens")
            )
        elif event == "attempt_finished":
            tier = payload.get("tier")
            item_for(item_id).record(
                STAGE_EXECUTE,
                payload.get("prompt_tokens"),
                payload.get("completion_tokens"),
                tier=str(tier) if tier is not None else None,
            )
        elif event == "verification" and payload.get("kind") == STAGE_JUDGE:
            evidence: dict[str, Any] = payload.get("evidence") or {}
            # A judge check that failed before any model call (missing or
            # empty artefact, unclean termination) carries no token keys at
            # all, so it is not a call and is not counted as unreported.
            if "prompt_tokens" not in evidence and "completion_tokens" not in evidence:
                continue
            item_for(item_id).record(
                STAGE_JUDGE, evidence.get("prompt_tokens"), evidence.get("completion_tokens")
            )

    for item in run.items.values():
        merge_usage(run.total, item.total)
    return run


def format_usage(usage: Usage) -> str:
    """``12,345 prompt + 678 completion tokens over 4 calls``, or why not."""
    if usage.calls == 0:
        return "no model calls"
    if usage.reported_calls == 0:
        plural = "" if usage.calls == 1 else "s"
        return f"tokens not reported by the harness ({usage.calls} call{plural})"
    text = (
        f"{usage.prompt_tokens:,} prompt + {usage.completion_tokens:,} completion tokens "
        f"over {usage.reported_calls} call{'' if usage.reported_calls == 1 else 's'}"
    )
    if usage.unreported_calls:
        text += f" ({usage.unreported_calls} more unreported)"
    return text
