"""Validates and normalizes tiered settlement structures for bounties."""

from __future__ import annotations

from app.schemas.assist import PerformanceTranche, ReputationStake, SettlementStructure


def default_analytical() -> SettlementStructure:
    """Immediate-value bounty: full payout on delivery."""
    return SettlementStructure(
        immediate_payout_percent=100,
        performance_tranches=None,
        reputation_stake=ReputationStake(enabled=True, weight=1.0),
    )


def default_predictive() -> SettlementStructure:
    """Forward-looking bounty: split between immediate and conditional."""
    return SettlementStructure(
        immediate_payout_percent=45,
        performance_tranches=[
            PerformanceTranche(
                percent=35,
                indicator="Analyst-defined leading indicators",
                measurement="observable_market_data",
                escrow_duration_days=365,
                partial_credit=True,
            )
        ],
        reputation_stake=ReputationStake(enabled=True, weight=1.5),
    )


def default_hybrid() -> SettlementStructure:
    """Mix of immediate deliverable value and forward-looking claims."""
    return SettlementStructure(
        immediate_payout_percent=65,
        performance_tranches=[
            PerformanceTranche(
                percent=20,
                indicator="Analyst-defined performance metrics",
                measurement="observable_data",
                escrow_duration_days=180,
                partial_credit=True,
            )
        ],
        reputation_stake=ReputationStake(enabled=True, weight=1.2),
    )


def validate_structure(structure: SettlementStructure) -> SettlementStructure:
    """Ensure percentages sum correctly and fields are within bounds."""
    total = structure.immediate_payout_percent
    if structure.performance_tranches:
        for tranche in structure.performance_tranches:
            total += tranche.percent

    if total > 100:
        scale = 100.0 / total
        structure.immediate_payout_percent = int(structure.immediate_payout_percent * scale)
        if structure.performance_tranches:
            for tranche in structure.performance_tranches:
                tranche.percent = int(tranche.percent * scale)

    if structure.reputation_stake and structure.reputation_stake.weight < 0:
        structure.reputation_stake.weight = 1.0

    return structure


def from_draft_dict(raw: dict | None) -> SettlementStructure | None:
    """Parse a settlement structure from the AI-generated draft dict."""
    if not raw:
        return None
    try:
        return validate_structure(SettlementStructure.model_validate(raw))
    except Exception:
        return None
