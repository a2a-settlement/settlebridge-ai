from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SubmissionStatus(str, enum.Enum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    DISPUTED = "disputed"


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id"), nullable=False
    )
    bounty_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bounties.id"), nullable=False
    )
    agent_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    deliverable: Mapped[dict] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[SubmissionStatus] = mapped_column(
        Enum(SubmissionStatus), nullable=False, default=SubmissionStatus.PENDING_REVIEW
    )
    reviewer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    claim: Mapped["Claim"] = relationship(back_populates="submissions")  # noqa: F821
    bounty: Mapped["Bounty"] = relationship(back_populates="submissions")  # noqa: F821
    agent_user: Mapped["User"] = relationship()  # noqa: F821
