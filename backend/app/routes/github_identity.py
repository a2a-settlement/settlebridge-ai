"""Routes for linking a verified GitHub identity to a SettleBridge user."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.github import GitHubIdentity
from app.models.user import User
from app.services.github_identity import (
    IdentityConflictError,
    build_authorize_url,
    complete_oauth,
    get_identity_for_user,
)

router = APIRouter()


def _public_identity(identity: GitHubIdentity | None) -> dict:
    if identity is None:
        return {"linked": False, "login": None, "github_user_id": None, "verified_at": None}
    verified_at = identity.verified_at
    return {
        "linked": True,
        "login": identity.login,
        "github_user_id": identity.github_user_id,
        "verified_at": verified_at.isoformat() if verified_at is not None else None,
    }


@router.get("/connect")
async def connect_github(user: User = Depends(get_current_user)) -> dict:
    try:
        authorize_url = build_authorize_url(user.id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub identity linking is not configured",
        ) from exc
    return {"authorize_url": authorize_url}


class OAuthCallbackBody(BaseModel):
    code: str = ""
    state: str = ""


@router.post("/callback")
async def github_identity_callback(
    body: OAuthCallbackBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """The frontend's OAuth return page posts ``code`` and ``state`` here.

    Requiring the signed-in user binds the link to the browser session that
    finishes the flow, not only to whoever generated ``state``.
    """
    conflict = HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="GitHub account is already linked to another user",
    )
    try:
        identity = await complete_oauth(db, body.code, body.state, user_id=user.id)
        await db.commit()
    except IdentityConflictError as exc:
        raise conflict from exc
    except IntegrityError as exc:
        await db.rollback()
        raise conflict from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="GitHub identity linking failed",
        ) from exc
    return _public_identity(identity)


@router.get("/")
async def read_github_identity(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return _public_identity(await get_identity_for_user(db, user.id))


@router.delete("/")
async def delete_github_identity(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    identity = await get_identity_for_user(db, user.id)
    if identity is not None:
        await db.delete(identity)
    await db.commit()
    return {"linked": False}
