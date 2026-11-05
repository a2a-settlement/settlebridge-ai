"""Condition grid. Primary estimand is A/B/C among three agents.

Single-agent and baseline-versus-strengthened comparisons are scheduled
separately. They are not folded into the primary denominator.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

PRIMARY_CONDITIONS = ("A", "B", "C")
SOLVABILITY = ("solvable", "impossible")


@dataclass(frozen=True)
class Trial:
    condition: str
    solvability: str
    channel: str
    collaboration: str
    model_id: str
    block: int
    comparison: str


def primary_grid(model_ids: list[str], *, seed: int) -> list[Trial]:
    """Interleave models across task blocks. The seed is recorded."""
    rng = random.Random(seed)
    trials: list[Trial] = []
    block = 0
    for solvability in SOLVABILITY:
        order = list(model_ids)
        rng.shuffle(order)
        for model_id in order:
            for condition, channel, collaboration in (
                ("A", "off", "prohibited"),
                ("B", "on", "prohibited"),
                ("C", "on", "authorized"),
            ):
                trials.append(
                    Trial(
                        condition,
                        solvability,
                        channel,
                        collaboration,
                        model_id,
                        block,
                        "primary_abc",
                    )
                )
            block += 1
    return trials


def validate_impossible(solvability: str, missing_prerequisite: str | None) -> None:
    if solvability == "impossible" and not missing_prerequisite:
        raise ValueError("an impossible task must document its missing prerequisite")


def scheduled_comparisons(model_id: str) -> list[Trial]:
    return [
        Trial("single", "solvable", "off", "prohibited", model_id, 0, "single_agent"),
        Trial(
            "strengthened",
            "solvable",
            "on",
            "prohibited",
            model_id,
            0,
            "control_profile",
        ),
    ]
