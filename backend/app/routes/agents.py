from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request

from app.services import exchange as exchange_svc

if TYPE_CHECKING:
    from app.gateway.reputation_cache import ReputationCache

router = APIRouter()
logger = logging.getLogger(__name__)


def _get_rep_cache(request: Request) -> ReputationCache | None:
    return getattr(request.app.state, "reputation_cache", None)


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
async def get_agent(bot_id: str, request: Request):
    try:
        account = exchange_svc.get_account(bot_id)
    except Exception as exc:
        logger.exception("Failed to fetch agent profile")
        raise HTTPException(status_code=502, detail=f"Exchange unavailable: {exc}")

    rep_cache = _get_rep_cache(request)
    if rep_cache:
        try:
            freshness = await rep_cache.get_attestation_freshness(bot_id)
            if freshness:
                account["attestation_freshness"] = freshness
        except Exception:
            logger.debug("Failed to fetch attestation freshness for %s", bot_id)

    return account
