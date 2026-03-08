from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.notification import Notification
from app.models.user import User
from app.schemas.notification import NotificationListResponse, NotificationResponse
from app.services.notification_service import get_user_notifications

router = APIRouter()


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows, total, unread = await get_user_notifications(db, user.id, limit=limit, offset=offset)
    return NotificationListResponse(
        notifications=[NotificationResponse.model_validate(n) for n in rows],
        total=total,
        unread_count=unread,
    )


@router.post("/{notification_id}/read", response_model=NotificationResponse)
async def mark_read(
    notification_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    n = (
        await db.execute(select(Notification).where(Notification.id == notification_id))
    ).scalar_one_or_none()
    if not n or n.user_id != user.id:
        raise HTTPException(status_code=404, detail="Notification not found")

    n.read = True
    n.read_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(n)
    return NotificationResponse.model_validate(n)


@router.post("/read-all")
async def mark_all_read(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import update

    await db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read == False)  # noqa: E712
        .values(read=True, read_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return {"status": "ok"}
