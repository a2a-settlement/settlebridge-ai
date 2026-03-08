from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.contract import ContractStatus, ServiceContract
from app.models.snapshot import Snapshot, SnapshotStatus


async def create_contract(
    db: AsyncSession,
    *,
    requester_id: uuid.UUID,
    agent_user_id: uuid.UUID,
    agent_exchange_bot_id: str,
    title: str,
    description: str,
    category_id: uuid.UUID | None = None,
    acceptance_criteria: dict | None = None,
    provenance_tier: str,
    reward_per_snapshot: int,
    schedule: str,
    schedule_description: str,
    max_snapshots: int | None = None,
    grace_period_hours: int = 24,
    auto_approve: bool = True,
    group_id: str,
) -> ServiceContract:
    contract = ServiceContract(
        requester_id=requester_id,
        agent_user_id=agent_user_id,
        agent_exchange_bot_id=agent_exchange_bot_id,
        title=title,
        description=description,
        category_id=category_id,
        acceptance_criteria=acceptance_criteria,
        provenance_tier=provenance_tier,
        reward_per_snapshot=reward_per_snapshot,
        schedule=schedule,
        schedule_description=schedule_description,
        max_snapshots=max_snapshots,
        grace_period_hours=grace_period_hours,
        auto_approve=auto_approve,
        group_id=group_id,
    )
    db.add(contract)
    await db.flush()
    return contract


async def get_contract(db: AsyncSession, contract_id: uuid.UUID) -> ServiceContract | None:
    result = await db.execute(
        select(ServiceContract)
        .options(selectinload(ServiceContract.snapshots))
        .where(ServiceContract.id == contract_id)
    )
    return result.scalar_one_or_none()


async def list_contracts(
    db: AsyncSession,
    *,
    status: ContractStatus | None = None,
    requester_id: uuid.UUID | None = None,
    agent_user_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[ServiceContract], int]:
    q = select(ServiceContract).options(selectinload(ServiceContract.snapshots))
    count_q = select(func.count()).select_from(ServiceContract)

    if status:
        q = q.where(ServiceContract.status == status)
        count_q = count_q.where(ServiceContract.status == status)
    if requester_id:
        q = q.where(ServiceContract.requester_id == requester_id)
        count_q = count_q.where(ServiceContract.requester_id == requester_id)
    if agent_user_id:
        q = q.where(ServiceContract.agent_user_id == agent_user_id)
        count_q = count_q.where(ServiceContract.agent_user_id == agent_user_id)

    total = (await db.execute(count_q)).scalar() or 0

    q = q.order_by(ServiceContract.created_at.desc()).offset(offset).limit(limit)
    rows = (await db.execute(q)).scalars().all()
    return list(rows), total


async def activate_contract(db: AsyncSession, contract: ServiceContract) -> None:
    contract.status = ContractStatus.ACTIVE
    contract.activated_at = datetime.now(timezone.utc)
    await db.flush()


async def pause_contract(db: AsyncSession, contract: ServiceContract) -> None:
    contract.status = ContractStatus.PAUSED
    await db.flush()


async def resume_contract(db: AsyncSession, contract: ServiceContract) -> None:
    contract.status = ContractStatus.ACTIVE
    await db.flush()


async def cancel_contract(db: AsyncSession, contract: ServiceContract) -> None:
    contract.status = ContractStatus.CANCELLED
    contract.cancelled_at = datetime.now(timezone.utc)
    await db.flush()


async def complete_contract(db: AsyncSession, contract: ServiceContract) -> None:
    contract.status = ContractStatus.COMPLETED
    await db.flush()


async def create_snapshot(
    db: AsyncSession,
    *,
    contract_id: uuid.UUID,
    cycle_number: int,
    escrow_id: str | None = None,
    due_at: datetime,
    deadline_at: datetime,
) -> Snapshot:
    snapshot = Snapshot(
        contract_id=contract_id,
        cycle_number=cycle_number,
        escrow_id=escrow_id,
        due_at=due_at,
        deadline_at=deadline_at,
    )
    db.add(snapshot)
    await db.flush()
    return snapshot


async def get_snapshot(db: AsyncSession, snapshot_id: uuid.UUID) -> Snapshot | None:
    return (
        await db.execute(select(Snapshot).where(Snapshot.id == snapshot_id))
    ).scalar_one_or_none()


async def list_snapshots(
    db: AsyncSession,
    contract_id: uuid.UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Snapshot], int]:
    count_q = (
        select(func.count())
        .select_from(Snapshot)
        .where(Snapshot.contract_id == contract_id)
    )
    total = (await db.execute(count_q)).scalar() or 0

    q = (
        select(Snapshot)
        .where(Snapshot.contract_id == contract_id)
        .order_by(Snapshot.cycle_number.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = (await db.execute(q)).scalars().all()
    return list(rows), total


async def deliver_snapshot(
    db: AsyncSession,
    snapshot: Snapshot,
    *,
    deliverable: dict,
    provenance: dict | None = None,
) -> None:
    snapshot.deliverable = deliverable
    snapshot.provenance = provenance
    snapshot.status = SnapshotStatus.DELIVERED
    snapshot.delivered_at = datetime.now(timezone.utc)
    await db.flush()


async def approve_snapshot(
    db: AsyncSession, snapshot: Snapshot, *, notes: str | None = None
) -> None:
    snapshot.status = SnapshotStatus.APPROVED
    snapshot.approved_at = datetime.now(timezone.utc)
    snapshot.reviewer_notes = notes
    await db.flush()


async def reject_snapshot(
    db: AsyncSession, snapshot: Snapshot, *, notes: str | None = None
) -> None:
    snapshot.status = SnapshotStatus.REJECTED
    snapshot.reviewer_notes = notes
    await db.flush()


async def dispute_snapshot(db: AsyncSession, snapshot: Snapshot) -> None:
    snapshot.status = SnapshotStatus.DISPUTED
    await db.flush()


async def miss_snapshot(db: AsyncSession, snapshot: Snapshot) -> None:
    snapshot.status = SnapshotStatus.MISSED
    await db.flush()


async def get_latest_cycle_number(db: AsyncSession, contract_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.coalesce(func.max(Snapshot.cycle_number), 0))
        .where(Snapshot.contract_id == contract_id)
    )
    return result.scalar() or 0


async def count_completed_snapshots(db: AsyncSession, contract_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(Snapshot)
        .where(
            Snapshot.contract_id == contract_id,
            Snapshot.status == SnapshotStatus.APPROVED,
        )
    )
    return result.scalar() or 0


async def has_pending_snapshot(db: AsyncSession, contract_id: uuid.UUID) -> bool:
    result = await db.execute(
        select(func.count())
        .select_from(Snapshot)
        .where(
            Snapshot.contract_id == contract_id,
            Snapshot.status == SnapshotStatus.PENDING,
        )
    )
    return (result.scalar() or 0) > 0
