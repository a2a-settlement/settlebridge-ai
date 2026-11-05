"""Supplier calls request_payment before a bound acceptance record exists.

Baseline pays. Strengthened leaves the ledger unchanged. The actor does
not hold an exchange credential.
"""

from __future__ import annotations

from pathlib import Path

from assurance.actors.scripted import premature_payment_actor, team_actors
from assurance.controller import run_scripted
from assurance.schemas import AssessmentResult, ScenarioSpec


def run_premature_payment(
    scenario: ScenarioSpec,
    *,
    team: bool = False,
    output_dir: Path | None = None,
    run_id: str = "premature-payment",
) -> AssessmentResult:
    actors = team_actors() if team else [premature_payment_actor()]
    return run_scripted(
        scenario,
        actors,
        approval_bound=False,
        output_dir=output_dir,
        run_id=run_id,
    )
