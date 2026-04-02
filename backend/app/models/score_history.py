from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class BountyMode(str, enum.Enum):
    PRODUCTION = "production"
    TRAINING = "training"


class ScoreHistory(Base):
    __tablename__ = "score_history"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    bounty_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bounties.id"), nullable=False, index=True
    )
    task_type: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    numeric_score: Mapped[float] = mapped_column(Float, nullable=False)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    diagnostics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    mode: Mapped[BountyMode] = mapped_column(
        Enum(BountyMode), nullable=False, default=BountyMode.PRODUCTION, index=True
    )
    training_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("training_runs.id"), nullable=True, index=True
    )
    provenance_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    bounty: Mapped["Bounty"] = relationship()  # noqa: F821
    training_run: Mapped["TrainingRun | None"] = relationship(back_populates="score_history_rows")  # noqa: F821
