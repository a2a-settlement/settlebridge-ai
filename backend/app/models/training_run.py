from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TrainingRunStatus(str, enum.Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    EXHAUSTED = "exhausted"


class TrainingRun(Base):
    __tablename__ = "training_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    target_bounty_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bounties.id"), nullable=False
    )
    task_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    max_iterations: Mapped[int] = mapped_column(Integer, nullable=False)
    stake_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    stake_spent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    score_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[TrainingRunStatus] = mapped_column(
        Enum(TrainingRunStatus), nullable=False, default=TrainingRunStatus.RUNNING
    )
    bounty_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    target_bounty: Mapped["Bounty"] = relationship()  # noqa: F821
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
    agent_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    bounty_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bounties.id"), nullable=False
    )
    total_iterations: Mapped[int] = mapped_column(Integer, nullable=False)
    total_stake_spent: Mapped[int] = mapped_column(Integer, nullable=False)
    final_production_ema: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_training_ema: Mapped[float | None] = mapped_column(Float, nullable=True)
    merkle_root: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signed_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    training_run: Mapped["TrainingRun"] = relationship(back_populates="transcript")
