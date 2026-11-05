"""Verify a user's GitHub account through the GitHub App's user OAuth flow."""

from __future__ import annotations

import secrets
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
from jose import JOSEError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.github import GitHubIdentity

_STATE_PURPOSE = "github_identity"
_STATE_TTL_SECONDS = 600
_TOKEN_ACCEPT = "application/json"
_USER_ACCEPT = "application/vnd.github+json"
_USER_AGENT = "SettleBridge"


class IdentityConflictError(Exception):
    """The GitHub account is already linked to a different SettleBridge user."""


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=30)


def build_authorize_url(user_id: uuid.UUID) -> str:
    if not isinstance(user_id, uuid.UUID):
        raise ValueError("invalid user id")
    client_id = _oauth_client_id()
    query = urlencode(
        {
            "client_id": client_id,
            "state": _encode_state(user_id),
            "allow_signup": "false",
        }
    )
    return f"{_oauth_base()}/login/oauth/authorize?{query}"


async def complete_oauth(
    db: AsyncSession, code: str, state: str, *, user_id: uuid.UUID
) -> GitHubIdentity:
    """Link the GitHub account behind ``code`` to the signed-in ``user_id``.

    ``state`` must name the same user. Without that check, a victim who opens an
    attacker's authorize URL would link the victim's GitHub account to the
    attacker's SettleBridge user.
    """
    # The OAuth code and user access token stay in this call. Do not log or store them.
    if not isinstance(code, str) or code == "" or any(char.isspace() for char in code):
        raise ValueError("missing oauth code")
    if _user_id_from_state(state) != user_id:
        raise ValueError("invalid oauth state")
    client_id = _oauth_client_id()
    client_secret = _oauth_client_secret()
    async with _client() as client:
        access_token = await _exchange_code(client, client_id, client_secret, code)
        github_user_id, login = await _fetch_github_user(client, access_token)
    return await _upsert_identity(db, user_id, github_user_id, login)


async def get_identity_for_user(db: AsyncSession, user_id: uuid.UUID) -> GitHubIdentity | None:
    statement = select(GitHubIdentity).where(GitHubIdentity.user_id == user_id)
    return await _scalar(db, statement)


def _encode_state(user_id: uuid.UUID) -> str:
    secret, algorithm = _state_signing()
    payload = {
        "sub": str(user_id),
        "purpose": _STATE_PURPOSE,
        "nonce": secrets.token_urlsafe(16),
        "exp": int(time.time()) + _STATE_TTL_SECONDS,
    }
    encoded = jwt.encode(payload, secret, algorithm=algorithm)
    if isinstance(encoded, bytes):
        return encoded.decode("ascii")
    return encoded


def _user_id_from_state(state: str) -> uuid.UUID:
    if not isinstance(state, str) or state.strip() == "":
        raise ValueError("invalid oauth state")
    secret, algorithm = _state_signing()
    try:
        payload = jwt.decode(
            state,
            secret,
            algorithms=[algorithm],
            options={
                "verify_signature": True,
                "verify_exp": True,
                "require_exp": True,
                "verify_aud": False,
                "verify_iss": False,
            },
        )
    except JOSEError:
        raise ValueError("invalid oauth state") from None
    if payload.get("purpose") != _STATE_PURPOSE:
        raise ValueError("invalid oauth state")
    nonce = payload.get("nonce")
    if not isinstance(nonce, str) or nonce == "":
        raise ValueError("invalid oauth state")
    subject = payload.get("sub")
    if not isinstance(subject, str):
        raise ValueError("invalid oauth state")
    try:
        return uuid.UUID(subject)
    except ValueError:
        raise ValueError("invalid oauth state") from None


def _state_signing() -> tuple[str, str]:
    secret = settings.JWT_SECRET
    algorithm = settings.JWT_ALGORITHM
    if not isinstance(secret, str) or secret.strip() == "":
        raise ValueError("GitHub identity state signing is not configured")
    if not isinstance(algorithm, str) or algorithm.strip() == "" or algorithm.lower() == "none":
        raise ValueError("GitHub identity state signing is not configured")
    return secret.strip(), algorithm.strip()


