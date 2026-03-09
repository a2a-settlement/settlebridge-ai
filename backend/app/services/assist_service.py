"""Orchestrates Bounty Assist sessions: creation, messaging, finalization."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.assist_session import AssistSession, AssistSessionStatus
from app.models.bounty import Bounty, BountyStatus, Difficulty, ProvenanceTier
from app.models.category import Category
from app.services import conversation_engine, settlement_builder


async def create_session(db: AsyncSession, user_id: uuid.UUID) -> AssistSession:
    session = AssistSession(
        user_id=user_id,
        status=AssistSessionStatus.ACTIVE,
        messages=[],
        turn_count=0,
    )
    db.add(session)
    await db.flush()
    return session


async def get_session(db: AsyncSession, session_id: uuid.UUID) -> AssistSession | None:
    return (
        await db.execute(
            select(AssistSession).where(AssistSession.id == session_id)
        )
    ).scalar_one_or_none()


async def list_sessions(
    db: AsyncSession, user_id: uuid.UUID
) -> list[AssistSession]:
    q = (
        select(AssistSession)
        .where(AssistSession.user_id == user_id)
        .order_by(AssistSession.updated_at.desc())
        .limit(20)
    )
    return list((await db.execute(q)).scalars().all())


async def stream_and_persist(
    db: AsyncSession,
    session: AssistSession,
    user_message: str,
) -> AsyncGenerator[str, None]:
    """Stream Claude's response while accumulating the full text.

    After the stream completes, persist both the user message and
    the full assistant response to the session, and update the draft.
    """
    now = datetime.now(timezone.utc).isoformat()
    user_msg = {"role": "user", "content": user_message, "timestamp": now}

    history = list(session.messages)

    accumulated: list[str] = []

    async for chunk in conversation_engine.stream_response(history, user_message):
        accumulated.append(chunk)
        yield chunk

    full_text = "".join(accumulated)
    parsed = conversation_engine.parse_response(full_text)

    assistant_msg = {
        "role": "assistant",
        "content": full_text,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    updated_messages = list(session.messages) + [user_msg, assistant_msg]
    session.messages = updated_messages
    session.turn_count = session.turn_count + 1

    if parsed.bounty_draft:
        session.bounty_draft = parsed.bounty_draft
        ss_raw = parsed.bounty_draft.get("settlement_structure")
        if ss_raw:
            ss = settlement_builder.from_draft_dict(ss_raw)
            if ss:
                session.settlement_structure = ss.model_dump()

        if _draft_is_complete(parsed.bounty_draft) and session.turn_count >= 2:
            session.status = AssistSessionStatus.DRAFT_READY

    if session.turn_count >= settings.ASSIST_MAX_TURNS:
        session.status = AssistSessionStatus.DRAFT_READY

    await db.flush()


def _draft_is_complete(draft: dict) -> bool:
    """Check whether the essential fields of a bounty draft have been filled."""
    required = ["title", "description", "reward_suggestion"]
    return all(draft.get(k) for k in required)


async def send_message_no_stream(
    db: AsyncSession,
    session: AssistSession,
    user_message: str,
) -> conversation_engine.EngineResponse:
    """Non-streaming variant for simpler callers."""
    now = datetime.now(timezone.utc).isoformat()
    user_msg = {"role": "user", "content": user_message, "timestamp": now}

    history = list(session.messages)
    response = await conversation_engine.get_response(history, user_message)

    assistant_msg = {
        "role": "assistant",
        "content": response.raw_text,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    updated_messages = list(session.messages) + [user_msg, assistant_msg]
    session.messages = updated_messages
    session.turn_count = session.turn_count + 1

    if response.bounty_draft:
        session.bounty_draft = response.bounty_draft
        ss_raw = response.bounty_draft.get("settlement_structure")
        if ss_raw:
            ss = settlement_builder.from_draft_dict(ss_raw)
            if ss:
                session.settlement_structure = ss.model_dump()

        if _draft_is_complete(response.bounty_draft) and session.turn_count >= 2:
            session.status = AssistSessionStatus.DRAFT_READY

    if session.turn_count >= settings.ASSIST_MAX_TURNS:
        session.status = AssistSessionStatus.DRAFT_READY

    await db.flush()
    return response


async def _resolve_category_id(db: AsyncSession, slug: str | None) -> uuid.UUID | None:
    if not slug:
        return None
    result = await db.execute(select(Category).where(Category.slug == slug))
    cat = result.scalar_one_or_none()
    return cat.id if cat else None


def _map_difficulty(val: str | None) -> Difficulty:
    if not val:
        return Difficulty.MEDIUM
    try:
        return Difficulty(val.lower())
    except ValueError:
        return Difficulty.MEDIUM


def _map_provenance_tier(val: str | None) -> ProvenanceTier:
    if not val:
        return ProvenanceTier.TIER1_SELF_DECLARED
    try:
        return ProvenanceTier(val.lower())
    except ValueError:
        return ProvenanceTier.TIER1_SELF_DECLARED


async def finalize_session(
    db: AsyncSession,
    session: AssistSession,
    overrides: dict | None = None,
) -> Bounty:
    """Convert the session's bounty draft into a real Bounty record."""
    if session.status == AssistSessionStatus.FINALIZED:
        raise ValueError("Session already finalized")

    draft = session.bounty_draft or {}
    ov = overrides or {}

    title = ov.get("title") or draft.get("title") or "Untitled Bounty"
    description = ov.get("description") or draft.get("description") or ""
    category_id = ov.get("category_id") or await _resolve_category_id(
        db, draft.get("category_slug")
    )
    tags = ov.get("tags") or draft.get("tags")
    reward_amount = ov.get("reward_amount") or draft.get("reward_suggestion") or 100
    difficulty = _map_difficulty(ov.get("difficulty") or draft.get("difficulty"))
    provenance_tier = _map_provenance_tier(
        ov.get("provenance_tier") or draft.get("provenance_tier")
    )

    ac_raw = draft.get("acceptance_criteria")
    acceptance_criteria = ac_raw if isinstance(ac_raw, dict) else None

    ss = session.settlement_structure
    if not ss and draft.get("settlement_structure"):
        parsed = settlement_builder.from_draft_dict(draft["settlement_structure"])
        ss = parsed.model_dump() if parsed else None

    deadline = None
    if ov.get("deadline"):
        deadline = ov["deadline"]

    bounty = Bounty(
        requester_id=session.user_id,
        title=title,
        description=description,
        category_id=category_id,
        tags=tags,
        acceptance_criteria=acceptance_criteria,
        reward_amount=reward_amount,
        difficulty=difficulty,
        provenance_tier=provenance_tier,
        settlement_structure=ss,
        deadline=deadline,
        status=BountyStatus.DRAFT,
    )
    db.add(bounty)
    await db.flush()

    session.status = AssistSessionStatus.FINALIZED
    session.finalized_bounty_id = bounty.id
    await db.flush()

    return bounty


async def abandon_session(db: AsyncSession, session: AssistSession) -> None:
    session.status = AssistSessionStatus.ABANDONED
    await db.flush()
