from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class BountyStatus(str, enum.Enum):
    DRAFT = "draft"
    OPEN = "open"
    CLAIMED = "claimed"
    SUBMITTED = "submitted"
    IN_REVIEW = "in_review"
    COMPLETED = "completed"
    DISPUTED = "disputed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class Difficulty(str, enum.Enum):
    TRIVIAL = "trivial"
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    EXPERT = "expert"


class ProvenanceTier(str, enum.Enum):
    TIER1_SELF_DECLARED = "tier1_self_declared"
    TIER2_SIGNED = "tier2_signed"
    TIER3_VERIFIABLE = "tier3_verifiable"


class Bounty(Base):
    __tablename__ = "bounties"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    requester_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id"), nullable=True
    )
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    acceptance_criteria: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reward_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    escrow_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[BountyStatus] = mapped_column(
        Enum(BountyStatus), nullable=False, default=BountyStatus.DRAFT
    )
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_claims: Mapped[int] = mapped_column(Integer, default=1)
    min_reputation: Mapped[float | None] = mapped_column(Float, nullable=True)
    difficulty: Mapped[Difficulty] = mapped_column(
        Enum(Difficulty), nullable=False, default=Difficulty.MEDIUM
    )
    auto_approve: Mapped[bool] = mapped_column(default=False)
    provenance_tier: Mapped[ProvenanceTier] = mapped_column(
        Enum(ProvenanceTier), nullable=False, default=ProvenanceTier.TIER1_SELF_DECLARED
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    funded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    settlement_structure: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    requester: Mapped["User"] = relationship(back_populates="bounties")  # noqa: F821
    category: Mapped["Category | None"] = relationship(lazy="joined")  # noqa: F821
    claims: Mapped[list["Claim"]] = relationship(back_populates="bounty")  # noqa: F821
    submissions: Mapped[list["Submission"]] = relationship(back_populates="bounty")  # noqa: F821
