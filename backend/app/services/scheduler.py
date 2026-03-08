"""Background scheduler for recurring service contracts.

Runs inside the FastAPI process — polls every 60 seconds for contracts
that need new snapshot cycles or have overdue deliveries.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from croniter import croniter
from sqlalchemy import select

from app.database import async_session
from app.models.contract import ContractStatus, ServiceContract
from app.models.notification import NotificationType
from app.models.snapshot import Snapshot, SnapshotStatus
from app.services import contract_service
from app.services.notification_service import create_notification

logger = logging.getLogger(__name__)

POLL_INTERVAL = 60


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


async def run_scheduler() -> None:
    logger.info("Contract scheduler started (poll every %ds)", POLL_INTERVAL)
    while True:
        try:
            await _process_active_contracts()
        except Exception:
            logger.exception("Scheduler loop error")
        await asyncio.sleep(POLL_INTERVAL)
