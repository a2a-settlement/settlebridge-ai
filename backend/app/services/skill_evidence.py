"""Skill track-record evidence from settled bounty history.

Evidence uses exact match between Agent Card skill.id (or account skills[])
and bounty.tags. Display-only badges — never gates escrow.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bounty import Bounty
from app.models.claim import Claim
from app.models.submission import Submission, SubmissionStatus

EVIDENCED_MIN_COUNT = 5
EVIDENCED_MIN_AVG = 75.0

_SETTLED = (
    SubmissionStatus.APPROVED,
    SubmissionStatus.PARTIALLY_APPROVED,
)


def effective_score(score: int | None, ai_review: dict | None) -> float | None:
    """COALESCE(submission.score, ai_review.score)."""
    if score is not None:
        return float(score)
    if isinstance(ai_review, dict):
        raw = ai_review.get("score")
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None
    return None


def extract_skill_ids(account: dict[str, Any]) -> list[str]:
    """Prefer Agent Card skill.id values; fall back to account skills[]."""
    card = account.get("agent_card")
    ids: list[str] = []
    if isinstance(card, dict):
        skills = card.get("skills") or []
        for skill in skills:
            if isinstance(skill, dict) and skill.get("id"):
                ids.append(str(skill["id"]))
            elif isinstance(skill, str) and skill.strip():
                ids.append(skill.strip())
    if not ids:
        for s in account.get("skills") or []:
            if isinstance(s, str) and s.strip():
                ids.append(s.strip())
    # Preserve order, drop dupes
    seen: set[str] = set()
    out: list[str] = []
    for sid in ids:
        if sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


def aggregate_skill_evidence(
    skill_ids: list[str],
    rows: list[tuple[list[str] | None, float | None]],
) -> list[dict[str, Any]]:
    """Group settled scores by skill.id ∩ bounty.tags (exact match)."""
    skill_set = set(skill_ids)
    buckets: dict[str, list[float]] = defaultdict(list)
    for tags, score in rows:
        if score is None or not tags:
            continue
        for tag in tags:
            if tag in skill_set:
                buckets[tag].append(score)

    evidence: list[dict[str, Any]] = []
    for skill_id in skill_ids:
        scores = buckets.get(skill_id, [])
        settled_count = len(scores)
        avg_score = round(sum(scores) / settled_count, 1) if settled_count else None
        evidenced = (
            settled_count >= EVIDENCED_MIN_COUNT
            and avg_score is not None
            and avg_score >= EVIDENCED_MIN_AVG
        )
        evidence.append(
            {
                "skill_id": skill_id,
                "settled_count": settled_count,
                "avg_score": avg_score,
                "evidenced": evidenced,
            }
        )
    return evidence


async def compute_skill_evidence(
    db: AsyncSession,
    bot_id: str,
    skill_ids: list[str],
) -> list[dict[str, Any]]:
    if not skill_ids:
        return []

    result = await db.execute(
        select(Bounty.tags, Submission.score, Submission.ai_review)
        .join(Claim, Claim.id == Submission.claim_id)
        .join(Bounty, Bounty.id == Submission.bounty_id)
        .where(
            Claim.agent_exchange_bot_id == bot_id,
            Submission.status.in_(_SETTLED),
        )
    )
    rows: list[tuple[list[str] | None, float | None]] = []
    for tags, score, ai_review in result.all():
        rows.append((tags, effective_score(score, ai_review)))
    return aggregate_skill_evidence(skill_ids, rows)
