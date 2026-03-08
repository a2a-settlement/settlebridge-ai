from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.bounty import ProvenanceTier


class ContractStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class ServiceContract(Base):
    __tablename__ = "service_contracts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    requester_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    agent_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    agent_exchange_bot_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id"), nullable=True
    )
    acceptance_criteria: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    provenance_tier: Mapped[ProvenanceTier] = mapped_column(
        Enum(ProvenanceTier), nullable=False, default=ProvenanceTier.TIER1_SELF_DECLARED
    )
    reward_per_snapshot: Mapped[int] = mapped_column(Integer, nullable=False)
    schedule: Mapped[str] = mapped_column(String(100), nullable=False)
    schedule_description: Mapped[str] = mapped_column(String(255), nullable=False)
    max_snapshots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    grace_period_hours: Mapped[int] = mapped_column(Integer, default=24)
    auto_approve: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[ContractStatus] = mapped_column(
        Enum(ContractStatus), nullable=False, default=ContractStatus.DRAFT
    )
    group_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    requester: Mapped["User"] = relationship(foreign_keys=[requester_id])  # noqa: F821
    agent_user: Mapped["User"] = relationship(foreign_keys=[agent_user_id])  # noqa: F821
    category: Mapped["Category | None"] = relationship()  # noqa: F821
    snapshots: Mapped[list["Snapshot"]] = relationship(back_populates="contract")  # noqa: F821
