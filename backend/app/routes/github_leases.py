"""Agent-facing bounty listing, lease, submission, and heartbeat routes."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import github_leases, trust_gateway

logger = logging.getLogger(__name__)

router = APIRouter()


def _response(result: github_leases.APIResult) -> JSONResponse:
    return JSONResponse(status_code=result.status_code, content=result.body)


def _agent_claims(authorization: str | None):
    try:
        token = trust_gateway.bearer_token(authorization)
        return trust_gateway.verify_gateway_token(token)
    except trust_gateway.TrustTokenError:
        logger.info("rejected gateway token")
        return JSONResponse(status_code=401, content={"error": "invalid_token"})


def _require_read_scope(claims) -> JSONResponse | None:
    # These calls do not spend the solver's funds. mcp:read is enough, and a
    # token that still carries only the old financial scope is accepted.
    scopes = claims.scopes
    if trust_gateway.READ_SCOPE in scopes or trust_gateway.FINANCIAL_SCOPE in scopes:
        return None
    return JSONResponse(
        status_code=403,
        content={
            "error": "insufficient_scope",
            "required_scope": trust_gateway.READ_SCOPE,
        },
    )


async def _viewer_for_listing(authorization: str | None, db: AsyncSession):
    """Anonymous when the header is absent or the agent does not map to one user.

    A present header is verified. An invalid token is a 401 response. These
    reads do not require ``mcp:read``; the token only identifies the caller.
    """
    if authorization is None:
        return None
    claims = _agent_claims(authorization)
    if isinstance(claims, JSONResponse):
        return claims
    users = await github_leases.fetch_users_for_agent(db, claims.agent_id)
    if len(users) != 1:
        return None
    return users[0]


async def _json_body(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=422, content={"error": "invalid_body"})
    if not isinstance(payload, dict):
        return JSONResponse(status_code=422, content={"error": "invalid_body"})
    return payload


@router.get("/github/bounties")
async def list_github_bounties(
    language: str | None = None,
    limit: int = 50,
    authorization: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    viewer = await _viewer_for_listing(authorization, db)
    if isinstance(viewer, JSONResponse):
        return viewer
    result = await github_leases.list_bounties(
        db, language=language, limit=limit, viewer=viewer
    )
    return _response(result)


@router.post("/github/bounties/{bounty_id}/claim-lease")
async def claim_bounty_lease(
    bounty_id: uuid.UUID,
    authorization: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    claims = _agent_claims(authorization)
    if isinstance(claims, JSONResponse):
        return claims
    denied = _require_read_scope(claims)
    if denied is not None:
        return denied
    result = await github_leases.claim_lease(db, bounty_id, claims, idempotency_key)
    return _response(result)


@router.post("/github/leases/{lease_id}/renew")
async def renew_bounty_lease(
    lease_id: uuid.UUID,
    authorization: Annotated[str | None, Header()] = None,
    x_lease_token: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    claims = _agent_claims(authorization)
    if isinstance(claims, JSONResponse):
        return claims
    denied = _require_read_scope(claims)
    if denied is not None:
        return denied
    result = await github_leases.renew_lease(db, lease_id, claims, x_lease_token)
    return _response(result)


@router.post("/github/leases/{lease_id}/release")
async def release_bounty_lease(
    lease_id: uuid.UUID,
    authorization: Annotated[str | None, Header()] = None,
    x_lease_token: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    claims = _agent_claims(authorization)
    if isinstance(claims, JSONResponse):
        return claims
    denied = _require_read_scope(claims)
    if denied is not None:
        return denied
    result = await github_leases.release_lease(db, lease_id, claims, x_lease_token)
    return _response(result)


@router.post("/github/leases/{lease_id}/submit")
async def submit_bounty_lease(
    lease_id: uuid.UUID,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_lease_token: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    claims = _agent_claims(authorization)
    if isinstance(claims, JSONResponse):
        return claims
    denied = _require_read_scope(claims)
    if denied is not None:
        return denied
    body = await _json_body(request)
    if isinstance(body, JSONResponse):
        return body
    result = await github_leases.submit_lease(
        db, lease_id, claims, x_lease_token, body, idempotency_key
    )
    return _response(result)


@router.get("/github/bounties/{bounty_id}/status")
async def github_bounty_status(
    bounty_id: uuid.UUID,
    authorization: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    viewer = await _viewer_for_listing(authorization, db)
    if isinstance(viewer, JSONResponse):
        return viewer
    return _response(await github_leases.bounty_status(db, bounty_id, viewer))


@router.post("/workers/heartbeat")
async def worker_heartbeat(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    claims = _agent_claims(authorization)
    if isinstance(claims, JSONResponse):
        return claims
    denied = _require_read_scope(claims)
    if denied is not None:
        return denied
    body = await _json_body(request)
    if isinstance(body, JSONResponse):
        return body
    return _response(await github_leases.heartbeat(db, claims, body))
