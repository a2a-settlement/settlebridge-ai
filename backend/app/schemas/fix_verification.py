"""Request and response bodies for the public fix-verification API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AdvisoryInput(BaseModel):
    """Advisory fields the verifier needs. Unknown keys are kept and forwarded."""

    model_config = ConfigDict(extra="allow")

    vuln_id: str | None = None
    aliases: list[str] = Field(default_factory=list)
    summary: str = ""
    details: str = ""
    package: Any = None
    ecosystem: Any = None


class FixVerificationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_full_name: str = Field(min_length=1, max_length=255)
    pr: int = Field(ge=1, le=1_000_000_000)
    base_sha: str = Field(pattern=r"^[0-9a-fA-F]{40}$")
    fix_sha: str = Field(pattern=r"^[0-9a-fA-F]{40}$")
    advisory: AdvisoryInput = Field(default_factory=AdvisoryInput)
    alert_ref: str | None = Field(default=None, max_length=255)
    solver_kind: str = "unknown"
    solver_principal_id: str | None = Field(default=None, max_length=255)

    @field_validator("solver_kind", mode="before")
    @classmethod
    def _solver_kind(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        kind = value.strip().casefold().replace("-", "_")
        if kind in {"house", "third_party", "unknown"}:
            return kind
        return value

    @field_validator("solver_kind")
    @classmethod
    def _known_solver_kind(cls, value: str) -> str:
        if value not in {"house", "third_party", "unknown"}:
            raise ValueError("solver_kind must be house, third_party, or unknown")
        return value


class FixVerificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repo_full_name: str
    installation_id: int | None = None
    vuln_id: str
    alert_ref: str | None = None
    pr_number: int | None = None
    base_sha: str
    fix_sha: str
    state: str
    verdict: str | None = None
    inconclusive_reason: str | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    cost_cents: int
    receipt_id: str | None = None
    embargo_state: str
    solver_kind: str
    created_at: datetime | None = None
    completed_at: datetime | None = None


class ReceiptResponse(BaseModel):
    receipt_id: str
