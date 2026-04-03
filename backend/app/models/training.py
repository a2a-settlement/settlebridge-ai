from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TrainingRunStatus(str, enum.Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ScoreMode(str, enum.Enum):
    TRAINING = "training"
    PRODUCTION = "production"


class TrainingRun(Base):
    __tablename__ = "training_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bounty_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bounties.id"), nullable=False
    )
    agent_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    status: Mapped[TrainingRunStatus] = mapped_column(
        Enum(TrainingRunStatus), nullable=False, default=TrainingRunStatus.RUNNING
    )
    max_iterations: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    stake_budget: Mapped[int] = mapped_column(Integer, nullable=False, default=1000)
    stake_spent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    score_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.85)
    task_type: Mapped[str] = mapped_column(String(100), nullable=False)
    bounty_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    iterations_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    score_history: Mapped[list["ScoreHistory"]] = relationship(back_populates="training_run")
    transcript: Mapped["TrainingTranscript | None"] = relationship(
        back_populates="training_run", uselist=False
    )


class ScoreHistory(Base):
    __tablename__ = "score_history"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    bounty_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bounties.id"), nullable=False
    )
    training_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("training_runs.id"), nullable=True
    )
    task_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    numeric_score: Mapped[float] = mapped_column(Float, nullable=False)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    diagnostics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    mode: Mapped[ScoreMode] = mapped_column(
        Enum(ScoreMode), nullable=False, default=ScoreMode.TRAINING
    )
    provenance_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    training_run: Mapped["TrainingRun | None"] = relationship(back_populates="score_history")


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
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    training_run: Mapped["TrainingRun"] = relationship(back_populates="transcript")
