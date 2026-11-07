"""Execution spend ledger (owner: S2). S0 signatures are frozen; bodies are the worker's.

Rules:
- ``reserve_spend`` is idempotent on ``idempotency_key``: a retried request returns the
  existing reservation and reserves nothing new.
- ``record_usage`` is idempotent on ``execution_id``. Cause ``platform_retry`` is
  recorded with ``charged=False`` and never moves ``spent_cents``.
- Budget counters change only under a row lock (or a conditional UPDATE on SQLite).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.spend import ExecutionUsage, SpendReservation


class BudgetExceeded(Exception):
    """The period cap, per-job cap, or concurrency cap would be exceeded."""


class BudgetPaused(Exception):
    """The account budget is paused."""


class BudgetMissing(Exception):
    """The account has no budget row."""


async def reserve_spend(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    subject: str,
    idempotency_key: str,
    cents: int,
    expires_at: datetime,
) -> SpendReservation:
    raise NotImplementedError


async def record_usage(
    db: AsyncSession,
    *,
    reservation_id: uuid.UUID,
    execution_id: str,
    cause: str,
    cpu_seconds: int,
    wall_seconds: int,
    model_tokens: int,
    cost_cents: int,
) -> ExecutionUsage:
    raise NotImplementedError


async def reconcile_reservation(db: AsyncSession, reservation_id: uuid.UUID) -> SpendReservation:
    """Close a reservation: charged usage moves to spent, the unused part is released."""
    raise NotImplementedError


async def release_reservation(db: AsyncSession, reservation_id: uuid.UUID) -> SpendReservation:
    """Release a reservation with no further usage (cancel or failure before execution)."""
    raise NotImplementedError


async def expire_reservations(db: AsyncSession, now: datetime) -> int:
    """Expire reservations past ``expires_at``; returns the count."""
    raise NotImplementedError


async def budget_status(db: AsyncSession, user_id: uuid.UUID) -> dict:
    """{"period_cap_cents","reserved_cents","spent_cents","available_cents","paused"}."""
    raise NotImplementedError
