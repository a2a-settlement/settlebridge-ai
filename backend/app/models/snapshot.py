from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SnapshotStatus(str, enum.Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    APPROVED = "approved"
    REJECTED = "rejected"
    MISSED = "missed"
    DISPUTED = "disputed"


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("service_contracts.id"), nullable=False
    )
    cycle_number: Mapped[int] = mapped_column(Integer, nullable=False)
    escrow_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    deliverable: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    provenance: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[SnapshotStatus] = mapped_column(
        Enum(SnapshotStatus), nullable=False, default=SnapshotStatus.PENDING
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    contract: Mapped["ServiceContract"] = relationship(back_populates="snapshots")  # noqa: F821
