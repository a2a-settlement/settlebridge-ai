"""Execution spend ledger (owner: S2). S0 signatures are frozen; bodies are the worker's.

Rules:
- ``reserve_spend`` is idempotent on ``idempotency_key``: a retried request returns the
  existing reservation and reserves nothing new.
- ``record_usage`` is idempotent on ``execution_id``. Cause ``platform_retry`` is
  recorded with ``charged=False`` and never moves ``spent_cents``.
- Charged usage never exceeds the reservation. ``ExecutionUsage.cost_cents`` keeps the
  actual cost; the part above ``reserved_cents`` is absorbed by the platform, and the
  orchestrator must stop once ``reservation_exhausted`` is true.
- Budget counters change only under a row lock (or a conditional UPDATE on SQLite).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import object_session

from app.models.spend import (
    RESERVATION_TRANSITIONS,
    AccountBudget,
    ExecutionUsage,
    ReservationStatus,
    SpendReservation,
    UsageCause,
)


class BudgetExceeded(Exception):
    """The period cap, per-job cap, or concurrency cap would be exceeded."""


class BudgetPaused(Exception):
    """The account budget is paused."""


class BudgetMissing(Exception):
    """The account has no budget row."""


def _require_transition(current: str, new: str) -> None:
    allowed = RESERVATION_TRANSITIONS.get(current, frozenset())
    if new not in allowed:
        raise ValueError(f"cannot transition reservation from {current} to {new}")


def _detach(db: AsyncSession, obj: object) -> None:
    if object_session(obj) is not None:
        db.expunge(obj)


async def _budget_for_user(db: AsyncSession, user_id: uuid.UUID) -> AccountBudget | None:
    stmt = select(AccountBudget).where(AccountBudget.user_id == user_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _lock_budget(db: AsyncSession, budget_id: uuid.UUID) -> AccountBudget | None:
    stmt = (
        select(AccountBudget)
        .where(AccountBudget.id == budget_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _reload_budget(db: AsyncSession, budget_id: uuid.UUID) -> None:
    stmt = (
        select(AccountBudget)
        .where(AccountBudget.id == budget_id)
        .execution_options(populate_existing=True)
    )
    await db.execute(stmt)


async def _reservation_by_key(db: AsyncSession, idempotency_key: str) -> SpendReservation | None:
    stmt = select(SpendReservation).where(SpendReservation.idempotency_key == idempotency_key)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _replay(
    db: AsyncSession, idempotency_key: str, budget_id: uuid.UUID
) -> SpendReservation | None:
    existing = await _reservation_by_key(db, idempotency_key)
    if existing is not None and existing.budget_id != budget_id:
        raise ValueError("idempotency_key belongs to another budget")
    return existing


async def _lock_reservation(db: AsyncSession, reservation_id: uuid.UUID) -> SpendReservation | None:
    stmt = (
        select(SpendReservation)
        .where(SpendReservation.id == reservation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _reserved_count(db: AsyncSession, budget_id: uuid.UUID) -> int:
    stmt = (
        select(func.count())
        .select_from(SpendReservation)
        .where(
            SpendReservation.budget_id == budget_id,
            SpendReservation.status == ReservationStatus.RESERVED.value,
        )
    )
    return int((await db.execute(stmt)).scalar_one())


async def _usage_by_execution(db: AsyncSession, execution_id: str) -> ExecutionUsage | None:
    stmt = select(ExecutionUsage).where(ExecutionUsage.execution_id == execution_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _claim_period(db: AsyncSession, budget_id: uuid.UUID, cents: int) -> bool:
    """Conditional period hold. Success is ``rowcount == 1`` only.

    ``SELECT FOR UPDATE`` is a no-op on SQLite, so the WHERE clause is the lock:
    the database applies at most one increment that still fits.
    """
    result = await db.execute(
        update(AccountBudget)
        .where(AccountBudget.id == budget_id)
        .where(
            AccountBudget.reserved_cents + AccountBudget.spent_cents + cents
            <= AccountBudget.period_cap_cents
        )
        .values(reserved_cents=AccountBudget.reserved_cents + cents)
    )
    if result.rowcount != 1:
        return False
    await _reload_budget(db, budget_id)
    return True


async def _give_back_hold(db: AsyncSession, budget_id: uuid.UUID, cents: int) -> None:
    result = await db.execute(
        update(AccountBudget)
        .where(AccountBudget.id == budget_id)
        .where(AccountBudget.reserved_cents >= cents)
        .values(reserved_cents=AccountBudget.reserved_cents - cents)
    )
    if result.rowcount != 1:
        raise ValueError("reserved_cents underflow")
    await _reload_budget(db, budget_id)


async def _settle_budget(
    db: AsyncSession, budget_id: uuid.UUID, hold_cents: int, spent_cents: int
) -> None:
    """Drop the hold and record charged usage. The hold must already be reserved."""
    result = await db.execute(
        update(AccountBudget)
        .where(AccountBudget.id == budget_id)
        .where(AccountBudget.reserved_cents >= hold_cents)
        .values(
            reserved_cents=AccountBudget.reserved_cents - hold_cents,
            spent_cents=AccountBudget.spent_cents + spent_cents,
        )
    )
    if result.rowcount != 1:
        raise ValueError("reserved_cents underflow")


async def _claim_status(
    db: AsyncSession, reservation_id: uuid.UUID, current: str, new: str
) -> bool:
    result = await db.execute(
        update(SpendReservation)
        .where(SpendReservation.id == reservation_id)
        .where(SpendReservation.status == current)
        .values(status=new)
    )
    return result.rowcount == 1


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def reserve_spend(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    subject: str,
    idempotency_key: str,
    cents: int,
    expires_at: datetime,
) -> SpendReservation:
    if cents <= 0:
        raise ValueError("cents must be positive")
    budget = await _budget_for_user(db, user_id)
    if budget is None:
        raise BudgetMissing
    # Replay returns the stored reservation before any cap check or counter change.
    existing = await _replay(db, idempotency_key, budget.id)
    if existing is not None:
        return existing
    budget = await _lock_budget(db, budget.id)
    if budget is None:
        raise BudgetMissing
    existing = await _replay(db, idempotency_key, budget.id)
    if existing is not None:
        return existing
    if budget.paused_at is not None:
        raise BudgetPaused
    if cents > budget.per_job_cap_cents:
        raise BudgetExceeded
    if await _reserved_count(db, budget.id) >= budget.max_concurrent_jobs:
        raise BudgetExceeded
    if not await _claim_period(db, budget.id, cents):
        raise BudgetExceeded
    reservation = SpendReservation(
        id=uuid.uuid4(),
        budget_id=budget.id,
        subject=subject,
        idempotency_key=idempotency_key,
        reserved_cents=cents,
        used_cents=0,
        status=ReservationStatus.RESERVED.value,
        expires_at=expires_at,
    )
    try:
        # Insert races the unique key. Roll the insert back and return the hold
        # in this same transaction; the winner's row is the replay result.
        async with db.begin_nested():
            db.add(reservation)
            await db.flush()
    except IntegrityError:
        _detach(db, reservation)
        await _give_back_hold(db, budget.id, cents)
        existing = await _replay(db, idempotency_key, budget.id)
        if existing is None:
            raise
        return existing
    return reservation


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
    existing = await _usage_by_execution(db, execution_id)
    if existing is not None:
        return existing
    if cost_cents < 0:
        raise ValueError("cost_cents must be non-negative")
    cause_value = UsageCause(cause).value
    charged = cause_value != UsageCause.PLATFORM_RETRY.value
    reservation = await _lock_reservation(db, reservation_id)
    if reservation is None:
        raise ValueError("reservation not found")
    existing = await _usage_by_execution(db, execution_id)
    if existing is not None:
        return existing
    if reservation.status != ReservationStatus.RESERVED.value:
        raise ValueError("reservation is not reserved")
    usage = ExecutionUsage(
        id=uuid.uuid4(),
        reservation_id=reservation.id,
        execution_id=execution_id,
        cause=cause_value,
        charged=charged,
        cpu_seconds=cpu_seconds,
        wall_seconds=wall_seconds,
        model_tokens=model_tokens,
        cost_cents=cost_cents,
    )
    try:
        async with db.begin_nested():
            db.add(usage)
            await db.flush()
            if charged and cost_cents:
                updated = await db.execute(
                    update(SpendReservation)
                    .where(SpendReservation.id == reservation.id)
                    .where(SpendReservation.status == ReservationStatus.RESERVED.value)
                    .values(
                        used_cents=case(
                            (
                                SpendReservation.used_cents + cost_cents
                                > SpendReservation.reserved_cents,
                                SpendReservation.reserved_cents,
                            ),
                            else_=SpendReservation.used_cents + cost_cents,
                        )
                    )
                )
                if updated.rowcount != 1:
                    raise ValueError("reservation is not reserved")
    except IntegrityError:
        _detach(db, usage)
        existing = await _usage_by_execution(db, execution_id)
        if existing is None:
            raise
        return existing
    except Exception:
        _detach(db, usage)
        raise
    await db.refresh(reservation, attribute_names=["used_cents"])
    return usage


def reservation_exhausted(reservation: SpendReservation) -> bool:
    """True once charged usage has reached the hold; no further work should run on it."""
    return reservation.used_cents >= reservation.reserved_cents


async def _close(db: AsyncSession, reservation_id: uuid.UUID, new_status: str) -> SpendReservation:
    reservation = await _lock_reservation(db, reservation_id)
    if reservation is None:
        raise ValueError("reservation not found")
    # A closed reservation already settled its hold, whichever way it closed.
    if reservation.status != ReservationStatus.RESERVED.value:
        return reservation
    _require_transition(reservation.status, new_status)
    hold_cents = reservation.reserved_cents
    spent_cents = reservation.used_cents
    async with db.begin_nested():
        claimed = await _claim_status(db, reservation.id, reservation.status, new_status)
        if claimed:
            await _settle_budget(db, reservation.budget_id, hold_cents, spent_cents)
    if not claimed:
        await db.refresh(reservation)
        return reservation
    await db.refresh(reservation)
    await _reload_budget(db, reservation.budget_id)
    return reservation


async def reconcile_reservation(db: AsyncSession, reservation_id: uuid.UUID) -> SpendReservation:
    """Close a reservation: charged usage moves to spent, the unused part is released."""
    return await _close(db, reservation_id, ReservationStatus.RECONCILED.value)


async def release_reservation(db: AsyncSession, reservation_id: uuid.UUID) -> SpendReservation:
    """Release a reservation, charging usage already recorded and giving back the hold."""
    return await _close(db, reservation_id, ReservationStatus.RELEASED.value)


async def expire_reservations(db: AsyncSession, now: datetime) -> int:
    """Expire reservations past ``expires_at``; returns the count."""
    due = await db.execute(
        select(SpendReservation.id).where(
            SpendReservation.status == ReservationStatus.RESERVED.value,
            SpendReservation.expires_at < now,
        )
    )
    expired = 0
    for reservation_id in due.scalars().all():
        reservation = await _lock_reservation(db, reservation_id)
        if reservation is None or reservation.status != ReservationStatus.RESERVED.value:
            continue
        if _as_utc(reservation.expires_at) >= _as_utc(now):
            continue
        _require_transition(reservation.status, ReservationStatus.EXPIRED.value)
        hold_cents = reservation.reserved_cents
        spent_cents = reservation.used_cents
        async with db.begin_nested():
            claimed = await _claim_status(
                db,
                reservation.id,
                ReservationStatus.RESERVED.value,
                ReservationStatus.EXPIRED.value,
            )
            if claimed:
                await _settle_budget(db, reservation.budget_id, hold_cents, spent_cents)
        if not claimed:
            continue
        await db.refresh(reservation)
        await _reload_budget(db, reservation.budget_id)
        expired += 1
    return expired


async def budget_status(db: AsyncSession, user_id: uuid.UUID) -> dict:
    """{"period_cap_cents","reserved_cents","spent_cents","available_cents","paused"}."""
    budget = await _budget_for_user(db, user_id)
    if budget is None:
        raise BudgetMissing
    available = budget.period_cap_cents - budget.reserved_cents - budget.spent_cents
    if available < 0:
        available = 0
    return {
        "period_cap_cents": int(budget.period_cap_cents),
        "reserved_cents": int(budget.reserved_cents),
        "spent_cents": int(budget.spent_cents),
        "available_cents": int(available),
        "paused": budget.paused_at is not None,
    }
