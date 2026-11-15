"""Managed agent routes — list and manage exchange agents the caller controls.

Namespace is the developer_id(s) controlled by the user's linked exchange account
(read from the exchange). Listing and mutations use the user's exchange API key
against ownership-scoped exchange endpoints — never the dashboard key.
"""
from __future__ import annotations

import json
import logging
from typing import Any, NoReturn

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.services import exchange as exchange_svc

logger = logging.getLogger(__name__)

router = APIRouter()


class UpdateManagedDeveloperIdRequest(BaseModel):
    developer_id: str


class AttachDeveloperIdRequest(BaseModel):
    agent_api_key: str


class RegisterManagedBotRequest(BaseModel):
    bot_name: str
    developer_id: str
    description: str | None = None
    skills: list[str] = Field(default_factory=list)
    is_test: bool = False


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


def _clean_secrets(val: Any, redact_secrets: tuple[str, ...]) -> Any:
    if not redact_secrets:
        return val
    if isinstance(val, str):
        out = val
        for secret in redact_secrets:
            if secret:
                out = out.replace(secret, "[redacted]")
        return out
    if isinstance(val, list):
        return [_clean_secrets(item, redact_secrets) for item in val]
    if isinstance(val, dict):
        return {k: _clean_secrets(v, redact_secrets) for k, v in val.items()}
    return val


def _extract_exchange_error(
    exc: Exception,
    *,
    redact_secrets: tuple[str, ...] = (),
) -> tuple[int | None, Any]:
    """Return ``(status_code, detail)`` extracted from an exchange HTTP/status exception."""
    resp = getattr(exc, "response", None)
    status_code: int | None = None
    detail: Any = None
    if resp is not None:
        raw_code = getattr(resp, "status_code", None)
        if isinstance(raw_code, int):
            status_code = raw_code
        raw_text = getattr(resp, "text", None)
        if isinstance(raw_text, str) and raw_text.strip():
            try:
                parsed = json.loads(raw_text)
                if isinstance(parsed, dict) and "detail" in parsed:
                    detail = _clean_secrets(parsed["detail"], redact_secrets)
                else:
                    detail = _clean_secrets(parsed, redact_secrets)
            except ValueError:
                detail = _clean_secrets(raw_text.strip(), redact_secrets)

    err_str = _clean_secrets(str(exc), redact_secrets)
    if status_code is None:
        for code in (400, 401, 403, 404, 409, 422):
            if str(code) in err_str:
                status_code = code
                break

    return status_code, detail


def _map_exchange_http_error(
    exc: Exception,
    *,
    not_found: str = "Agent not found.",
    conflict: str = "Developer ID already claimed by another account.",
    bad_request: str | None = None,
    map_401_to_403: bool = False,
    forward_422: bool = False,
    redact_secrets: tuple[str, ...] = (),
) -> NoReturn:
    status_code, detail = _extract_exchange_error(exc, redact_secrets=redact_secrets)
    err = _clean_secrets(str(exc), redact_secrets)

    if bad_request is not None and status_code == 400:
        raise HTTPException(status_code=400, detail=detail or bad_request)
    if status_code == 403 or (map_401_to_403 and status_code == 401):
        raise HTTPException(
            status_code=403,
            detail=detail or "Not authorized to manage this agent.",
        )
    if status_code == 404:
        raise HTTPException(status_code=404, detail=detail or not_found)
    if status_code == 409:
        raise HTTPException(status_code=409, detail=detail or conflict)
    if forward_422 and status_code == 422:
        raise HTTPException(
            status_code=422,
            detail=detail if detail is not None else err,
        )
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=detail if isinstance(detail, str) and detail else err,
    )


@router.get("")
async def list_bots(user: User = Depends(get_current_user)) -> dict:
    """List exchange agents under the developer IDs the caller controls."""
    _require_linked(user)
    try:
        result = exchange_svc.list_managed_accounts(user)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to list managed agents")
        _map_exchange_http_error(
            exc,
            not_found="Exchange account not found.",
            redact_secrets=(user.exchange_api_key or "",),
        )
    bots = result.get("bots") or []
    for bot in bots:
        bot["own"] = bot.get("id") == user.exchange_bot_id
    developer_id = result.get("developer_id")
    raw_dev_ids = result.get("developer_ids")
    if raw_dev_ids is None:
        developer_ids = [developer_id] if developer_id else []
    else:
        developer_ids = list(raw_dev_ids)
    caller_account_type = result.get("caller_account_type") or "agent"
    gateway_claimed = result.get("gateway_claimed_agents")
    gateway_claimed_agents = list(gateway_claimed) if gateway_claimed is not None else []
    return {
        "developer_id": developer_id,
        "developer_ids": developer_ids,
        "caller_account_type": caller_account_type,
        "bots": bots,
        "gateway_claimed_agents": gateway_claimed_agents,
        "count": result.get("count", len(bots)),
    }


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


