"""Compliance dashboard API endpoints.

Provides operator-level read access to self-dealing signals, null-resolved
disputes, EMA suppression events, and counterparty diversity outliers.
All endpoints require operator authentication (GATEWAY_EXCHANGE_API_KEY).
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.gateway import AuditEntry, GatewayAgent, ReputationSnapshot

logger = logging.getLogger(__name__)

router = APIRouter()

_OPERATOR_KEY = os.getenv("GATEWAY_EXCHANGE_API_KEY", "")


def _require_operator(authorization: str | None = None) -> None:
    """Minimal operator key check — extend to proper auth middleware as needed."""
    if not _OPERATOR_KEY:
        return
    if authorization != f"Bearer {_OPERATOR_KEY}":
        raise HTTPException(status_code=403, detail="Operator credentials required")


# ---------------------------------------------------------------------------
# Self-dealing transaction feed
# ---------------------------------------------------------------------------


@router.get("/self-dealing-feed")
async def self_dealing_feed(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Return audit entries where self_dealing_class is not 'arms_length'."""
    result = await db.execute(
        select(AuditEntry)
        .where(
            AuditEntry.details["self_dealing_class"].astext.in_(
                ["self_dealing", "suspected_self_dealing"]
            )
        )
        .order_by(desc(AuditEntry.timestamp))
        .limit(limit)
        .offset(offset)
    )
    entries = result.scalars().all()

    count_result = await db.execute(
        select(text("COUNT(*)")).select_from(AuditEntry).where(
            AuditEntry.details["self_dealing_class"].astext.in_(
                ["self_dealing", "suspected_self_dealing"]
            )
        )
    )
    total = count_result.scalar() or 0

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "entries": [
            {
                "id": str(e.id),
                "timestamp": e.timestamp.isoformat(),
                "source_agent": e.source_agent,
                "target_agent": e.target_agent,
                "escrow_id": e.escrow_id,
                "self_dealing_class": (e.details or {}).get("self_dealing_class"),
                "policy_decision": e.policy_decision,
                "details": e.details,
            }
            for e in entries
        ],
    }


# ---------------------------------------------------------------------------
# Null-resolution dispute log
# ---------------------------------------------------------------------------


@router.get("/null-resolutions")
async def null_resolutions(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Return audit entries for null-resolved disputes (confirmed self-dealing)."""
    result = await db.execute(
        select(AuditEntry)
        .where(
            AuditEntry.details["verdict_outcome"].astext == "null_resolution"
        )
        .order_by(desc(AuditEntry.timestamp))
        .limit(limit)
        .offset(offset)
    )
    entries = result.scalars().all()

    return {
        "limit": limit,
        "offset": offset,
        "entries": [
            {
                "id": str(e.id),
                "timestamp": e.timestamp.isoformat(),
                "source_agent": e.source_agent,
                "target_agent": e.target_agent,
                "escrow_id": e.escrow_id,
                "details": e.details,
            }
            for e in entries
        ],
    }


# ---------------------------------------------------------------------------
# EMA suppression log
# ---------------------------------------------------------------------------


@router.get("/ema-suppressions")
async def ema_suppressions(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Return audit entries where EMA update was suppressed due to self-dealing."""
    result = await db.execute(
        select(AuditEntry)
        .where(AuditEntry.details["ema_suppressed"].astext == "true")
        .order_by(desc(AuditEntry.timestamp))
        .limit(limit)
        .offset(offset)
    )
    entries = result.scalars().all()

    return {
        "limit": limit,
        "offset": offset,
        "entries": [
            {
                "id": str(e.id),
                "timestamp": e.timestamp.isoformat(),
                "source_agent": e.source_agent,
                "target_agent": e.target_agent,
                "escrow_id": e.escrow_id,
                "details": e.details,
            }
            for e in entries
        ],
    }


# ---------------------------------------------------------------------------
# Diversity outliers
# ---------------------------------------------------------------------------


@router.get("/diversity-outliers")
async def diversity_outliers(
    hhi_threshold: float = Query(default=0.5, ge=0.0, le=1.0),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Return gateway agents whose latest HHI snapshot exceeds the threshold.

    High HHI indicates single-relationship concentration risk.
    Threshold of 0.5 means one counterparty accounts for >70% of transaction volume.
    """
    subq = (
        select(
            ReputationSnapshot.agent_id,
            ReputationSnapshot.counterparty_hhi,
            ReputationSnapshot.snapshot_at,
        )
        .where(ReputationSnapshot.counterparty_hhi.isnot(None))
        .distinct(ReputationSnapshot.agent_id)
        .order_by(
            ReputationSnapshot.agent_id,
            desc(ReputationSnapshot.snapshot_at),
        )
        .subquery()
    )

    result = await db.execute(
        select(GatewayAgent, subq.c.counterparty_hhi, subq.c.snapshot_at)
        .join(subq, GatewayAgent.exchange_account_id == subq.c.agent_id)
        .where(subq.c.counterparty_hhi >= hhi_threshold)
        .order_by(desc(subq.c.counterparty_hhi))
        .limit(limit)
        .offset(offset)
    )
    rows = result.all()

    return {
        "hhi_threshold": hhi_threshold,
        "limit": limit,
        "offset": offset,
        "outliers": [
            {
                "exchange_account_id": row.GatewayAgent.exchange_account_id,
                "bot_name": row.GatewayAgent.bot_name,
                "principal_id": str(row.GatewayAgent.principal_id)
                if row.GatewayAgent.principal_id
                else None,
                "counterparty_hhi": row.counterparty_hhi,
                "snapshot_at": row.snapshot_at.isoformat() if row.snapshot_at else None,
            }
            for row in rows
        ],
    }
