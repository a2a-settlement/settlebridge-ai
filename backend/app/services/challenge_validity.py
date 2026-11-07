"""Finding validity (owner: P1-V). Plan section 4 is the specification.

A claim is ``confirmed`` only when every check passes, and no check depends on the
challenger's own assertion. Pure functions take recorded runner results; this module
never starts a sandbox itself.
"""

from __future__ import annotations

from app.schemas.challenge import (
    ClaimIn,
    ControlOutcome,
    DifferentialOutcome,
    RunnerResult,
    ValidationOutcome,
)


def precheck_citation(claim: ClaimIn, contract_content: dict) -> tuple[bool, str]:
    """The citation must name an oracle source listed in the frozen contract."""
    raise NotImplementedError


def evaluate_controls(positive: RunnerResult, negative: RunnerResult) -> ControlOutcome:
    raise NotImplementedError


def evaluate_differential(
    base: RunnerResult | None,
    target: RunnerResult,
    reference: RunnerResult | None,
    failure_class: str,
) -> DifferentialOutcome:
    raise NotImplementedError


def decide(
    *,
    claim: ClaimIn,
    contract_content: dict,
    tamper_clean: bool,
    target: RunnerResult,
    positive: RunnerResult,
    negative: RunnerResult,
    base: RunnerResult | None,
    reference: RunnerResult | None,
    rederivation: RunnerResult | None,
    reruns: list[RunnerResult],
    minimized: RunnerResult | None,
) -> ValidationOutcome:
    raise NotImplementedError
