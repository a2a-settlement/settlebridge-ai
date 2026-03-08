from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.submission import SubmissionStatus


class DeliverablePayload(BaseModel):
    content: str
    content_type: str = "text/plain"
    attachments: list[dict] | None = None
    metadata: dict | None = None


class ProvenancePayload(BaseModel):
    source_type: str = "generated"
    source_refs: list[str] | None = None
    timestamps: list[dict] | None = None
    content_hash: str | None = None
    attestation_level: str = "self_declared"


class SubmitWorkRequest(BaseModel):
    deliverable: DeliverablePayload
    provenance: ProvenancePayload | None = None


class ReviewRequest(BaseModel):
    notes: str | None = None


class DisputeRequest(BaseModel):
    reason: str


class SubmissionResponse(BaseModel):
    id: uuid.UUID
    claim_id: uuid.UUID
    bounty_id: uuid.UUID
    agent_user_id: uuid.UUID
    deliverable: dict
    provenance: dict | None = None
    status: SubmissionStatus
    reviewer_notes: str | None = None
    submitted_at: datetime
    reviewed_at: datetime | None = None

    model_config = {"from_attributes": True}
