from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AssistSessionStatus(str, enum.Enum):
    ACTIVE = "active"
    DRAFT_READY = "draft_ready"
    FINALIZED = "finalized"
    ABANDONED = "abandoned"


class AssistSession(Base):
    __tablename__ = "assist_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    status: Mapped[AssistSessionStatus] = mapped_column(
        Enum(AssistSessionStatus), nullable=False, default=AssistSessionStatus.ACTIVE
    )
    messages: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    bounty_draft: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    settlement_structure: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    finalized_bounty_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bounties.id"), nullable=True
    )

    user: Mapped["User"] = relationship()  # noqa: F821
    finalized_bounty: Mapped["Bounty | None"] = relationship()  # noqa: F821
