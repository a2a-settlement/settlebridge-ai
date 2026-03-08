from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification, NotificationType


async def create_notification(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    type: NotificationType,
    title: str,
    message: str,
    reference_id: uuid.UUID | None = None,
) -> Notification:
    notification = Notification(
        user_id=user_id,
        type=type,
        title=title,
        message=message,
        reference_id=reference_id,
    )
    db.add(notification)
    await db.flush()
    return notification


async def get_user_notifications(
    db: AsyncSession, user_id: uuid.UUID, *, limit: int = 50, offset: int = 0
) -> tuple[list[Notification], int, int]:
    total_q = select(func.count()).select_from(Notification).where(Notification.user_id == user_id)
    total = (await db.execute(total_q)).scalar() or 0

    unread_q = (
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user_id, Notification.read == False)  # noqa: E712
    )
    unread_count = (await db.execute(unread_q)).scalar() or 0

    q = (
        select(Notification)
        .where(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = (await db.execute(q)).scalars().all()
    return list(rows), total, unread_count
