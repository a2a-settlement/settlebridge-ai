"""Execution spend: account budgets, reservations ahead of work, and per-execution usage.

Request idempotency lives on ``spend_reservations.idempotency_key``: a retried request
returns the same reservation. Every real execution writes its own ``execution_usage``
row, keyed by ``execution_id``. Platform retries are recorded with cause
``platform_retry`` and ``charged = false``.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ReservationStatus(str, enum.Enum):
    RESERVED = "reserved"
    RECONCILED = "reconciled"
    RELEASED = "released"
    EXPIRED = "expired"


RESERVATION_TRANSITIONS: dict[str, frozenset[str]] = {
    "reserved": frozenset({"reconciled", "released", "expired"}),
    "reconciled": frozenset(),
    "released": frozenset(),
    "expired": frozenset(),
}


class UsageCause(str, enum.Enum):
    INITIAL = "initial"
    RERUN = "rerun"
    CONTROL = "control"
    REDERIVATION = "rederivation"
    DEPENDENCY_ACQUISITION = "dependency_acquisition"
    PLATFORM_RETRY = "platform_retry"


class AccountBudget(Base):
    __tablename__ = "account_budgets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), unique=True, nullable=False
    )
    period_cap_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    per_job_cap_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_concurrent_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Maintained by services/spend.py under a row lock; never written elsewhere.
    reserved_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    spent_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SpendReservation(Base):
    __tablename__ = "spend_reservations"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_spend_reservations_idempotency_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    budget_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account_budgets.id"), nullable=False, index=True
    )
    # What the reservation funds, e.g. "challenge_attempt:<uuid>".
    subject: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    reserved_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    used_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ReservationStatus.RESERVED.value, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ExecutionUsage(Base):
    __tablename__ = "execution_usage"
    __table_args__ = (UniqueConstraint("execution_id", name="uq_execution_usage_execution_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reservation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("spend_reservations.id"), nullable=False, index=True
    )
    # Unique per real sandbox execution; the runner result echoes it.
    execution_id: Mapped[str] = mapped_column(String(128), nullable=False)
    cause: Mapped[str] = mapped_column(String(32), nullable=False)
    charged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    cpu_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wall_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cost_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