@router.post("/developer-ids/attach")
async def attach_developer_id(
    body: AttachDeveloperIdRequest,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Attach another developer_id by proving control of an agent API key under it."""
    _require_linked(user)
    secrets_to_redact = (body.agent_api_key, user.exchange_api_key or "")
    try:
        return exchange_svc.attach_developer_id(user, agent_api_key=body.agent_api_key)
    except HTTPException:
        raise
    except Exception as exc:
        code, _ = _extract_exchange_error(exc, redact_secrets=secrets_to_redact)
        logger.warning(
            "attach_developer_id failed for user %s (status=%s, err=%s)",
            user.id,
            code,
            type(exc).__name__,
        )
        _map_exchange_http_error(
            exc,
            not_found="Agent account not found.",
            conflict="Developer ID already claimed by another account.",
            map_401_to_403=True,
            redact_secrets=secrets_to_redact,
        )


@router.delete("/developer-ids/{developer_id}")
async def detach_developer_id(
    developer_id: str,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Detach a secondary developer_id from the caller's linked exchange account."""
    _require_linked(user)
    try:
        return exchange_svc.detach_developer_id(user, developer_id=developer_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(
            "detach_developer_id failed for user %s developer_id=%s (%s)",
            user.id,
            developer_id,
            type(exc).__name__,
        )
        _map_exchange_http_error(
            exc,
            not_found="Developer ID not found.",
            bad_request="Cannot detach your primary developer ID.",
            redact_secrets=(user.exchange_api_key or "",),
        )


@router.post("/register")
async def register_managed_bot(
    body: RegisterManagedBotRequest,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Register a new agent under a caller-controlled developer_id without mutating user."""
    _require_linked(user)
    try:
        resp = exchange_svc.register_managed_account(
            user,
            bot_name=body.bot_name,
            developer_id=body.developer_id,
            description=body.description,
            skills=body.skills,
            is_test=body.is_test,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(
            "register_managed_bot failed for user %s developer_id=%s (%s)",
            user.id,
            body.developer_id,
            type(exc).__name__,
        )
        _map_exchange_http_error(
            exc,
            conflict="An agent with this name already exists.",
            forward_422=True,
            redact_secrets=(user.exchange_api_key or "",),
        )
    return {
        "account": resp["account"],
        "api_key": resp["api_key"],
        "starter_tokens": resp.get("starter_tokens", 0),
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
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("rotate_managed_key failed for %s", bot_id)
        _map_exchange_http_error(exc, redact_secrets=(user.exchange_api_key or "",))

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
        _map_exchange_http_error(exc, redact_secrets=(user.exchange_api_key or "",))


@router.post("/{bot_id}/unsuspend")
async def unsuspend_bot(
    bot_id: str,
    user: User = Depends(get_current_user),
) -> dict:
    """Reactivate a suspended agent the caller controls."""
    _require_linked(user)
    try:
        return exchange_svc.unsuspend_managed_account(user, bot_id=bot_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("unsuspend_managed_account failed for %s", bot_id)
        _map_exchange_http_error(exc, redact_secrets=(user.exchange_api_key or "",))


@router.put("/{bot_id}/card")
async def publish_bot_card(
    bot_id: str,
    body: dict[str, Any] = Body(...),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Publish or update the Agent Card for an agent the caller controls."""
    _require_linked(user)
    try:
        return exchange_svc.publish_managed_agent_card(user, bot_id=bot_id, card=body)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(
            "publish_managed_agent_card failed for %s (%s)",
            bot_id,
            type(exc).__name__,
        )
        _map_exchange_http_error(
            exc,
            forward_422=True,
            redact_secrets=(user.exchange_api_key or "",),
        )


@router.get("/{bot_id}/settlements")
async def get_bot_settlements(
    bot_id: str,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """List recent non-mint settlements for an agent the caller controls."""
    _require_linked(user)
    try:
        return exchange_svc.get_managed_agent_settlements(user, bot_id=bot_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(
            "get_managed_agent_settlements failed for %s (%s)",
            bot_id,
            type(exc).__name__,
        )
        _map_exchange_http_error(
            exc,
            redact_secrets=(user.exchange_api_key or "",),
        )
