from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.middleware.auth import (
    _get_or_create_exchange_user,
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.schemas.user import (
    ExchangeLoginRequest,
    LinkExchangeRequest,
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)
from app.services import exchange as exchange_svc
from app.services import github_login

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/register", response_model=TokenResponse)
async def register(body: UserRegisterRequest, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
        user_type=body.user_type,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = create_access_token(user.id)
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
async def login(body: UserLoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if not user or not user.password_hash or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )

    token = create_access_token(user.id)
    return TokenResponse(access_token=token)


@router.post("/exchange-login", response_model=TokenResponse)
async def exchange_login(body: ExchangeLoginRequest, db: AsyncSession = Depends(get_db)):
    if not body.api_key.startswith("ate_"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="API key must start with ate_"
        )
    try:
        user = await _get_or_create_exchange_user(body.api_key, db)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Exchange login failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to validate exchange key"
        )
    token = create_access_token(user.id)
    return TokenResponse(access_token=token)


class GitHubLoginStart(BaseModel):
    next: str | None = None


class GitHubLoginCallback(BaseModel):
    code: str = ""
    state: str = ""
    nonce: str = ""


def _require_github_login() -> None:
    if not settings.GITHUB_LOGIN_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


@router.post("/github/start")
async def github_login_start(body: GitHubLoginStart) -> dict:
    """An authorize URL and a one-time nonce the browser must send back with the code."""
    _require_github_login()
    try:
        authorize_url, nonce = github_login.build_login_url(body.next)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub sign-in is not configured",
        ) from None
    return {"authorize_url": authorize_url, "nonce": nonce}


@router.post("/github/callback")
async def github_login_callback(
    body: GitHubLoginCallback, db: AsyncSession = Depends(get_db)
) -> dict:
    _require_github_login()
    try:
        result = await github_login.complete_login(db, body.code, body.state, body.nonce)
    except ValueError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="GitHub sign-in failed. Start again from the sign-in page.",
        ) from None
    return {
        "access_token": create_access_token(result["user"].id),
        "token_type": "bearer",
        "login": result["login"],
        "created": result["created"],
        "next": result["next"],
    }


@router.post("/link-exchange", response_model=UserResponse)
async def link_exchange(
    body: LinkExchangeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.exchange_bot_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Exchange account already linked"
        )

    try:
        result = exchange_svc.register_account(
            bot_name=body.bot_name,
            developer_id=body.developer_id or user.display_name,
            developer_name=user.display_name,
            contact_email=user.email,
        )
    except Exception as exc:
        logger.exception("Exchange registration failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Exchange registration failed: {exc}",
        )

    user.exchange_bot_id = result["account"]["id"]
    user.exchange_api_key = result["api_key"]
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/me", response_model=UserResponse)
async def me(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.exchange_bot_id:
        try:
            balance_data = exchange_svc.get_balance(user)
            user.exchange_balance_cached = balance_data.get("available", 0)
            await db.commit()
            await db.refresh(user)
        except Exception:
            pass
    return user
