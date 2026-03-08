from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bounty import Bounty, BountyStatus, ProvenanceTier
from app.models.claim import Claim, ClaimStatus
from app.models.submission import Submission, SubmissionStatus


def validate_provenance(provenance: dict | None, required_tier: ProvenanceTier) -> list[str]:
    """Validate provenance against the required tier. Returns a list of errors."""
    errors: list[str] = []
    if required_tier == ProvenanceTier.TIER1_SELF_DECLARED:
        return errors

    if not provenance:
        errors.append(f"Provenance required for {required_tier.value}")
        return errors

    if required_tier in (ProvenanceTier.TIER2_SIGNED, ProvenanceTier.TIER3_VERIFIABLE):
        if not provenance.get("source_refs"):
            errors.append("source_refs required for tier 2+")
        if not provenance.get("content_hash"):
            errors.append("content_hash required for tier 2+")

    if required_tier == ProvenanceTier.TIER3_VERIFIABLE:
        if not provenance.get("timestamps"):
            errors.append("timestamps required for tier 3")

    return errors


async def create_submission(
    db: AsyncSession,
    *,
    claim_id: uuid.UUID,
    bounty_id: uuid.UUID,
    agent_user_id: uuid.UUID,
    deliverable: dict,
    provenance: dict | None = None,
) -> Submission:
    sub = Submission(
        claim_id=claim_id,
        bounty_id=bounty_id,
        agent_user_id=agent_user_id,
        deliverable=deliverable,
        provenance=provenance,
    )
    db.add(sub)

    claim = (await db.execute(select(Claim).where(Claim.id == claim_id))).scalar_one()
    claim.status = ClaimStatus.SUBMITTED
    claim.submitted_at = datetime.now(timezone.utc)

    bounty = (await db.execute(select(Bounty).where(Bounty.id == bounty_id))).scalar_one()
    bounty.status = BountyStatus.IN_REVIEW

    await db.flush()
    return sub


async def get_submission(db: AsyncSession, submission_id: uuid.UUID) -> Submission | None:
    return (
        await db.execute(select(Submission).where(Submission.id == submission_id))
    ).scalar_one_or_none()


async def approve_submission(db: AsyncSession, submission: Submission, notes: str | None = None):
    submission.status = SubmissionStatus.APPROVED
    submission.reviewer_notes = notes
    submission.reviewed_at = datetime.now(timezone.utc)

    claim = (await db.execute(select(Claim).where(Claim.id == submission.claim_id))).scalar_one()
    claim.status = ClaimStatus.ACCEPTED
    claim.resolved_at = datetime.now(timezone.utc)

    bounty = (
        await db.execute(select(Bounty).where(Bounty.id == submission.bounty_id))
    ).scalar_one()
    bounty.status = BountyStatus.COMPLETED
    bounty.completed_at = datetime.now(timezone.utc)

    await db.flush()


async def reject_submission(db: AsyncSession, submission: Submission, notes: str | None = None):
    submission.status = SubmissionStatus.REJECTED
    submission.reviewer_notes = notes
    submission.reviewed_at = datetime.now(timezone.utc)

    claim = (await db.execute(select(Claim).where(Claim.id == submission.claim_id))).scalar_one()
    claim.status = ClaimStatus.REJECTED
    claim.resolved_at = datetime.now(timezone.utc)

    bounty = (
        await db.execute(select(Bounty).where(Bounty.id == submission.bounty_id))
    ).scalar_one()
    bounty.status = BountyStatus.OPEN

    await db.flush()


async def dispute_submission(db: AsyncSession, submission: Submission):
    submission.status = SubmissionStatus.DISPUTED

    bounty = (
        await db.execute(select(Bounty).where(Bounty.id == submission.bounty_id))
    ).scalar_one()
    bounty.status = BountyStatus.DISPUTED

    await db.flush()
