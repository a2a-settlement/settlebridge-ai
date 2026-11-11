"""Request and response bodies for verifier-bypass bounty reports."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.challenge import FailureClassLiteral

SUMMARY_MAX_LENGTH = 4000
TEST_SOURCE_MAX_LENGTH = 65_536
NOTES_MAX_LENGTH = 4000
DECISION_NOTES_MAX_LENGTH = 4000

FAILURE_CLASSES = frozenset(get_args(FailureClassLiteral))
# ``triaged`` is the operator hold before a terminal decision. Accepted and
# rejected are the decisions that set ``decided_at``.
DecisionLiteral = Literal["triaged", "accepted", "rejected"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReproducerInput(_Strict):
    test_source: str = Field(min_length=1, max_length=TEST_SOURCE_MAX_LENGTH)
    failure_class: FailureClassLiteral
    notes: str = Field(default="", max_length=NOTES_MAX_LENGTH)


class VerifierBypassCreate(_Strict):
    verification_id: uuid.UUID
    summary: str = Field(min_length=1, max_length=SUMMARY_MAX_LENGTH)
    reproducer: ReproducerInput


class VerifierBypassDecision(_Strict):
    decision: DecisionLiteral
    reward_cents: int | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=DECISION_NOTES_MAX_LENGTH)


class ReproducerView(BaseModel):
    test_source: str
    failure_class: str
    notes: str = ""


class VerifierBypassView(BaseModel):
    id: uuid.UUID
    verification_id: uuid.UUID
    submitter_user_id: uuid.UUID
    state: str
    summary: str
    reproducer: ReproducerView
    reward_cents: int | None = None
    decision_notes: str | None = None
    created_at: datetime
    decided_at: datetime | None = None
