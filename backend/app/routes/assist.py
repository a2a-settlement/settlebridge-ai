from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.assist_session import AssistSessionStatus
from app.models.user import User
from app.schemas.assist import (
    AssistSessionListResponse,
    AssistSessionResponse,
    FinalizeSessionRequest,
    SendMessageRequest,
    StartSessionRequest,
)
from app.schemas.bounty import BountyResponse
from app.services import assist_service

router = APIRouter()


def _validate_ownership(session, user: User):
    if session.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your session")


async def _sse_generator(db: AsyncSession, session, user_message: str):
    """Yield SSE-formatted events from the streaming Claude response.

    Events:
      - event: token     data: <text chunk>
      - event: draft     data: <bounty_draft json>
      - event: status    data: <session status>
      - event: done      data: {}
    """
    accumulated: list[str] = []
    try:
        async for chunk in assist_service.stream_and_persist(db, session, user_message):
            accumulated.append(chunk)
            yield f"event: token\ndata: {json.dumps(chunk)}\n\n"

        await db.commit()
        await db.refresh(session)

        if session.bounty_draft:
            yield f"event: draft\ndata: {json.dumps(session.bounty_draft)}\n\n"

        yield f"event: status\ndata: {json.dumps(session.status.value)}\n\n"
        yield "event: done\ndata: {}\n\n"
    except Exception as exc:
        await db.rollback()
        yield f"event: error\ndata: {json.dumps(str(exc))}\n\n"


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def start_session(
    body: StartSessionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await assist_service.create_session(db, user.id)
    await db.commit()
    await db.refresh(session)

    return StreamingResponse(
        _sse_generator(db, session, body.initial_message),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Session-Id": str(session.id),
        },
    )


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: uuid.UUID,
    body: SendMessageRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await assist_service.get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    _validate_ownership(session, user)

    if session.status in (AssistSessionStatus.FINALIZED, AssistSessionStatus.ABANDONED):
        raise HTTPException(status_code=400, detail="Session is no longer active")

    return StreamingResponse(
        _sse_generator(db, session, body.content),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.get("/sessions/{session_id}", response_model=AssistSessionResponse)
async def get_session(
    session_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await assist_service.get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    _validate_ownership(session, user)
    return AssistSessionResponse.model_validate(session)


@router.get("/sessions", response_model=AssistSessionListResponse)
async def list_sessions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sessions = await assist_service.list_sessions(db, user.id)
    return AssistSessionListResponse(
        sessions=[AssistSessionResponse.model_validate(s) for s in sessions],
        total=len(sessions),
    )


@router.post("/sessions/{session_id}/finalize", response_model=BountyResponse)
async def finalize_session(
    session_id: uuid.UUID,
    body: FinalizeSessionRequest | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await assist_service.get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    _validate_ownership(session, user)

    if session.status == AssistSessionStatus.FINALIZED:
        raise HTTPException(status_code=400, detail="Session already finalized")
    if session.status == AssistSessionStatus.ABANDONED:
        raise HTTPException(status_code=400, detail="Session was abandoned")

    overrides = body.model_dump(exclude_none=True) if body else None

    try:
        bounty = await assist_service.finalize_session(db, session, overrides)
        await db.commit()
        await db.refresh(bounty)
        return BountyResponse.model_validate(bounty)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def abandon_session(
    session_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await assist_service.get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    _validate_ownership(session, user)

    await assist_service.abandon_session(db, session)
    await db.commit()
