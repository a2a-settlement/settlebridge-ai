"""Bot key management routes — list and rotate keys for managed exchange bots."""
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


@router.get("")
async def list_bots(
    developer_id: str | None = None,
    user: User = Depends(get_current_user),
) -> dict:
    """List exchange bots managed by the authenticated user."""
    if not user.exchange_api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No exchange account linked. Connect your exchange account in Settings first.",
        )
    try:
        result = exchange_svc.list_managed_bots(user, developer_id=developer_id)
        return result
    except Exception as exc:
        logger.exception("Failed to list managed bots")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.post("/{bot_id}/rotate-key", response_model=RotateKeyResult)
async def rotate_bot_key(
    bot_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RotateKeyResult:
    """Rotate the API key for a managed bot. Returns the new key once — store it immediately.

    If the rotated bot is the user's own linked account, the new key is automatically
    persisted to the user record so the fund/balance flow stays healthy.
    """
    if not user.exchange_api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No exchange account linked.",
        )
    try:
        result = exchange_svc.rotate_bot_key(user, bot_id=bot_id)
    except Exception as exc:
        err = str(exc)
        if "403" in err:
            raise HTTPException(status_code=403, detail="Not authorized to rotate this bot's key.")
        if "404" in err:
            raise HTTPException(status_code=404, detail="Bot not found.")
        logger.exception("rotate_bot_key failed for %s", bot_id)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=err)

    new_key = result["api_key"]

    # Auto-persist the new key if the user just rotated their own linked bot,
    # so balance checks and escrow flows don't go stale.
    if user.exchange_bot_id == bot_id:
        user.exchange_api_key = new_key
        await db.commit()
        logger.info("Auto-updated exchange_api_key for user %s after key rotation", user.id)

    # Look up bot name from the managed-bots list for a friendlier response
    bot_name = bot_id
    try:
        bots_result = exchange_svc.list_managed_bots(user)
        for b in bots_result.get("bots", []):
            if b["id"] == bot_id:
                bot_name = b["bot_name"]
                break
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
    """Suspend a managed bot (stops it receiving new escrows)."""
    import httpx
    from app.config import settings as app_settings

    if not app_settings.A2A_DASHBOARD_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Suspend is not configured on this server. Set A2A_DASHBOARD_API_KEY.",
        )

    resp = httpx.post(
        f"{app_settings.effective_exchange_url}/v1/dashboard/agents/{bot_id}/suspend",
        headers={"Authorization": f"Bearer {app_settings.A2A_DASHBOARD_API_KEY}"},
        timeout=10,
    )
    if resp.status_code == 403:
        raise HTTPException(status_code=403, detail="Dashboard key rejected by exchange. Check A2A_DASHBOARD_API_KEY.")
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Bot not found on exchange.")
    resp.raise_for_status()
    return resp.json()


@router.post("/{bot_id}/unsuspend")
async def unsuspend_bot(
    bot_id: str,
    user: User = Depends(get_current_user),
) -> dict:
    """Reactivate a suspended managed bot."""
    import httpx
    from app.config import settings as app_settings

    if not app_settings.A2A_DASHBOARD_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unsuspend is not configured on this server. Set A2A_DASHBOARD_API_KEY.",
        )

    resp = httpx.post(
        f"{app_settings.effective_exchange_url}/v1/dashboard/agents/{bot_id}/unsuspend",
        headers={"Authorization": f"Bearer {app_settings.A2A_DASHBOARD_API_KEY}"},
        timeout=10,
    )
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Bot not found on exchange.")
    resp.raise_for_status()
    return resp.json()


@router.patch("/settings")
async def update_bot_settings(
    body: UpdateManagedDeveloperIdRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Update the developer_id namespace this user manages."""
    user.managed_developer_id = body.developer_id
    await db.commit()
    return {"managed_developer_id": user.managed_developer_id, "status": "updated"}
