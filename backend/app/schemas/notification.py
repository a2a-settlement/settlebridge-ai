from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.notification import NotificationType


class NotificationResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    type: NotificationType
    title: str
    message: str
    reference_id: uuid.UUID | None = None
    read: bool
    created_at: datetime
    read_at: datetime | None = None

    model_config = {"from_attributes": True}


class NotificationListResponse(BaseModel):
    notifications: list[NotificationResponse]
    total: int
    unread_count: int
