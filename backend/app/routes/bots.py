"""Managed agent routes — list and manage exchange agents the caller controls.

Namespace is the developer_id of the user's linked exchange account (read from
the exchange). Listing and mutations use the user's exchange API key against
ownership-scoped exchange endpoints — never the dashboard key.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
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


def _require_linked(user: User) -> None:
    if not user.exchange_bot_id or not user.exchange_api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No exchange account linked. Connect your exchange account in Settings first.",
        )


def _map_exchange_http_error(exc: Exception, *, not_found: str = "Agent not found.") -> None:
    err = str(exc)
    if "403" in err:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to manage this agent.",
        )
    if "404" in err:
        raise HTTPException(status_code=404, detail=not_found)
    if "409" in err:
        raise HTTPException(
            status_code=409,
            detail="Developer ID already claimed by another account.",
        )
    raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=err)


@router.get("")
async def list_bots(user: User = Depends(get_current_user)) -> dict:
    """List exchange agents under the developer ID the caller controls."""
    _require_linked(user)
    try:
        result = exchange_svc.list_managed_accounts(user)
    except Exception as exc:
        logger.exception("Failed to list managed agents")
        _map_exchange_http_error(exc, not_found="Exchange account not found.")
        raise  # pragma: no cover
    bots = result.get("bots") or []
    for bot in bots:
        bot["own"] = bot.get("id") == user.exchange_bot_id
    return {
        "developer_id": result.get("developer_id"),
        "bots": bots,
        "count": result.get("count", len(bots)),
    }


@router.post("/{bot_id}/rotate-key", response_model=RotateKeyResult)
async def rotate_bot_key(
    bot_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RotateKeyResult:
    """Rotate an agent key the caller controls. Shown once — store immediately."""
    _require_linked(user)
    try:
        result = exchange_svc.rotate_managed_key(user, bot_id=bot_id)
    except Exception as exc:
        logger.exception("rotate_managed_key failed for %s", bot_id)
        _map_exchange_http_error(exc)
        raise  # pragma: no cover

    new_key = result["api_key"]
    if user.exchange_bot_id == bot_id:
        user.exchange_api_key = new_key
        await db.commit()
        logger.info("Auto-updated exchange_api_key for user %s after key rotation", user.id)

    bot_name = bot_id
    try:
        bot_name = exchange_svc.get_account(bot_id).get("bot_name") or bot_id
    except Exception:
        pass

    return RotateKeyResult(
        bot_id=bot_id,
        bot_name=bot_name,
        api_key=new_key,
        grace_period_minutes=result.get("grace_period_minutes", 5),
    )


@router.post("/{bot_id}/suspend")
async def suspend_bot(
    bot_id: str,
    user: User = Depends(get_current_user),
) -> dict:
    """Suspend an agent the caller controls (stops new escrows)."""
    _require_linked(user)
    if bot_id == user.exchange_bot_id:
        raise HTTPException(
            status_code=400,
            detail="You can't suspend your own linked account from here.",
        )
    try:
        return exchange_svc.suspend_managed_account(user, bot_id=bot_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("suspend_managed_account failed for %s", bot_id)
        _map_exchange_http_error(exc)
        raise  # pragma: no cover


@router.post("/{bot_id}/unsuspend")
async def unsuspend_bot(
    bot_id: str,
    user: User = Depends(get_current_user),
) -> dict:
    """Reactivate a suspended agent the caller controls."""
    _require_linked(user)
    try:
        return exchange_svc.unsuspend_managed_account(user, bot_id=bot_id)
    except Exception as exc:
        logger.exception("unsuspend_managed_account failed for %s", bot_id)
        _map_exchange_http_error(exc)
        raise  # pragma: no cover


@router.patch("/settings")
async def update_bot_settings(
    body: UpdateManagedDeveloperIdRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Record the managed developer ID. Must match the linked exchange account."""
    _require_linked(user)
    try:
        namespace = exchange_svc.own_namespace(user)
    except Exception as exc:
        logger.warning("Failed to read namespace for user %s: %s", user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Couldn't read your exchange account.",
        )
    if not namespace:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your linked exchange account has no developer ID.",
        )
    if body.developer_id != namespace:
        raise HTTPException(
            status_code=403,
            detail=f"Your developer ID is {namespace}, from your linked exchange account.",
        )
    user.managed_developer_id = namespace
    await db.commit()
    return {"managed_developer_id": user.managed_developer_id, "status": "updated"}
