"""Managed bot routes: bots in the namespace of the user's own linked exchange account.

The namespace is the ``developer_id`` of the user's linked exchange account, read
from the exchange, never a value the user types. Listing uses the operator dashboard
key, so it is limited to bots in that namespace. Suspend and reactivate use that key
and the same namespace check, but only platform admins may call them. A
``developer_id`` is self-declared at registration, which is enough to scope a list
but not a suspension. Key rotation is limited to the user's own linked account.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.middleware.auth import get_current_user, is_platform_admin
from app.models.user import User
from app.services import exchange as exchange_svc

logger = logging.getLogger(__name__)

router = APIRouter()


class UpdateManagedDeveloperIdRequest(BaseModel):
    developer_id: str


class RotateKeyResult(BaseModel):
    bot_id: str
    bot_name: str
    api_key: str
    grace_period_minutes: int
    warning: str = (
        "Store this key immediately — it will not be shown again. "
        "Update clawd/.exchange-credentials.json or your credential store."
    )


def _namespace(user: User) -> str:
    if not user.exchange_bot_id or not user.exchange_api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No exchange account linked. Connect your exchange account in Settings first.",
        )
    try:
        namespace = exchange_svc.own_namespace(user)
    except Exception as exc:
        logger.warning("Failed to read namespace for user %s: %s", user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Couldn't read your exchange account.",
        )
    if namespace is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your linked exchange account has no developer ID.",
        )
    return namespace


def _require_in_namespace(user: User, bot_id: str) -> str:
    namespace = _namespace(user)
    try:
        developer_id = exchange_svc.get_account(bot_id).get("developer_id")
    except Exception:
        raise HTTPException(status_code=404, detail="Bot not found on exchange.")
    if developer_id != namespace:
        raise HTTPException(
            status_code=403,
            detail="That bot is not in your exchange account's namespace.",
        )
    return namespace


def _require_dashboard_key(action: str) -> str:
    if not settings.A2A_DASHBOARD_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{action} is not configured on this server. Set A2A_DASHBOARD_API_KEY.",
        )
    return settings.A2A_DASHBOARD_API_KEY


@router.get("")
async def list_bots(user: User = Depends(get_current_user)) -> dict:
    """List exchange bots in the namespace of the user's linked exchange account."""
    namespace = _namespace(user)
    _require_dashboard_key("Listing bots")
    try:
        bots = exchange_svc.list_namespace_bots(namespace)
    except Exception as exc:
        logger.exception("Failed to list bots in %s", namespace)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    for bot in bots:
        bot["own"] = bot["id"] == user.exchange_bot_id
    return {"developer_id": namespace, "bots": bots}


@router.post("/{bot_id}/rotate-key", response_model=RotateKeyResult)
async def rotate_bot_key(
    bot_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RotateKeyResult:
    """Rotate the key of the user's own linked account and store it. Shown once."""
    if not user.exchange_api_key or user.exchange_bot_id != bot_id:
        raise HTTPException(
            status_code=403,
            detail="Only your linked account's key can be rotated here. "
            "Rotate other bots' keys from the bot itself.",
        )
    try:
        new_key, grace_minutes = exchange_svc.rotate_api_key_with_grace(user)
    except Exception as exc:
        logger.exception("rotate_api_key failed for %s", bot_id)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    user.exchange_api_key = new_key
    await db.commit()

    bot_name = bot_id
    try:
        bot_name = exchange_svc.get_account(bot_id).get("bot_name") or bot_id
    except Exception:
        pass

    return RotateKeyResult(
        bot_id=bot_id,
        bot_name=bot_name,
        api_key=new_key,
        grace_period_minutes=grace_minutes,
    )


def _dashboard_toggle(user: User, bot_id: str, action: str) -> dict:
    if not is_platform_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only platform admins can suspend or reactivate bots.",
        )
    key = _require_dashboard_key(action.title())
    _require_in_namespace(user, bot_id)
    if action == "suspend" and bot_id == user.exchange_bot_id:
        raise HTTPException(
            status_code=400,
            detail="You can't suspend your own linked account from here.",
        )
    resp = httpx.post(
        f"{settings.effective_exchange_url}/v1/dashboard/agents/{bot_id}/{action}",
        headers={"Authorization": f"Bearer {key}"},
        timeout=10,
    )
    if resp.status_code == 403:
        raise HTTPException(
            status_code=403,
            detail="Dashboard key rejected by exchange. Check A2A_DASHBOARD_API_KEY.",
        )
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Bot not found on exchange.")
    resp.raise_for_status()
    return resp.json()


@router.post("/{bot_id}/suspend")
async def suspend_bot(bot_id: str, user: User = Depends(get_current_user)) -> dict:
    """Suspend a bot in the admin's namespace (stops it receiving new escrows)."""
    return _dashboard_toggle(user, bot_id, "suspend")


@router.post("/{bot_id}/unsuspend")
async def unsuspend_bot(bot_id: str, user: User = Depends(get_current_user)) -> dict:
    """Reactivate a suspended bot in the admin's namespace."""
    return _dashboard_toggle(user, bot_id, "unsuspend")


@router.patch("/settings")
async def update_bot_settings(
    body: UpdateManagedDeveloperIdRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Record the namespace. It must be the linked exchange account's developer ID."""
    namespace = _namespace(user)
    if body.developer_id != namespace:
        raise HTTPException(
            status_code=403,
            detail=f"Your namespace is {namespace}, from your linked exchange account.",
        )
    user.managed_developer_id = namespace
    await db.commit()
    return {"managed_developer_id": user.managed_developer_id, "status": "updated"}
