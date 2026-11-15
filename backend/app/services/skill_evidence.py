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


def aggregate_evidenced_bot_ids(
    rows: list[tuple[str, list[str] | None, float | None]],
    skills_by_bot: dict[str, list[str]] | None = None,
) -> set[str]:
    """Group settled scores by ``(bot_id, tag)`` and return evidenced ``bot_id`` set.

    When ``skills_by_bot`` has a non-empty skill list for a bot, only tags matching
    one of that bot's declared skills count toward evidence (matching
    :func:`aggregate_skill_evidence`). If ``skills_by_bot`` is omitted or a bot has
    no skills in its directory row (e.g. skills only inside its Agent Card), any
    settled bounty tag is eligible.
    """
    allowed_by_bot: dict[str, set[str]] = {}
    if skills_by_bot:
        for bid, sids in skills_by_bot.items():
            if sids:
                allowed_by_bot[bid] = set(sids)

    buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
    for bot_id, tags, score in rows:
        if not bot_id or score is None or not tags:
            continue
        allowed = allowed_by_bot.get(bot_id)
        for tag in dict.fromkeys(tags):
            if not tag:
                continue
            if allowed is not None and tag not in allowed:
                continue
            buckets[(bot_id, tag)].append(score)

    evidenced_bots: set[str] = set()
    for (bot_id, _tag), scores in buckets.items():
        if bot_id in evidenced_bots:
            continue
        settled_count = len(scores)
        if settled_count < EVIDENCED_MIN_COUNT:
            continue
        avg_score = round(sum(scores) / settled_count, 1)
        if avg_score >= EVIDENCED_MIN_AVG:
            evidenced_bots.add(bot_id)
    return evidenced_bots


async def compute_evidenced_bot_ids(
    db: AsyncSession,
    bot_ids: list[str],
    skills_by_bot: dict[str, list[str]] | None = None,
) -> set[str]:
    """Return ``bot_id`` values with >= 5 settled submissions averaging >= 75.0 on a tag."""
    if not bot_ids:
        return set()

    result = await db.execute(
        select(
            Claim.agent_exchange_bot_id,
            Bounty.tags,
            Submission.score,
            Submission.ai_review,
        )
        .join(Claim, Claim.id == Submission.claim_id)
        .join(Bounty, Bounty.id == Submission.bounty_id)
        .where(
            Claim.agent_exchange_bot_id.in_(bot_ids),
            Submission.status.in_(_SETTLED),
        )
    )
    rows: list[tuple[str, list[str] | None, float | None]] = []
    for bot_id, tags, score, ai_review in result.all():
        rows.append((bot_id, tags, effective_score(score, ai_review)))
    return aggregate_evidenced_bot_ids(rows, skills_by_bot=skills_by_bot)
