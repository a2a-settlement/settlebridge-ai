"""Signed evidence report and read models (owner: P1-5).

The report is a ChallengeReport serialized canonically (sorted keys, no whitespace)
and signed as a compact JWS with ES256 using CHALLENGE_REPORT_SIGNING_KEY. It never
contains tokens, raw logs, or another tenant's data.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.challenge import ChallengeDetailOut, ChallengeReport


async def build_report(db: AsyncSession, task_id: uuid.UUID) -> ChallengeReport:
    raise NotImplementedError


def sign_report(report: ChallengeReport, private_key_pem: str) -> str:
    raise NotImplementedError


def verify_report(jws: str, public_key_pem: str) -> ChallengeReport:
    raise NotImplementedError


async def challenge_detail(
    db: AsyncSession, task_id: uuid.UUID, viewer_user_id: uuid.UUID
) -> ChallengeDetailOut | None:
    """Returns None for a task the viewer may not see (callers answer 404)."""
    raise NotImplementedError
