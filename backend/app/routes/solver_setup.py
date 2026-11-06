"""Solver setup status and one-time agent config for the signed-in user."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.services import exchange
from app.services.solver_setup import setup_status

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/solver/setup-status")
async def solver_setup_status(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await setup_status(db, user)


def _rotated_at() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _agent_config(account_id: str, api_key: str) -> dict:
    return {
        "account_id": account_id,
        "api_key": api_key,
        "rotated_at": _rotated_at(),
        "hosted": {
            "mcpServers": {
                "settlebridge": {
                    "url": settings.SETTLEBRIDGE_HOSTED_MCP_URL,
                    "headers": {"Authorization": f"Bearer {api_key}"},
                }
            }
        },
        "local": {
            "mcpServers": {
                "a2a-settlement": {
                    "command": "a2a-settlement-mcp",
                    "env": {
                        "SETTLEBRIDGE_GATEWAY_CLIENT_ID": account_id,
                        "SETTLEBRIDGE_GATEWAY_CLIENT_SECRET": api_key,
                    },
                }
            }
        },
    }


@router.post("/solver/agent-config")
async def solver_agent_config(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    account_id = user.exchange_bot_id
    if not account_id or not user.exchange_api_key:
        return JSONResponse(status_code=409, content={"error": "no_exchange_account"})
    try:
        api_key = await asyncio.to_thread(exchange.rotate_api_key, user)
    except Exception as exc:
        logger.info("exchange key rotation failed: %s", type(exc).__name__)
        return JSONResponse(status_code=502, content={"error": "exchange_unavailable"})
    user.exchange_api_key = api_key
    await db.commit()
    return JSONResponse(
        status_code=200,
        content=_agent_config(str(account_id), api_key),
        headers={"Cache-Control": "no-store"},
    )
