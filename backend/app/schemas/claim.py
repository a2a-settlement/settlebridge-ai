from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.claim import ClaimStatus


class ClaimResponse(BaseModel):
    id: uuid.UUID
    bounty_id: uuid.UUID
    agent_user_id: uuid.UUID
    agent_exchange_bot_id: str
    status: ClaimStatus
    claimed_at: datetime
    submitted_at: datetime | None = None
    resolved_at: datetime | None = None
    abandon_reason: str | None = None

    model_config = {"from_attributes": True}


class AbandonRequest(BaseModel):
    reason: str | None = None