def _oauth_client_id() -> str:
    client_id = settings.GITHUB_OAUTH_CLIENT_ID
    if not isinstance(client_id, str) or client_id.strip() == "":
        raise ValueError("GitHub OAuth client id is not configured")
    return client_id.strip()


def _oauth_client_secret() -> str:
    secret = settings.GITHUB_OAUTH_CLIENT_SECRET
    if not isinstance(secret, str) or secret.strip() == "":
        raise ValueError("GitHub OAuth client secret is not configured")
    return secret.strip()


def _oauth_base() -> str:
    raw = settings.GITHUB_OAUTH_URL
    if not isinstance(raw, str) or raw.strip() == "":
        raise ValueError("GitHub OAuth URL is not configured")
    return raw.strip().rstrip("/")


def _api_base() -> str:
    raw = settings.GITHUB_API_URL
    if not isinstance(raw, str) or raw.strip() == "":
        raise ValueError("GitHub API URL is not configured")
    return raw.strip().rstrip("/")


async def _exchange_code(
    client: httpx.AsyncClient, client_id: str, client_secret: str, code: str
) -> str:
    try:
        response = await client.post(
            f"{_oauth_base()}/login/oauth/access_token",
            data={"client_id": client_id, "client_secret": client_secret, "code": code},
            headers={"Accept": _TOKEN_ACCEPT},
        )
    except httpx.HTTPError:
        raise ValueError("GitHub OAuth token exchange failed") from None
    if response.status_code != 200:
        raise ValueError("GitHub OAuth token exchange failed")
    try:
        body = response.json()
    except ValueError:
        raise ValueError("GitHub OAuth token exchange failed") from None
    if not isinstance(body, dict) or body.get("error"):
        raise ValueError("GitHub OAuth token exchange failed")
    access_token = body.get("access_token")
    if (
        not isinstance(access_token, str)
        or access_token == ""
        or any(char.isspace() for char in access_token)
    ):
        raise ValueError("GitHub OAuth token exchange failed")
    return access_token


async def _fetch_github_user(client: httpx.AsyncClient, access_token: str) -> tuple[int, str]:
    try:
        response = await client.get(
            f"{_api_base()}/user",
            headers={
                "Accept": _USER_ACCEPT,
                "Authorization": f"Bearer {access_token}",
                "User-Agent": _USER_AGENT,
            },
        )
    except httpx.HTTPError:
        raise ValueError("GitHub user lookup failed") from None
    if response.status_code != 200:
        raise ValueError("GitHub user lookup failed")
    try:
        body = response.json()
    except ValueError:
        raise ValueError("GitHub user lookup failed") from None
    if not isinstance(body, dict):
        raise ValueError("GitHub user lookup failed")
    github_user_id = body.get("id")
    login = body.get("login")
    if type(github_user_id) is not int or github_user_id <= 0:
        raise ValueError("GitHub user lookup failed")
    if not isinstance(login, str) or login.strip() == "":
        raise ValueError("GitHub user lookup failed")
    return github_user_id, login


async def _upsert_identity(
    db: AsyncSession, user_id: uuid.UUID, github_user_id: int, login: str
) -> GitHubIdentity:
    existing_github = await _scalar(
        db, select(GitHubIdentity).where(GitHubIdentity.github_user_id == github_user_id)
    )
    if existing_github is not None and existing_github.user_id != user_id:
        raise IdentityConflictError("GitHub account is already linked to another user")
    existing_user = await _scalar(
        db, select(GitHubIdentity).where(GitHubIdentity.user_id == user_id)
    )
    verified_at = datetime.now(timezone.utc)
    if existing_user is None:
        identity = GitHubIdentity(
            user_id=user_id,
            github_user_id=github_user_id,
            login=login,
            verified_at=verified_at,
        )
        db.add(identity)
        return identity
    existing_user.github_user_id = github_user_id
    existing_user.login = login
    existing_user.verified_at = verified_at
    return existing_user


async def _scalar(db: AsyncSession, statement) -> GitHubIdentity | None:
    result = await db.execute(statement)
    return result.scalar_one_or_none()
