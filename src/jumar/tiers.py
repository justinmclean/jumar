# SPDX-License-Identifier: Apache-2.0
"""Model tiers for the execute stage: start cheap, escalate on failure (W24).

Models judge difficulty poorly before trying, and a wrong cheap guess pays for
the failed attempt, its repairs and then the strong model anyway. So instead
of predicting, the execute stage starts each subtask on a tier set by
mechanical signals and climbs one tier after every attempt that did not pass:

- ``[harness] tiers = ["haiku", "sonnet", "opus"]`` names
  ``[harness.profiles.<name>]`` tables, cheapest first.
- A subtask starts at tier 0, one step up for a ``judge`` check (its work is
  judged as prose, not executed), and one more when its check reads three or
  more files. Capped at the top tier.
- Repair ``n`` runs at ``start + n``, capped at the top tier, where it simply
  retries in place. ``max_repairs`` (AC7.1) still bounds the total, and every
  repair follows an attempt that failed or ended non-clean (W17), so each one
  is a reason to climb. A harness outage ends the item before any repair, so it
  never escalates.
- An explicit ``--harness-profile`` or an item's ``@harness=`` pins the
  line-up: no escalation.
- Decompose and the judge never use tiers; the judge stays on its own model.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config, HarnessConfig
from .models import CheckKind, Subtask
from .verify.inputs import input_paths

# A check reading at least this many files counts as a broader subtask.
_MANY_FILES = 3


@dataclass(frozen=True)
class TierLadder:
    """The tiers one item escalates through; empty when escalation is off."""

    names: tuple[str, ...]
    profiles: dict[str, HarnessConfig]

    @property
    def active(self) -> bool:
        return bool(self.names)

    def clamp(self, index: int) -> int:
        return max(0, min(index, len(self.names) - 1))

    def harness(self, index: int) -> HarnessConfig:
        return self.profiles[self.names[self.clamp(index)]]

    def describe(self, index: int) -> dict[str, Any]:
        i = self.clamp(index)
        return {"tier": self.names[i], "tier_index": i, "tiers": list(self.names)}


NO_TIERS = TierLadder(names=(), profiles={})


def ladder_for(config: Config, item_profile: str | None) -> TierLadder:
    """The ladder an item escalates through, or NO_TIERS when its line-up is pinned."""
    tiers = getattr(config, "harness_tiers", ())
    if not tiers or config.harness_profile is not None or item_profile:
        return NO_TIERS
    return TierLadder(
        names=tuple(tiers), profiles={name: config.harness_profiles[name] for name in tiers}
    )


def starting_tier(subtask: Subtask, cwd: Path, ladder: TierLadder) -> int:
    """Mechanical starting tier for *subtask*: no model is asked to guess."""
    if not ladder.active:
        return 0
    index = 0
    if subtask.check.kind is CheckKind.judge:
        index += 1
    if len(input_paths(subtask.check, cwd)) >= _MANY_FILES:
        index += 1
    return ladder.clamp(index)
