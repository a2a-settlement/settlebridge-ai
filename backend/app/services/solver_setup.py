"""Read-only solver setup status."""

from __future__ import annotations

import asyncio
import logging
import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.github import GitHubRepoLink
from app.models.user import User
from app.services import exchange, github_leases

logger = logging.getLogger(__name__)

_FINANCIAL_SCOPE = "mcp:read mcp:tool:financial"
_PROBE_TIMEOUT_S = 5.0
_ACCOUNT_PLACEHOLDER = "<account id>"
_SECRET_PLACEHOLDER = "<your exchange API key>"
_LINK_GITHUB = "/dashboard/settings/github"
_SETTINGS = "/dashboard/settings"
_BOUNTIES = "/bounties"


def _step(code: str, message: str, *, blocking: bool, url: str | None) -> dict:
    return {"code": code, "message": message, "blocking": blocking, "url": url}


def _post_token(url: str, account_id: str, api_key: str) -> httpx.Response:
    return httpx.post(
        url,
        data={
            "grant_type": "client_credentials",
            "client_id": account_id,
            "client_secret": api_key,
            "scope": _FINANCIAL_SCOPE,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=_PROBE_TIMEOUT_S,
    )


async def probe_financial_scope(account_id: str | None, api_key: str | None) -> str:
    url = settings.TRUST_GATEWAY_TOKEN_URL
    if not url or not account_id or not api_key:
        return "unknown"
    try:
        response = await asyncio.to_thread(_post_token, url, account_id, api_key)
    except Exception as exc:
        logger.info("financial scope probe failed: %s", type(exc).__name__)
        return "unknown"
    logger.info("financial scope probe status %s", response.status_code)
    if response.status_code in (400, 403):
        return "denied"
    if response.status_code != 200:
        return "unknown"
    try:
        body = response.json()
    except Exception as exc:
        logger.info("financial scope probe failed: %s", type(exc).__name__)
        return "unknown"
    token = body.get("access_token") if isinstance(body, dict) else None
    if isinstance(token, str) and token:
        return "granted"
    return "unknown"


async def _reputation(user: User) -> float | None:
    if not user.exchange_bot_id:
        return None
    try:
        balance = await asyncio.to_thread(exchange.get_balance, user)
        return float(balance["reputation"])
    except Exception as exc:
        logger.info("reputation lookup failed: %s", type(exc).__name__)
        return None


def _classify(rows, user_id: uuid.UUID, reputation: float | None) -> tuple[list[dict], int]:
    eligible: list[dict] = []
    ineligible = 0
    for binding, bounty, spec in rows:
        if bounty.requester_id == user_id:
            continue
        required = float(spec.required_ema)
        if reputation is not None and required <= reputation:
            eligible.append(
                {
                    "bounty_id": str(binding.bounty_id),
                    "repo": binding.repo_full_name,
                    "issue_number": binding.issue_number,
                    "title": bounty.title,
                    "amount_ate": int(spec.amount_ate),
                    "required_ema": required,
                }
            )
        else:
            ineligible += 1
    return eligible, ineligible


async def user_funds_repos(db: AsyncSession, user_id: uuid.UUID) -> bool:
    stmt = select(GitHubRepoLink.id).where(GitHubRepoLink.funder_user_id == user_id).limit(1)
    return (await db.execute(stmt)).first() is not None


def _next_steps(
    *,
    account_id: str | None,
    linked: bool,
    scope: str,
    reputation: float | None,
    funds: bool,
) -> list[dict]:
    steps: list[dict] = []
    if not account_id:
        steps.append(
            _step(
                "no_exchange_account",
                "Create an exchange account and sign in to SettleBridge with its API key.",
                blocking=True,
                url=_SETTINGS,
            )
        )
    if not linked:
        steps.append(
            _step(
                "github_not_linked",
                "Link the GitHub account that will open your pull requests.",
                blocking=True,
                url=_LINK_GITHUB,
            )
        )
    if scope == "denied":
        steps.append(
            _step(
                "financial_scope_denied",
                "This exchange key was refused the mcp:read mcp:tool:financial scope.",
                blocking=True,
                url=None,
            )
        )
    default_ema = settings.GITHUB_DEFAULT_REQUIRED_EMA
    if reputation is not None and reputation < default_ema:
        steps.append(
            _step(
                "reputation_below_most_repos",
                (
                    f"Most repos require a reputation of {default_ema:g}. "
                    "Starter repos can be lower."
                ),
                blocking=False,
                url=None,
            )
        )
    if funds:
        steps.append(
            _step(
                "funds_own_repos",
                (
                    "Pull requests from your linked GitHub account cannot claim "
                    "bounties on repos you fund."
                ),
                blocking=False,
                url=None,
            )
        )
    return steps


def _mcp_config(account_id: str | None) -> dict:
    return {
        "mcpServers": {
            "a2a-settlement": {
                "command": "a2a-settlement-mcp",
                "env": {
                    "SETTLEBRIDGE_GATEWAY_CLIENT_ID": account_id or _ACCOUNT_PLACEHOLDER,
                    "SETTLEBRIDGE_GATEWAY_CLIENT_SECRET": _SECRET_PLACEHOLDER,
                },
            }
        }
    }


async def setup_status(db: AsyncSession, user: User) -> dict:
    account_id = user.exchange_bot_id or None
    identity = await github_leases.fetch_identity_for_user(db, user.id)
    reputation = await _reputation(user)
    scope = await probe_financial_scope(account_id, user.exchange_api_key or None)
    rows = await github_leases.fetch_advertised_bounties(db, language=None, limit=100)
    eligible, ineligible = _classify(rows, user.id, reputation)
    funds = await user_funds_repos(db, user.id)
    steps = _next_steps(
        account_id=account_id,
        linked=identity is not None,
        scope=scope,
        reputation=reputation,
        funds=funds,
    )
    return {
        "exchange_account_id": account_id,
        "github_login": identity.login if identity is not None else None,
        "reputation": reputation,
        "financial_scope": scope,
        "eligible_bounties": eligible,
        "ineligible_count": ineligible,
        "next_steps": steps,
        "ready": not any(step["blocking"] for step in steps),
        "mcp_config": _mcp_config(account_id),
        "urls": {
            "link_github": _LINK_GITHUB,
            "rotate_key": _SETTINGS,
            "bounties": _BOUNTIES,
        },
    }
