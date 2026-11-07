"""Challenge detail, signed report, and operator create. Mounted at /api/v1."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.challenge import ChallengeTask
from app.models.github import GitHubRepoLink
from app.models.user import User
from app.schemas.challenge import ChallengeTaskCreate
from app.services import challenge_orchestrator, challenge_report

router = APIRouter()


def _error(status_code: int, error: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": error})


def _not_found() -> JSONResponse:
    return _error(404, "not_found")


def _idempotency_key(value: object) -> str | None:
    """Present keys are 1..128 printable ASCII. Missing is rejected by the caller."""
    if (
        not isinstance(value, str)
        or len(value) < 1
        or len(value) > 128
        or any(ord(char) < 32 or ord(char) > 126 for char in value)
    ):
        return None
    return value


@router.get("/challenges/{task_id}")
async def get_challenge(
    task_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    detail = await challenge_report.challenge_detail(db, task_id, user.id)
    if detail is None:
        return _not_found()
    return detail


@router.get("/challenges/{task_id}/report")
async def get_challenge_report(
    task_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    detail = await challenge_report.challenge_detail(db, task_id, user.id)
    if detail is None or not challenge_report.signing_key_configured():
        return _not_found()
    try:
        report = await challenge_report.build_report(db, task_id)
        token = challenge_report.sign_report(report, settings.CHALLENGE_REPORT_SIGNING_KEY)
    except (
        challenge_report.ReportNotBuilt,
        challenge_report.ReportVerificationError,
        ValidationError,
    ):
        return _not_found()
    return Response(content=token, media_type="application/jose")


@router.post("/challenges")
async def create_challenge(
    body: ChallengeTaskCreate,
    user: User = Depends(get_current_user),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    db: AsyncSession = Depends(get_db),
):
    if not settings.CHALLENGE_ENGINE_ENABLED:
        return _error(503, "challenge_engine_disabled")
    if not challenge_report.is_challenge_operator(user.id):
        return _error(403, "forbidden")
    key = _idempotency_key(idempotency_key)
    if key is None:
        return _error(400, "invalid_idempotency_key")
    existing = await _task_by_idempotency_key(db, key)
    if existing is not None:
        installation_id = int(existing.installation_id)
    else:
        installation_id = await _installation_id(db, body.repo_full_name)
        if installation_id is None:
            return _not_found()
    task = await challenge_orchestrator.create_task(
        db,
        owner_user_id=user.id,
        installation_id=installation_id,
        body=body,
        idempotency_key=key,
    )
    await db.commit()
    detail = await challenge_report.challenge_detail(db, task.id, user.id)
    if detail is None:
        return _not_found()
    status_code = 200 if existing is not None else 201
    return JSONResponse(status_code=status_code, content=detail.model_dump(mode="json"))


async def _task_by_idempotency_key(db: AsyncSession, key: str) -> ChallengeTask | None:
    stmt = select(ChallengeTask).where(ChallengeTask.idempotency_key == key)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _installation_id(db: AsyncSession, repo_full_name: str) -> int | None:
    stmt = (
        select(GitHubRepoLink.installation_id)
        .where(func.lower(GitHubRepoLink.repo_full_name) == repo_full_name.casefold())
        .limit(1)
    )
    found = (await db.execute(stmt)).scalar_one_or_none()
    if found is None:
        return None
    return int(found)
