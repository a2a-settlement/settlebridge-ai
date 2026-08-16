"""Background scheduler for recurring service contracts and stale bounty cleanup.

Runs inside the FastAPI process — polls every 60 seconds for contracts
that need new snapshot cycles or have overdue deliveries. Roughly once
per hour it also expires marketplace bounties whose exchange escrow is
already terminal (expired/refunded), without touching partially_released
efficacy holdbacks.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import async_session
from app.models.bounty import Bounty, BountyStatus
from app.models.claim import Claim, ClaimStatus
from app.models.contract import ContractStatus, ServiceContract
from app.models.notification import NotificationType
from app.models.snapshot import Snapshot, SnapshotStatus
from app.models.submission import Submission, SubmissionStatus
from app.models.user import User
from app.services import contract_service, exchange as exchange_svc
from app.services.notification_service import create_notification

logger = logging.getLogger(__name__)

POLL_INTERVAL = 60
# Run the stale-bounty sweep every N contract-scheduler ticks (~1 hour).
STALE_SWEEP_EVERY_N = 60
_STALE_SWEEP_TICK = 0

STALE_ACTIVE_STATUSES = (
    BountyStatus.CLAIMED,
    BountyStatus.SUBMITTED,
    BountyStatus.IN_REVIEW,
)
DEADLINE_GRACE = timedelta(days=7)
NO_DEADLINE_AGE = timedelta(days=30)
STALE_NOTES = (
    "Escrow expired/refunded on exchange before requester review; "
    "auto-expired by scheduler"
)


async def _process_active_contracts() -> None:
    async with async_session() as db:
        result = await db.execute(
            select(ServiceContract).where(ServiceContract.status == ContractStatus.ACTIVE)
        )
        contracts = result.scalars().all()

        now = datetime.now(timezone.utc)

        for contract in contracts:
            try:
                await _check_contract_cycle(db, contract, now)
                await _check_overdue_snapshots(db, contract, now)
            except Exception:
                logger.exception("Error processing contract %s", contract.id)

        await db.commit()


async def _check_contract_cycle(
    db, contract: ServiceContract, now: datetime
) -> None:
    if contract.max_snapshots:
        completed = await contract_service.count_completed_snapshots(db, contract.id)
        if completed >= contract.max_snapshots:
            await contract_service.complete_contract(db, contract)
            return

    has_pending = await contract_service.has_pending_snapshot(db, contract.id)
    if has_pending:
        return

    base_time = contract.activated_at or contract.created_at
    cron = croniter(contract.schedule, base_time)

    latest_cycle = await contract_service.get_latest_cycle_number(db, contract.id)

    next_due = cron.get_next(datetime)
    for _ in range(latest_cycle):
        next_due = cron.get_next(datetime)

    if next_due <= now:
        cycle_number = latest_cycle + 1
        deadline = next_due + timedelta(hours=contract.grace_period_hours)

        escrow_id = None

        snapshot = await contract_service.create_snapshot(
            db,
            contract_id=contract.id,
            cycle_number=cycle_number,
            escrow_id=escrow_id,
            due_at=next_due,
            deadline_at=deadline,
        )

        await create_notification(
            db,
            user_id=contract.agent_user_id,
            type=NotificationType.SNAPSHOT_DUE,
            title="Snapshot Due",
            message=f'Cycle {cycle_number} for "{contract.title}" is due. Deadline: {deadline.isoformat()}.',
            reference_id=snapshot.id,
        )

        logger.info(
            "Created snapshot cycle %d for contract %s (due %s)",
            cycle_number, contract.id, next_due.isoformat(),
        )


async def _check_overdue_snapshots(
    db, contract: ServiceContract, now: datetime
) -> None:
    result = await db.execute(
        select(Snapshot).where(
            Snapshot.contract_id == contract.id,
            Snapshot.status == SnapshotStatus.PENDING,
            Snapshot.deadline_at < now,
        )
    )
    overdue = result.scalars().all()

    for snapshot in overdue:
        await contract_service.miss_snapshot(db, snapshot)
        await create_notification(
            db,
            user_id=contract.agent_user_id,
            type=NotificationType.SNAPSHOT_MISSED,
            title="Snapshot Missed",
            message=f'Cycle {snapshot.cycle_number} for "{contract.title}" was missed.',
            reference_id=snapshot.id,
        )
        await create_notification(
            db,
            user_id=contract.requester_id,
            type=NotificationType.SNAPSHOT_MISSED,
            title="Snapshot Missed",
            message=f'Agent missed cycle {snapshot.cycle_number} for "{contract.title}".',
            reference_id=snapshot.id,
        )
        logger.info(
            "Marked snapshot %s (cycle %d) as missed for contract %s",
            snapshot.id, snapshot.cycle_number, contract.id,
        )


def _past_grace(bounty: Bounty, now: datetime) -> bool:
    if bounty.deadline is not None:
        return bounty.deadline + DEADLINE_GRACE < now
    created = bounty.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return created + NO_DEADLINE_AGE < now


async def _expire_stale_in_review() -> None:
    """Expire mid-flight bounties whose exchange escrow is already terminal.

    Never touches partially_released (healthy efficacy holdbacks).
    """
    now = datetime.now(timezone.utc)
    async with async_session() as db:
        result = await db.execute(
            select(Bounty)
            .options(selectinload(Bounty.requester))
            .where(
                Bounty.status.in_(STALE_ACTIVE_STATUSES),
                Bounty.escrow_id.isnot(None),
                Bounty.escrow_id != "pending_claim",
            )
        )
        candidates = [b for b in result.scalars().all() if _past_grace(b, now)]
        if not candidates:
            return

        expired_n = 0
        for bounty in candidates:
            requester: User | None = bounty.requester
            api_key = requester.exchange_api_key if requester else None
            status = exchange_svc.get_escrow_status(bounty.escrow_id, api_key)
            if status not in exchange_svc.TERMINAL_ESCROW_STATUSES:
                continue

            bounty.status = BountyStatus.EXPIRED
            bounty.escrow_id = None

            subs = (
                await db.execute(
                    select(Submission).where(
                        Submission.bounty_id == bounty.id,
                        Submission.status == SubmissionStatus.PENDING_REVIEW,
                    )
                )
            ).scalars().all()
            for sub in subs:
                sub.status = SubmissionStatus.REJECTED
                sub.reviewer_notes = STALE_NOTES
                sub.reviewed_at = now

            claims = (
                await db.execute(
                    select(Claim).where(
                        Claim.bounty_id == bounty.id,
                        Claim.status.in_((ClaimStatus.ACTIVE, ClaimStatus.SUBMITTED)),
                    )
                )
            ).scalars().all()
            for claim in claims:
                claim.status = ClaimStatus.REJECTED
                claim.resolved_at = now

            expired_n += 1
            logger.info(
                "Auto-expired bounty %s (escrow was %s): %s",
                bounty.id,
                status,
                bounty.title[:60],
            )

        if expired_n:
            await db.commit()
            logger.info("Stale-bounty sweep expired %d bounties", expired_n)


async def run_scheduler() -> None:
    global _STALE_SWEEP_TICK
    logger.info("Contract scheduler started (poll every %ds)", POLL_INTERVAL)
    while True:
        try:
            await _process_active_contracts()
        except Exception:
            logger.exception("Scheduler loop error")

        _STALE_SWEEP_TICK += 1
        if _STALE_SWEEP_TICK >= STALE_SWEEP_EVERY_N:
            _STALE_SWEEP_TICK = 0
            try:
                await _expire_stale_in_review()
            except Exception:
                logger.exception("Stale-bounty sweep error")

        await asyncio.sleep(POLL_INTERVAL)
