from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ClaimStatus(str, enum.Enum):
    ACTIVE = "active"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ABANDONED = "abandoned"
    EXPIRED = "expired"


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bounty_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bounties.id"), nullable=False
    )
    agent_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    agent_exchange_bot_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[ClaimStatus] = mapped_column(
        Enum(ClaimStatus), nullable=False, default=ClaimStatus.ACTIVE
    )
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    abandon_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    training_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("training_runs.id"), nullable=True
    )

    bounty: Mapped["Bounty"] = relationship(back_populates="claims")  # noqa: F821
    agent_user: Mapped["User"] = relationship(back_populates="claims")  # noqa: F821
    submissions: Mapped[list["Submission"]] = relationship(back_populates="claim")  # noqa: F821
