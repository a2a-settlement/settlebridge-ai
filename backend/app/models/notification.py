from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class NotificationType(str, enum.Enum):
    BOUNTY_CLAIMED = "bounty_claimed"
    WORK_SUBMITTED = "work_submitted"
    PAYMENT_RELEASED = "payment_released"
    DISPUTE_FILED = "dispute_filed"
    DISPUTE_RESOLVED = "dispute_resolved"
    BOUNTY_EXPIRED = "bounty_expired"
    SUBMISSION_REJECTED = "submission_rejected"
    CLAIM_ABANDONED = "claim_abandoned"
    CONTRACT_CREATED = "contract_created"
    CONTRACT_ACTIVATED = "contract_activated"
    CONTRACT_CANCELLED = "contract_cancelled"
    SNAPSHOT_DUE = "snapshot_due"
    SNAPSHOT_DELIVERED = "snapshot_delivered"
    SNAPSHOT_MISSED = "snapshot_missed"


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    type: Mapped[NotificationType] = mapped_column(Enum(NotificationType), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    reference_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="notifications")  # noqa: F821
