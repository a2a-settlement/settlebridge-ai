"""Sign in to SettleBridge with GitHub, creating the user on first sign-in.

Uses the same GitHub App OAuth client as identity linking. The signed ``state``
carries a hash of a one-time ``nonce`` that only the browser that started the
flow holds, so a code and state replayed into another browser are refused.

A GitHub account already linked to a user signs in as that user. A new one gets
a user, a linked identity and, when the exchange is reachable, an exchange
account. The OAuth code and user access token never leave this module.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode

from jose import JOSEError, jwt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.github import GitHubIdentity
from app.models.user import User, UserType
from app.services import exchange
from app.services import github_identity as oauth

logger = logging.getLogger(__name__)

_STATE_PURPOSE = "github_login"
_STATE_TTL_SECONDS = 600
_EMAIL_DOMAIN = "github.settlebridge.ai"


def _nonce_hash(nonce: str) -> str:
    return hashlib.sha256(nonce.encode()).hexdigest()


def safe_next(value: object) -> str | None:
    """A same-site path to return to after sign-in, or None."""
    if not isinstance(value, str) or not value.startswith("/") or len(value) > 512:
        return None
    if value.startswith(("//", "/\\")) or any(ord(char) < 32 for char in value):
        return None
    return value


def build_login_url(next_path: object = None) -> tuple[str, str]:
    """``(authorize_url, nonce)``. The caller keeps ``nonce`` in the browser that started it."""
    nonce = secrets.token_urlsafe(24)
    secret, algorithm = oauth._state_signing()
    payload = {
        "purpose": _STATE_PURPOSE,
        "nonce_hash": _nonce_hash(nonce),
        "exp": int(time.time()) + _STATE_TTL_SECONDS,
    }
    returned = safe_next(next_path)
    if returned:
        payload["next"] = returned
    state = jwt.encode(payload, secret, algorithm=algorithm)
    if isinstance(state, bytes):
        state = state.decode("ascii")
    query = urlencode({"client_id": oauth._oauth_client_id(), "state": state})
    return f"{oauth._oauth_base()}/login/oauth/authorize?{query}", nonce


def _verified_state(state: object, nonce: object) -> dict:
    if not isinstance(state, str) or not state or not isinstance(nonce, str) or not nonce:
        raise ValueError("invalid oauth state")
    secret, algorithm = oauth._state_signing()
    try:
        payload = jwt.decode(
            state,
            secret,
            algorithms=[algorithm],
            options={"require_exp": True, "verify_aud": False, "verify_iss": False},
        )
    except JOSEError:
        raise ValueError("invalid oauth state") from None
    expected = payload.get("nonce_hash")
    if payload.get("purpose") != _STATE_PURPOSE or not isinstance(expected, str):
        raise ValueError("invalid oauth state")
    if not hmac.compare_digest(expected, _nonce_hash(nonce)):
        raise ValueError("invalid oauth state")
    return payload


async def _identity_by_github_id(db: AsyncSession, github_user_id: int) -> GitHubIdentity | None:
    statement = select(GitHubIdentity).where(GitHubIdentity.github_user_id == github_user_id)
    return (await db.execute(statement)).scalar_one_or_none()


async def _user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()


def _register_exchange_account(user: User) -> None:
    try:
        result = exchange.register_account(
            bot_name=f"gh-{user.display_name}"[:64],
            developer_id=user.display_name,
            developer_name=user.display_name,
            contact_email=user.email,
        )
    except Exception:
        logger.warning("exchange registration failed for a new GitHub user", exc_info=True)
        return
    account = result.get("account") if isinstance(result, dict) else None
    api_key = result.get("api_key") if isinstance(result, dict) else None
    if isinstance(account, dict) and account.get("id") and isinstance(api_key, str):
        user.exchange_bot_id = str(account["id"])
        user.exchange_api_key = api_key


async def ensure_exchange_account(db: AsyncSession, user: User) -> bool:
    """Create an exchange account for ``user`` if it has none. True when it has one after."""
    if user.exchange_bot_id:
        return True
    await asyncio.to_thread(_register_exchange_account, user)
    if user.exchange_bot_id:
        await db.flush()
        return True
    return False


async def _create_user(db: AsyncSession, github_user_id: int, login: str) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{github_user_id}@{_EMAIL_DOMAIN}",
        password_hash=None,
        display_name=login,
        user_type=UserType.BOTH,
    )
    db.add(user)
    db.add(
        GitHubIdentity(
            id=uuid.uuid4(),
            user_id=user.id,
            github_user_id=github_user_id,
            login=login,
            verified_at=datetime.now(timezone.utc),
        )
    )
    await db.flush()
    if await ensure_exchange_account(db, user):
        await _starter_deposit(user)
    return user


async def _starter_deposit(user: User) -> None:
    amount = int(settings.GITHUB_LOGIN_STARTER_ATE)
    if not settings.GITHUB_SETUP_FUNDING_ENABLED or amount <= 0:
        return
    try:
        await asyncio.to_thread(exchange.deposit, user, amount, "settlebridge-github-signup")
    except Exception:
        logger.warning("starter deposit failed for a new GitHub user", exc_info=True)


async def complete_login(db: AsyncSession, code: object, state: object, nonce: object) -> dict:
    """Sign in the GitHub user behind ``code``. Commits. Returns the user and where to go next."""
    payload = _verified_state(state, nonce)
    if not isinstance(code, str) or not code or any(char.isspace() for char in code):
        raise ValueError("missing oauth code")
    async with oauth._client() as client:
        access_token = await oauth._exchange_code(
            client, oauth._oauth_client_id(), oauth._oauth_client_secret(), code
        )
        github_user_id, login = await oauth._fetch_github_user(client, access_token)

    created = False
    identity = await _identity_by_github_id(db, github_user_id)
    if identity is None:
        try:
            user = await _create_user(db, github_user_id, login)
            await db.commit()
            created = True
        except IntegrityError:
            # Another request created this GitHub user first; sign in as it.
            await db.rollback()
            identity = await _identity_by_github_id(db, github_user_id)
            if identity is None:
                raise ValueError("could not create the GitHub user") from None
    if identity is not None:
        user = await _user(db, identity.user_id)
        if user is None:
            raise ValueError("linked user not found")
        if identity.login != login:
            identity.login = login
        await db.commit()
    return {"user": user, "login": login, "created": created, "next": safe_next(payload.get("next"))}
