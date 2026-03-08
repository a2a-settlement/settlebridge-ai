from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class UserType(str, enum.Enum):
    REQUESTER = "requester"
    AGENT_OPERATOR = "agent_operator"
    BOTH = "both"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    user_type: Mapped[UserType] = mapped_column(Enum(UserType), nullable=False)
    exchange_bot_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    exchange_api_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    exchange_balance_cached: Mapped[float | None] = mapped_column(Float, nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    bounties: Mapped[list["Bounty"]] = relationship(back_populates="requester")  # noqa: F821
    claims: Mapped[list["Claim"]] = relationship(back_populates="agent_user")  # noqa: F821
    notifications: Mapped[list["Notification"]] = relationship(back_populates="user")  # noqa: F821
