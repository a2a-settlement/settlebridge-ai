"""Public fix-verification API.

The router is mounted at ``/api/v1/fix-verifications``. Include it from
``app.main`` (this module does not edit ``main``) with::

    from app.routes import fix_verifications
    app.include_router(fix_verifications.router)

Every route is 404 while ``FIX_VERIFIER_PUBLIC_API`` is false, including for
anonymous callers. Creating and replaying also require ``FIX_VERIFIER_ENABLED``.

GET is limited to the GitHub installation owner, the repo-link funder, and
platform operators (``PLATFORM_ADMIN_USER_IDS``). An ``incomplete_fix_confirmed``
result with ``embargo_state`` ``private`` keeps its ``result`` body for the
installation owner and operators; a funder receives the verdict only. Replay
is the installation owner or an operator.

``request_verification_receipt`` is the mediator hook used by the receipt
route. It does not fetch a receipt.
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.fix_verifier import FixVerification
from app.models.user import User
from app.schemas.fix_verification import FixVerificationCreate, FixVerificationResponse
from app.services.fix_verifier import (
    FeatureDisabled,
    InvalidVerificationRequest,
    NotOptedIn,
    access_context,
    replay_verification,
    request_verification,
    request_verification_receipt,
    viewer_can_read,
    viewer_can_replay,
    viewer_can_see_details,
)
from app.services.verifier_quota import QuotaExceeded

router = APIRouter(prefix="/api/v1/fix-verifications", tags=["fix-verifications"])


async def _require_public_api() -> None:
    if not settings.FIX_VERIFIER_PUBLIC_API:
        raise HTTPException(status_code=404, detail="not_found")


async def _authorized_user(
    _public: None = Depends(_require_public_api),
    user: User = Depends(get_current_user),
) -> User:
    return user


def _dump(row: FixVerification) -> dict:
    body = FixVerificationResponse.model_validate(row).model_dump(mode="json")
    if not isinstance(body.get("result"), dict):
        body["result"] = {}
    return body


def _redact_details(body: dict) -> dict:
    verdict = body.get("verdict")
    redacted = dict(body)
    redacted["result"] = {"verdict": verdict} if isinstance(verdict, str) else {}
    return redacted


def _visible_body(
    row: FixVerification, user_id, link, installation, *, direct: bool
) -> dict | None:
    if not direct and not viewer_can_read(user_id, row, link, installation):
        return None
    body = _dump(row)
    if not viewer_can_see_details(user_id, row, installation):
        return _redact_details(body)
    return body


async def _load(db: AsyncSession, verification_id: uuid.UUID) -> FixVerification | None:
    stmt = select(FixVerification).where(FixVerification.id == verification_id)
    return (await db.execute(stmt)).scalar_one_or_none()


@router.post("")
async def create_fix_verification(
    request: Request,
    user: User = Depends(_authorized_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        payload = FixVerificationCreate.model_validate(await request.json())
    except (json.JSONDecodeError, ValidationError, UnicodeError):
        raise HTTPException(status_code=422, detail="invalid_request") from None
    try:
        row = await request_verification(
            db,
            repo_full_name=payload.repo_full_name,
            pr=payload.pr,
            base_sha=payload.base_sha,
            fix_sha=payload.fix_sha,
            advisory=payload.advisory.model_dump(),
            alert_ref=payload.alert_ref,
            solver_kind=payload.solver_kind,
            solver_principal_id=payload.solver_principal_id,
            user=user,
        )
    except FeatureDisabled:
        raise HTTPException(status_code=404, detail="not_found") from None
    except NotOptedIn:
        raise HTTPException(status_code=403, detail="not_opted_in") from None
    except InvalidVerificationRequest as exc:
        raise HTTPException(status_code=422, detail=exc.code) from None
    except QuotaExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail="quota_exceeded",
            headers={"Retry-After": str(exc.retry_after)},
        ) from None
    link, installation = await access_context(db, row)
    await db.commit()
    body = _visible_body(row, user.id, link, installation, direct=True)
    return JSONResponse(status_code=201, content=body)


@router.get("/{verification_id}")
async def get_fix_verification(
    verification_id: uuid.UUID,
    user: User = Depends(_authorized_user),
    db: AsyncSession = Depends(get_db),
):
    row = await _load(db, verification_id)
    link, installation = (None, None)
    if row is not None:
        link, installation = await access_context(db, row)
    body = None if row is None else _visible_body(row, user.id, link, installation, direct=False)
    if body is None:
        raise HTTPException(status_code=404, detail="not_found")
    return body


@router.get("/{verification_id}/receipt")
async def get_fix_verification_receipt(
    verification_id: uuid.UUID,
    user: User = Depends(_authorized_user),
    db: AsyncSession = Depends(get_db),
):
    row = await _load(db, verification_id)
    link, installation = (None, None)
    if row is not None:
        link, installation = await access_context(db, row)
    if row is None or not viewer_can_read(user.id, row, link, installation):
        raise HTTPException(status_code=404, detail="not_found")
    receipt_id = await request_verification_receipt(db, row)
    if receipt_id is None:
        raise HTTPException(status_code=404, detail="not_found")
    return {"receipt_id": receipt_id}


@router.post("/{verification_id}/replay")
async def replay_fix_verification(
    verification_id: uuid.UUID,
    user: User = Depends(_authorized_user),
    db: AsyncSession = Depends(get_db),
):
    row = await _load(db, verification_id)
    link, installation = (None, None)
    if row is not None:
        link, installation = await access_context(db, row)
    if row is None or not viewer_can_replay(user.id, link, installation):
        raise HTTPException(status_code=404, detail="not_found")
    try:
        updated = await replay_verification(db, row, user=user)
    except FeatureDisabled:
        raise HTTPException(status_code=404, detail="not_found") from None
    except NotOptedIn:
        raise HTTPException(status_code=403, detail="not_opted_in") from None
    except InvalidVerificationRequest as exc:
        raise HTTPException(status_code=422, detail=exc.code) from None
    except QuotaExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail="quota_exceeded",
            headers={"Retry-After": str(exc.retry_after)},
        ) from None
    link, installation = await access_context(db, updated)
    await db.commit()
    body = _visible_body(updated, user.id, link, installation, direct=False)
    if body is None:
        raise HTTPException(status_code=404, detail="not_found")
    return body
