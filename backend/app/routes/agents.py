from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.services import exchange as exchange_svc

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("")
async def list_agents():
    try:
        directory = exchange_svc.get_directory()
    except Exception as exc:
        logger.exception("Failed to fetch agent directory")
        raise HTTPException(status_code=502, detail=f"Exchange directory unavailable: {exc}")
    bots = directory.get("bots", []) if isinstance(directory, dict) else directory
    return {"agents": bots}


@router.get("/{bot_id}")
async def get_agent(bot_id: str):
    try:
        account = exchange_svc.get_account(bot_id)
    except Exception as exc:
        logger.exception("Failed to fetch agent profile")
        raise HTTPException(status_code=502, detail=f"Exchange unavailable: {exc}")
    return account
