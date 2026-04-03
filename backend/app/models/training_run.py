from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TrainingRunStatus(str, enum.Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class TrainingRun(Base):
    __tablename__ = "training_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bounty_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bounties.id"), nullable=False
    )
    agent_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    status: Mapped[TrainingRunStatus] = mapped_column(
        Enum(TrainingRunStatus, name="trainingrunstatus", create_type=False),
        nullable=False,
        default=TrainingRunStatus.RUNNING,
    )
    max_iterations: Mapped[int] = mapped_column(Integer, nullable=False)
    stake_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    stake_spent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    score_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    task_type: Mapped[str] = mapped_column(String(100), nullable=False)
    bounty_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    iterations_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    public_title: Mapped[str | None] = mapped_column(String(500), nullable=True)

    score_history_rows: Mapped[list["ScoreHistory"]] = relationship(  # noqa: F821
        back_populates="training_run"
    )
    transcript: Mapped["TrainingTranscript | None"] = relationship(  # noqa: F821
        back_populates="training_run", uselist=False
    )


class TrainingTranscript(Base):
    __tablename__ = "training_transcripts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    training_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("training_runs.id"), nullable=False, unique=True
    )
    agent_display_id: Mapped[str] = mapped_column(String(255), nullable=False)
    bounty_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bounties.id"), nullable=False
    )
    total_iterations: Mapped[int] = mapped_column(Integer, nullable=False)
    total_stake_spent: Mapped[int] = mapped_column(Integer, nullable=False)
    final_training_ema: Mapped[float] = mapped_column(Float, nullable=False)
    merkle_root: Mapped[str] = mapped_column(String(255), nullable=False)
    signed_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    training_run: Mapped["TrainingRun"] = relationship(back_populates="transcript")
