"""Per-installation fix-verifier quota and per-repo hourly rate limit.

A verification may start only when today's UTC row has ``used`` below
``FIX_VERIFIER_DAILY_QUOTA`` and ``spend_cents`` plus the per-verification
budget still fits under ``FIX_VERIFIER_DAILY_COST_CAP_CENTS``, and the repo
has fewer than ``FIX_VERIFIER_RATE_LIMIT_PER_HOUR`` rows created in the last
hour. The daily slot is a conditional ``UPDATE`` (``SELECT ... FOR UPDATE``
on databases that support it). ``FOR UPDATE`` is a no-op on SQLite, so the
``WHERE`` clause is the lock, matching the spend ledger.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import object_session

from app.config import settings
from app.models.fix_verifier import FixVerification, VerifierQuota

_HOUR = timedelta(hours=1)


class QuotaExceeded(Exception):
    """Daily quota, daily spend cap, or the hourly per-repo rate limit."""

    def __init__(self, retry_after: int, *, reason: str):
        self.retry_after = max(1, int(retry_after))
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class QuotaReservation:
    """One consumed daily slot. Spend is charged later with the actual cost."""

    installation_id: int
    period: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _positive_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value if value > 0 else 0


def _period(now: datetime) -> str:
    return _as_utc(now).strftime("%Y-%m-%d")


def _seconds_until_next_utc_day(now: datetime) -> int:
    moment = _as_utc(now)
    tomorrow = datetime(moment.year, moment.month, moment.day, tzinfo=timezone.utc) + timedelta(
        days=1
    )
    return max(1, int((tomorrow - moment).total_seconds()))


def _rate_limit_retry_after(created: list[datetime], now: datetime, limit: int) -> int:
    """Seconds until enough of the current hour's rows age out to admit one more."""
    if not created:
        return 3600
    # Drop (count - limit + 1) rows, which is index (count - limit) once sorted.
    index = max(0, len(created) - max(limit, 0))
    index = min(index, len(created) - 1)
    moment = _as_utc(created[index])
    seconds = int((moment + _HOUR - _as_utc(now)).total_seconds())
    return max(1, seconds)


def _detach(db, obj: object) -> None:
    if object_session(obj) is not None:
        db.expunge(obj)


async def _recent_created_at(db, repo_full_name: str, now: datetime) -> list[datetime]:
    cutoff = _as_utc(now) - _HOUR
    stmt = (
        select(FixVerification.created_at)
        .where(FixVerification.repo_full_name == repo_full_name)
        .where(FixVerification.created_at >= cutoff)
        .order_by(FixVerification.created_at.asc())
    )
    values = (await db.execute(stmt)).scalars().all()
    return [_as_utc(value) for value in values if isinstance(value, datetime)]


async def _enforce_rate_limit(db, repo_full_name: str, now: datetime) -> None:
    limit = _positive_int(settings.FIX_VERIFIER_RATE_LIMIT_PER_HOUR)
    created = await _recent_created_at(db, repo_full_name, now)
    if limit <= 0 or len(created) >= limit:
        raise QuotaExceeded(
            _rate_limit_retry_after(created, now, limit),
            reason="rate_limit",
        )


async def _quota_row(db, installation_id: int, period: str) -> VerifierQuota | None:
    stmt = select(VerifierQuota).where(
        VerifierQuota.installation_id == installation_id,
        VerifierQuota.period == period,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _ensure_quota(db, installation_id: int, period: str, now: datetime) -> None:
    if await _quota_row(db, installation_id, period) is not None:
        return
    row = VerifierQuota(
        id=uuid.uuid4(),
        installation_id=installation_id,
        period=period,
        used=0,
        limit=_positive_int(settings.FIX_VERIFIER_DAILY_QUOTA),
        spend_cents=0,
        updated_at=now,
    )
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except IntegrityError:
        _detach(db, row)


async def _lock_quota(db, installation_id: int, period: str) -> None:
    stmt = (
        select(VerifierQuota)
        .where(VerifierQuota.installation_id == installation_id)
        .where(VerifierQuota.period == period)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    await db.execute(stmt)


async def _refresh_quota(db, installation_id: int, period: str) -> None:
    stmt = (
        select(VerifierQuota)
        .where(VerifierQuota.installation_id == installation_id)
        .where(VerifierQuota.period == period)
        .execution_options(populate_existing=True)
    )
    await db.execute(stmt)


async def _claim_slot(db, installation_id: int, period: str) -> bool:
    daily = _positive_int(settings.FIX_VERIFIER_DAILY_QUOTA)
    cap = _positive_int(settings.FIX_VERIFIER_DAILY_COST_CAP_CENTS)
    budget = _positive_int(settings.FIX_VERIFIER_MAX_COST_CENTS_PER_VERIFICATION)
    if daily <= 0:
        return False
    stmt = (
        update(VerifierQuota)
        .where(VerifierQuota.installation_id == installation_id)
        .where(VerifierQuota.period == period)
        .where(VerifierQuota.used < daily)
        .where(VerifierQuota.spend_cents + budget <= cap)
        .values(used=VerifierQuota.used + 1)
        .returning(VerifierQuota.id)
    )
    claimed = (await db.execute(stmt)).first()
    await _refresh_quota(db, installation_id, period)
    return claimed is not None


async def reserve_verification_slot(
    db, *, installation_id: int, repo_full_name: str
) -> QuotaReservation:
    """Take one daily slot, or raise ``QuotaExceeded`` with ``retry_after`` seconds.

    The hourly count is checked before the slot is taken, then again under the
    quota-row lock. Nothing is consumed when either check fails.
    """
    now = _utcnow()
    await _enforce_rate_limit(db, repo_full_name, now)
    period = _period(now)
    await _ensure_quota(db, installation_id, period, now)
    await _lock_quota(db, installation_id, period)
    await _enforce_rate_limit(db, repo_full_name, now)
    if not await _claim_slot(db, installation_id, period):
        raise QuotaExceeded(_seconds_until_next_utc_day(now), reason="daily_cap")
    return QuotaReservation(installation_id=int(installation_id), period=period)


async def release_verification_slot(db, reservation: QuotaReservation) -> None:
    """Return a slot that will not call the verifier (cache race or out-of-scope)."""
    stmt = (
        update(VerifierQuota)
        .where(VerifierQuota.installation_id == reservation.installation_id)
        .where(VerifierQuota.period == reservation.period)
        .where(VerifierQuota.used > 0)
        .values(used=VerifierQuota.used - 1)
        .returning(VerifierQuota.id)
    )
    await db.execute(stmt)
    await _refresh_quota(db, reservation.installation_id, reservation.period)


async def charge_verification_spend(db, reservation: QuotaReservation, cents: int) -> None:
    """Add the verifier's actual cost to today's spend. Zero is a no-op."""
    if isinstance(cents, bool) or not isinstance(cents, int) or cents <= 0:
        return
    stmt = (
        update(VerifierQuota)
        .where(VerifierQuota.installation_id == reservation.installation_id)
        .where(VerifierQuota.period == reservation.period)
        .values(spend_cents=VerifierQuota.spend_cents + cents)
        .returning(VerifierQuota.id)
    )
    charged = (await db.execute(stmt)).first()
    await _refresh_quota(db, reservation.installation_id, reservation.period)
    if charged is None:
        raise RuntimeError("verifier quota row missing")
