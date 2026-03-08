from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.contract import ContractStatus
from app.models.notification import NotificationType
from app.models.snapshot import SnapshotStatus
from app.models.user import User
from app.schemas.contract import (
    ContractCreateRequest,
    ContractListResponse,
    ContractResponse,
    DisputeRequest,
    ReviewRequest,
    SnapshotDeliverRequest,
    SnapshotListResponse,
    SnapshotResponse,
)
from app.services import contract_service, exchange as exchange_svc, mediator as mediator_svc
from app.services.notification_service import create_notification
from app.services.submission_service import validate_provenance
from app.utils.helpers import compute_content_hash

router = APIRouter()
logger = logging.getLogger(__name__)


def _contract_to_response(contract, snapshot_count: int = 0) -> ContractResponse:
    data = ContractResponse.model_validate(contract)
    data.snapshot_count = snapshot_count
    return data


@router.post("", response_model=ContractResponse, status_code=status.HTTP_201_CREATED)
async def create_contract(
    body: ContractCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group_id = f"contract-{uuid.uuid4().hex[:12]}"
    contract = await contract_service.create_contract(
        db,
        requester_id=user.id,
        agent_user_id=body.agent_user_id,
        agent_exchange_bot_id=body.agent_exchange_bot_id,
        title=body.title,
        description=body.description,
        category_id=body.category_id,
        acceptance_criteria=body.acceptance_criteria.model_dump() if body.acceptance_criteria else None,
        provenance_tier=body.provenance_tier,
        reward_per_snapshot=body.reward_per_snapshot,
        schedule=body.schedule,
        schedule_description=body.schedule_description,
        max_snapshots=body.max_snapshots,
        grace_period_hours=body.grace_period_hours,
        auto_approve=body.auto_approve,
        group_id=group_id,
    )

    await create_notification(
        db,
        user_id=body.agent_user_id,
        type=NotificationType.CONTRACT_CREATED,
        title="New Service Contract",
        message=f'You\'ve been assigned to contract "{body.title}".',
        reference_id=contract.id,
    )

    await db.commit()
    await db.refresh(contract)
    return _contract_to_response(contract)


@router.get("", response_model=ContractListResponse)
async def list_contracts(
    status_filter: ContractStatus | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    contracts, total = await contract_service.list_contracts(
        db, status=status_filter, limit=limit, offset=offset
    )
    return ContractListResponse(
        contracts=[_contract_to_response(c, len(c.snapshots) if hasattr(c, 'snapshots') and c.snapshots else 0) for c in contracts],
        total=total,
    )


@router.get("/my/created", response_model=ContractListResponse)
async def my_created_contracts(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    contracts, total = await contract_service.list_contracts(db, requester_id=user.id)
    return ContractListResponse(
        contracts=[_contract_to_response(c) for c in contracts],
        total=total,
    )


@router.get("/my/assigned", response_model=ContractListResponse)
async def my_assigned_contracts(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    contracts, total = await contract_service.list_contracts(db, agent_user_id=user.id)
    return ContractListResponse(
        contracts=[_contract_to_response(c) for c in contracts],
        total=total,
    )


@router.get("/{contract_id}", response_model=ContractResponse)
async def get_contract(
    contract_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    contract = await contract_service.get_contract(db, contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    snap_count = len(contract.snapshots) if contract.snapshots else 0
    return _contract_to_response(contract, snap_count)


@router.post("/{contract_id}/activate", response_model=ContractResponse)
async def activate_contract(
    contract_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    contract = await contract_service.get_contract(db, contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    if contract.requester_id != user.id:
        raise HTTPException(status_code=403, detail="Not your contract")
    if contract.status != ContractStatus.DRAFT:
        raise HTTPException(status_code=400, detail="Contract must be in draft status to activate")

    await contract_service.activate_contract(db, contract)

    await create_notification(
        db,
        user_id=contract.agent_user_id,
        type=NotificationType.CONTRACT_ACTIVATED,
        title="Contract Activated",
        message=f'Contract "{contract.title}" is now active. Deliveries begin per schedule.',
        reference_id=contract.id,
    )

    await db.commit()
    await db.refresh(contract)
    return _contract_to_response(contract)


@router.post("/{contract_id}/pause", response_model=ContractResponse)
async def pause_contract(
    contract_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    contract = await contract_service.get_contract(db, contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    if contract.requester_id != user.id:
        raise HTTPException(status_code=403, detail="Not your contract")
    if contract.status != ContractStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Contract must be active to pause")

    await contract_service.pause_contract(db, contract)
    await db.commit()
    await db.refresh(contract)
    return _contract_to_response(contract)


@router.post("/{contract_id}/resume", response_model=ContractResponse)
async def resume_contract(
    contract_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    contract = await contract_service.get_contract(db, contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    if contract.requester_id != user.id:
        raise HTTPException(status_code=403, detail="Not your contract")
    if contract.status != ContractStatus.PAUSED:
        raise HTTPException(status_code=400, detail="Contract must be paused to resume")

    await contract_service.resume_contract(db, contract)
    await db.commit()
    await db.refresh(contract)
    return _contract_to_response(contract)


@router.post("/{contract_id}/cancel", response_model=ContractResponse)
async def cancel_contract(
    contract_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    contract = await contract_service.get_contract(db, contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    if contract.requester_id != user.id:
        raise HTTPException(status_code=403, detail="Not your contract")
    if contract.status in (ContractStatus.CANCELLED, ContractStatus.COMPLETED):
        raise HTTPException(status_code=400, detail="Contract is already finished")

    await contract_service.cancel_contract(db, contract)

    await create_notification(
        db,
        user_id=contract.agent_user_id,
        type=NotificationType.CONTRACT_CANCELLED,
        title="Contract Cancelled",
        message=f'Contract "{contract.title}" has been cancelled.',
        reference_id=contract.id,
    )

    await db.commit()
    await db.refresh(contract)
    return _contract_to_response(contract)


# --- Snapshot endpoints ---


@router.get("/{contract_id}/snapshots", response_model=SnapshotListResponse)
async def list_snapshots(
    contract_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    contract = await contract_service.get_contract(db, contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    snapshots, total = await contract_service.list_snapshots(
        db, contract_id, limit=limit, offset=offset
    )
    return SnapshotListResponse(
        snapshots=[SnapshotResponse.model_validate(s) for s in snapshots],
        total=total,
    )


@router.post("/snapshots/{snapshot_id}/deliver", response_model=SnapshotResponse)
async def deliver_snapshot(
    snapshot_id: uuid.UUID,
    body: SnapshotDeliverRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    snapshot = await contract_service.get_snapshot(db, snapshot_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    contract = await contract_service.get_contract(db, snapshot.contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    if contract.agent_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your contract")
    if snapshot.status != SnapshotStatus.PENDING:
        raise HTTPException(status_code=400, detail="Snapshot is not pending delivery")

    errors = validate_provenance(body.provenance, contract.provenance_tier)
    if errors:
        raise HTTPException(status_code=400, detail={"provenance_errors": errors})

    deliverable = {
        "content": body.content,
        "content_type": body.content_type,
        "attachments": body.attachments,
        "metadata": body.metadata,
    }

    if snapshot.escrow_id:
        try:
            content_hash = compute_content_hash(body.content)
            exchange_svc.deliver(
                user,
                escrow_id=snapshot.escrow_id,
                content=body.content,
                content_hash=content_hash,
                provenance=body.provenance,
            )
        except Exception as exc:
            logger.warning("SDK deliver call failed (non-fatal): %s", exc)

    await contract_service.deliver_snapshot(
        db, snapshot, deliverable=deliverable, provenance=body.provenance
    )

    await create_notification(
        db,
        user_id=contract.requester_id,
        type=NotificationType.SNAPSHOT_DELIVERED,
        title="Snapshot Delivered",
        message=f'Cycle {snapshot.cycle_number} delivered for "{contract.title}".',
        reference_id=snapshot.id,
    )

    if contract.auto_approve and snapshot.escrow_id:
        try:
            audit = await mediator_svc.trigger_mediation(snapshot.escrow_id)
            verdict = audit.get("verdict", {})
            if verdict.get("outcome") == "auto_release" and verdict.get("confidence", 0) >= 0.8:
                exchange_svc.release_escrow(user, snapshot.escrow_id)
                await contract_service.approve_snapshot(db, snapshot, notes="Auto-approved by mediator")
                await create_notification(
                    db,
                    user_id=contract.requester_id,
                    type=NotificationType.SNAPSHOT_DELIVERED,
                    title="Snapshot Auto-Approved",
                    message=f'Cycle {snapshot.cycle_number} for "{contract.title}" was auto-approved.',
                    reference_id=snapshot.id,
                )
        except Exception as exc:
            logger.warning("Auto-approval mediation failed (falling back to manual): %s", exc)

    if contract.max_snapshots:
        completed = await contract_service.count_completed_snapshots(db, contract.id)
        if completed >= contract.max_snapshots:
            await contract_service.complete_contract(db, contract)

    await db.commit()
    await db.refresh(snapshot)
    return SnapshotResponse.model_validate(snapshot)


@router.post("/snapshots/{snapshot_id}/approve", response_model=SnapshotResponse)
async def approve_snapshot(
    snapshot_id: uuid.UUID,
    body: ReviewRequest | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    snapshot = await contract_service.get_snapshot(db, snapshot_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    contract = await contract_service.get_contract(db, snapshot.contract_id)
    if not contract or contract.requester_id != user.id:
        raise HTTPException(status_code=403, detail="Not your contract")
    if snapshot.status != SnapshotStatus.DELIVERED:
        raise HTTPException(status_code=400, detail="Snapshot must be in delivered status")

    if snapshot.escrow_id:
        try:
            exchange_svc.release_escrow(user, snapshot.escrow_id)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Failed to release escrow: {exc}")

    notes = body.notes if body else None
    await contract_service.approve_snapshot(db, snapshot, notes=notes)

    await create_notification(
        db,
        user_id=contract.agent_user_id,
        type=NotificationType.SNAPSHOT_DELIVERED,
        title="Snapshot Approved",
        message=f'Cycle {snapshot.cycle_number} for "{contract.title}" approved. Payment released.',
        reference_id=snapshot.id,
    )

    if contract.max_snapshots:
        completed = await contract_service.count_completed_snapshots(db, contract.id)
        if completed >= contract.max_snapshots:
            await contract_service.complete_contract(db, contract)

    await db.commit()
    await db.refresh(snapshot)
    return SnapshotResponse.model_validate(snapshot)


@router.post("/snapshots/{snapshot_id}/dispute", response_model=SnapshotResponse)
async def dispute_snapshot(
    snapshot_id: uuid.UUID,
    body: DisputeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    snapshot = await contract_service.get_snapshot(db, snapshot_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    contract = await contract_service.get_contract(db, snapshot.contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    is_requester = contract.requester_id == user.id
    is_agent = contract.agent_user_id == user.id
    if not is_requester and not is_agent:
        raise HTTPException(status_code=403, detail="Not a party to this contract")

    if snapshot.escrow_id:
        try:
            exchange_svc.dispute_escrow(user, snapshot.escrow_id, reason=body.reason)
        except Exception as exc:
            logger.warning("SDK dispute call failed (non-fatal): %s", exc)

    await contract_service.dispute_snapshot(db, snapshot)

    notify_user_id = contract.requester_id if is_agent else contract.agent_user_id
    await create_notification(
        db,
        user_id=notify_user_id,
        type=NotificationType.DISPUTE_FILED,
        title="Snapshot Disputed",
        message=f'Cycle {snapshot.cycle_number} for "{contract.title}" disputed: {body.reason}',
        reference_id=snapshot.id,
    )

    await db.commit()
    await db.refresh(snapshot)
    return SnapshotResponse.model_validate(snapshot)
