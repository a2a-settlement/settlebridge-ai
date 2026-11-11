"""Verifier-bypass bounty reports. Mounted at /api/v1.

Platform admins (``PLATFORM_ADMIN_USER_IDS``) triage and decide. The feature
flag ``VERIFIER_BYPASS_BOUNTY_ENABLED`` hides every route when it is off.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.middleware.auth import get_current_user, is_platform_admin
from app.models.user import User
from app.schemas.verifier_bypass import VerifierBypassCreate, VerifierBypassDecision
from app.services import verifier_bypass as bypass_service

router = APIRouter()


def _error(status_code: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": code})


def _not_found() -> JSONResponse:
    return _error(404, "not_found")


def _enabled() -> bool:
    return bool(settings.VERIFIER_BYPASS_BOUNTY_ENABLED)


@router.post("/verifier-bypasses")
async def create_verifier_bypass(
    body: VerifierBypassCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not _enabled():
        return _not_found()
    try:
        row = await bypass_service.submit_bypass(db, user, body)
        await db.commit()
    except bypass_service.BypassError as exc:
        await db.rollback()
        return _error(exc.status_code, exc.code)
    except Exception:
        await db.rollback()
        raise
    return JSONResponse(status_code=201, content=bypass_service.serialize_bypass(row))


@router.get("/verifier-bypasses/{submission_id}")
async def get_verifier_bypass(
    submission_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not _enabled():
        return _not_found()
    try:
        row = await bypass_service.submission_for_viewer(db, submission_id, user)
    except bypass_service.BypassError as exc:
        return _error(exc.status_code, exc.code)
    if row is None:
        return _not_found()
    return bypass_service.serialize_bypass(row)


@router.post("/verifier-bypasses/{submission_id}/decision")
async def decide_verifier_bypass(
    submission_id: uuid.UUID,
    body: VerifierBypassDecision,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not _enabled():
        return _not_found()
    if not is_platform_admin(user):
        return _error(403, "forbidden")
    try:
        row = await bypass_service.apply_decision(db, submission_id, body)
        await db.commit()
    except bypass_service.BypassError as exc:
        await db.rollback()
        return _error(exc.status_code, exc.code)
    except Exception:
        await db.rollback()
        raise
    return bypass_service.serialize_bypass(row)
