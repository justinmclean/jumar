# SPDX-License-Identifier: Apache-2.0
"""``@budget=`` — park an item once it has spent its ceiling.

``@failed=`` and ``@paused=auto-failures`` stop an item that keeps failing.
Nothing stopped an item that keeps *running*: one that passes on a cheap tier,
fails on the next, is repaired, and burns the same tokens every scheduled
firing without ever parking. ``@budget=`` is the other bound. It names what
the item may cost in total, across every run, and when the roll-up crosses
it the item parks with ``@paused=budget`` exactly as a failure threshold
parks with ``@paused=auto-failures``: journal first, one line changed, the
human un-parks by deleting the token.

Forms
-----
- ``@budget=200k`` / ``@budget=1.5m`` / ``@budget=500000`` — total tokens,
  prompt plus completion, summed over every run (``usage.py``).
- ``@budget=12calls`` — model calls, whatever they cost. The only form that
  binds when the harness reports no token counts.

The check runs when an item ends, done or failed, so the run that crosses the
line finishes; the item parks afterwards and is never selected again until a
person lifts it. A malformed value is an ingest warning and no ceiling.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .backoff import _get_line, _read_lines, _set_token, _write_lines
from .journal import BUDGET_EXCEEDED
from .usage import usage_across_runs

BUDGET_PAUSE_REASON = "budget"
UNIT_TOKENS = "tokens"
UNIT_CALLS = "calls"

_BUDGET_RE = re.compile(r"^(\d+(?:\.\d+)?)([km]?)(calls?)?$", re.IGNORECASE)
_SCALE = {"": 1, "k": 1_000, "m": 1_000_000}
_PAUSED_RE = re.compile(r"@paused=\S+")
_TICKED_RE = re.compile(r"^\s*-\s+\[[xX]\]")


@dataclass(frozen=True)
class Budget:
    limit: int
    unit: str  # UNIT_TOKENS | UNIT_CALLS
    raw: str


def parse_budget(text: str) -> Budget:
    """``200k`` → 200,000 tokens; ``12calls`` → 12 calls. ValueError otherwise."""
    m = _BUDGET_RE.match(text.strip())
    if m is None:
        raise ValueError(
            f"{text!r} is not a budget: use a token count such as 200k or 1.5m, "
            "or a call count such as 12calls"
        )
    number, scale, calls = m.group(1), m.group(2).lower(), m.group(3)
    if calls and ("." in number or scale):
        raise ValueError(f"{text!r} is not a budget: a call count is a whole number, e.g. 12calls")
    limit = int(round(float(number) * _SCALE[scale]))
    if limit <= 0:
        raise ValueError(f"{text!r} is not a budget: it must be above zero")
    return Budget(limit=limit, unit=UNIT_CALLS if calls else UNIT_TOKENS, raw=text.strip())


def enforce_budget(
    todo_path: Path,
    item: Any,
    journal: Any,
    runs_dir: Path,
    *,
    dry_run: bool = False,
) -> bool:
    """Park *item* with ``@paused=budget`` if it has spent its ``@budget=``.

    Returns True when the ceiling was reached (and, unless *dry_run*, the
    line was changed). An item with no budget, an unreadable one, or one
    already carrying ``@paused=`` is left alone. Journal-before-write, like
    ``backoff.advance_failure_count``.
    """
    raw = item.meta.get("budget")
    if raw is None:
        return False
    try:
        budget = parse_budget(str(raw))
    except ValueError:
        return False  # ingest already warned; an unreadable ceiling binds nothing

    usage, runs = usage_across_runs(runs_dir, item.item_id)
    spent = usage.total.total_tokens if budget.unit == UNIT_TOKENS else usage.total.calls
    if spent < budget.limit:
        return False

    lines = _read_lines(todo_path)
    idx, line = _get_line(lines, item)
    # A box that is now ticked is finished work; its ceiling no longer binds.
    # (A recurring item stays unticked, so it is parked like any other.)
    if _TICKED_RE.match(line):
        return False
    # The file, not the ingest-time meta: this same item may have been parked
    # for failures a moment ago, and that reason stands.
    already_paused = bool(_PAUSED_RE.search(line))

    journal.append(
        BUDGET_EXCEEDED,
        item_id=item.item_id,
        payload={
            "budget": budget.raw,
            "limit": budget.limit,
            "unit": budget.unit,
            "spent": spent,
            "prompt_tokens": usage.total.prompt_tokens,
            "completion_tokens": usage.total.completion_tokens,
            "calls": usage.total.calls,
            "unreported_calls": usage.total.unreported_calls,
            "runs": runs,
            "parked": not already_paused,
        },
    )
    if dry_run or already_paused:
        return True

    lines[idx] = _set_token(line, "paused", BUDGET_PAUSE_REASON).encode("utf-8")
    _write_lines(todo_path, lines)
    return True
