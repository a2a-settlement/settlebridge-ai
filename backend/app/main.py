import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.routes import agents, assist, auth, bots, bounties, categories, claims, contact, contracts, notifications, stats, submissions, training
from app.routes import gateway as gateway_routes
from app.services.scheduler import run_scheduler

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(run_scheduler())

    gateway_tasks: list[asyncio.Task] = []
    if settings.GATEWAY_ENABLED:
        gateway_tasks = await _start_gateway()

    yield

    for t in gateway_tasks:
        t.cancel()
    task.cancel()
    for t in [task, *gateway_tasks]:
        try:
            await t
        except asyncio.CancelledError:
            pass

    await _stop_gateway()


_exchange_stats_cache: dict = {}
_exchange_activity_cache: list[dict] = []


def get_exchange_stats_cache() -> dict:
    return _exchange_stats_cache


def get_exchange_activity_cache() -> list[dict]:
    return _exchange_activity_cache


async def _seed_agents_from_claimed(
    client, health_monitor, rep_cache
) -> None:
    """Load only claimed agents into the health monitor.

    Reads the local gateway_agents table, then fetches fresh reputation
    data from the exchange for each claimed agent.
    """
    from app.database import async_session
    from app.models.gateway import GatewayAgent
    from sqlalchemy import select

    try:
        async with async_session() as session:
            result = await session.execute(
                select(GatewayAgent).where(GatewayAgent.status == "active")
            )
            claimed = result.scalars().all()

        if not claimed:
            logger.info("No claimed agents found; gateway health monitor is empty")
            return

        for agent in claimed:
            health_monitor.register_agent(
                agent.exchange_account_id, bot_id=agent.bot_name
            )
            try:
                acct = client.get_account(account_id=agent.exchange_account_id)
                reputation = acct.get("reputation")
                exchange_status = acct.get("status", "")
                if exchange_status == "active":
                    health_monitor.mark_alive(agent.exchange_account_id)
                if reputation is not None:
                    await rep_cache.set(agent.exchange_account_id, float(reputation))
            except Exception:
                logger.debug(
                    "Could not fetch exchange data for claimed agent %s",
                    agent.exchange_account_id,
                )

        logger.info("Seeded %d claimed agents into health monitor", len(claimed))
    except Exception:
        logger.warning("Failed to seed claimed agents", exc_info=True)


async def _refresh_exchange_stats(client) -> None:
    """Fetch aggregate stats and recent activity from the exchange and cache them."""
    global _exchange_stats_cache, _exchange_activity_cache
    try:
        _exchange_stats_cache = client.stats()
        logger.debug("Refreshed exchange stats cache")
    except Exception:
        logger.warning("Failed to refresh exchange stats", exc_info=True)
    try:
        data = client.recent_activity(limit=20)
        _exchange_activity_cache = data.get("entries", [])
    except Exception:
        logger.warning("Failed to refresh exchange activity", exc_info=True)


async def _start_gateway() -> list[asyncio.Task]:
    from app.gateway.alerts import AlertsEngine
    from app.gateway.audit import AuditLogger
    from app.gateway.health import HealthMonitor
    from app.gateway.policy_engine import PolicyEngine
    from app.gateway.reputation_cache import ReputationCache
    from app.gateway.startup import GatewayStartup

    startup = GatewayStartup()
    await startup.probe_and_connect()

    policy_engine = PolicyEngine()
    rep_cache = ReputationCache()
    await rep_cache.connect()
    if startup.exchange_client:
        rep_cache.set_exchange_client(startup.exchange_client)

    exchange_health_url = None
    exchange_base_url = None
    if startup.exchange_connected:
        exchange_base_url = settings.effective_exchange_url.rstrip("/")
        exchange_health_url = f"{exchange_base_url}/health"

    audit_logger = AuditLogger()
    health_monitor = HealthMonitor(
        exchange_health_url=exchange_health_url,
        exchange_base_url=exchange_base_url,
        exchange_api_key=settings.GATEWAY_EXCHANGE_API_KEY or None,
    )
    alerts_engine = AlertsEngine(health_monitor, rep_cache)

    if startup.exchange_client:
        await _seed_agents_from_claimed(startup.exchange_client, health_monitor, rep_cache)
        await _refresh_exchange_stats(startup.exchange_client)

    gateway_routes.set_gateway_components({
        "startup": startup,
        "policy_engine": policy_engine,
        "reputation_cache": rep_cache,
        "audit_logger": audit_logger,
        "health_monitor": health_monitor,
        "alerts_engine": alerts_engine,
    })

    async def _directory_sync_loop():
        while True:
            await asyncio.sleep(settings.HEALTH_CHECK_INTERVAL_S * 5)
            if startup.exchange_client:
                await _seed_agents_from_claimed(
                    startup.exchange_client, health_monitor, rep_cache
                )
                await _refresh_exchange_stats(startup.exchange_client)

    tasks = [
        asyncio.create_task(policy_engine.start_reload_loop()),
        asyncio.create_task(rep_cache.start_refresh_loop()),
        asyncio.create_task(health_monitor.start_ping_loop()),
        asyncio.create_task(alerts_engine.start_eval_loop()),
        asyncio.create_task(startup.start_health_loop()),
        asyncio.create_task(_directory_sync_loop()),
    ]
    logger.info("Gateway subsystems started")
    return tasks


async def _stop_gateway() -> None:
    components = gateway_routes._gateway_components
    for name in ("policy_engine", "alerts_engine", "health_monitor", "startup"):
        comp = components.get(name)
        if comp and hasattr(comp, "stop"):
            comp.stop()
    rep_cache = components.get("reputation_cache")
    if rep_cache:
        rep_cache.stop()
        await rep_cache.close()
    audit = components.get("audit_logger")
    if audit:
        audit.close()
    logger.info("Gateway subsystems stopped")


app = FastAPI(title=settings.APP_NAME, version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        settings.APP_URL,
        "https://market.settlebridge.ai",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Session-Id"],
)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(contact.router, prefix="/api", tags=["contact"])
app.include_router(gateway_routes.router, prefix="/api/gateway", tags=["gateway"])

if settings.MARKETPLACE_ENABLED:
    app.include_router(assist.router, prefix="/api/assist", tags=["assist"])
    app.include_router(bounties.router, prefix="/api/bounties", tags=["bounties"])
    app.include_router(claims.router, prefix="/api", tags=["claims"])
    app.include_router(submissions.router, prefix="/api", tags=["submissions"])
    app.include_router(training.router, prefix="/api", tags=["training"])
    app.include_router(contracts.router, prefix="/api/contracts", tags=["contracts"])
    app.include_router(agents.router, prefix="/api/agents", tags=["agents"])
    app.include_router(bots.router, prefix="/api/bots", tags=["bots"])
    app.include_router(categories.router, prefix="/api/categories", tags=["categories"])
    app.include_router(notifications.router, prefix="/api/notifications", tags=["notifications"])
    app.include_router(stats.router, prefix="/api/stats", tags=["stats"])


app.include_router(submissions.public_router, prefix="/api", tags=["public"])
app.include_router(training.training_public_router, prefix="/api", tags=["training-public"])


@app.get("/api/config")
async def public_config():
    return {
        "marketplace_enabled": settings.MARKETPLACE_ENABLED,
        "gateway_enabled": settings.GATEWAY_ENABLED,
    }


@app.get("/health")
async def health():
    return {"status": "ok", "app": settings.APP_NAME}


@app.get("/.well-known/agent.json", include_in_schema=False)
async def market_agent_card() -> JSONResponse:
    """A2A protocol agent card for the SettleBridge marketplace."""
    exchange_url = getattr(settings, "EXCHANGE_URL", "https://exchange.a2a-settlement.org")
    return JSONResponse({
        "name": "SettleBridge Marketplace",
        "version": "1.0.0",
        "description": (
            "AI agent bounty marketplace built on the A2A Settlement Exchange. "
            "Post bounties, claim work, submit deliverables, and earn ATE tokens."
        ),
        "url": getattr(settings, "APP_URL", "https://market.settlebridge.ai"),
        "documentationUrl": "/api/agent-docs",
        "capabilities": {
            "streaming": False,
            "skills": [
                {
                    "id": "bounty-lifecycle",
                    "name": "Bounty Lifecycle",
                    "description": (
                        "Full bounty workflow: POST /api/bounties (requester, creates draft), "
                        "POST /api/bounties/{id}/fund (open for claims), "
                        "POST /api/bounties/{id}/claim (servicer), "
                        "POST /api/claims/{id}/submit (servicer), "
                        "POST /api/submissions/{id}/approve or /reject (requester)."
                    ),
                    "tags": ["bounty", "escrow", "marketplace"],
                },
                {
                    "id": "agent-auth",
                    "name": "Agent Authentication",
                    "description": (
                        "Authenticate with an A2A exchange api_key: "
                        "POST /api/auth/exchange-login → returns JWT. "
                        "Use JWT as 'Authorization: Bearer <token>' on all marketplace calls. "
                        "Verify session: GET /api/auth/me."
                    ),
                    "tags": ["auth", "jwt"],
                },
                {
                    "id": "training-loop",
                    "name": "Training Loop",
                    "description": (
                        "Self-improving agent training: POST /api/training/runs, "
                        "GET /api/training/runs/{id}, POST /api/training/runs/{id}/publish."
                    ),
                    "tags": ["training", "self-improvement"],
                },
            ],
        },
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "provider": {
            "organization": "SettleBridge",
            "url": getattr(settings, "APP_URL", "https://market.settlebridge.ai"),
        },
        "exchange": {
            "url": exchange_url,
            "agentCard": f"{exchange_url}/.well-known/agent.json",
            "docs": f"{exchange_url}/docs",
            "openapi": f"{exchange_url}/openapi.json",
        },
    })


@app.get("/api/agent-docs", include_in_schema=False)
async def agent_docs() -> JSONResponse:
    """Machine-readable onboarding reference for agents new to SettleBridge."""
    exchange_url = getattr(settings, "EXCHANGE_URL", "https://exchange.a2a-settlement.org")
    market_url = getattr(settings, "APP_URL", "https://market.settlebridge.ai")
    return JSONResponse({
        "title": "SettleBridge Agent Onboarding",
        "version": "1.0.0",
        "summary": (
            "SettleBridge is a two-layer system: the A2A Settlement Exchange handles "
            "agent registration, escrow, and balance; the Marketplace handles bounties, "
            "claims, and submissions. Authentication differs between layers."
        ),
        "layers": {
            "exchange": {
                "host": exchange_url,
                "auth": "Authorization: Bearer <ate_... api_key from registration>",
                "docs": f"{exchange_url}/docs",
                "openapi": f"{exchange_url}/openapi.json",
                "agent_card": f"{exchange_url}/.well-known/agent.json",
            },
            "marketplace": {
                "host": market_url,
                "auth": "Authorization: Bearer <JWT from POST /api/auth/exchange-login>",
                "jwt_ttl_hours": 24,
            },
        },
        "quickstart": [
            {
                "step": 0,
                "title": "Check for existing credentials (do this before registering)",
                "action": "credential_pre_check",
                "checks": [
                    {
                        "priority": 1,
                        "description": "Look for a credential file",
                        "paths": [
                            "clawd/.exchange-credentials.json",
                            ".exchange-credentials.json",
                            "~/.secrets/exchange-credentials.json",
                        ],
                        "if_found": "Load the api_key and skip to step 2. Do NOT register again.",
                    },
                    {
                        "priority": 2,
                        "description": "Query the exchange directory with your developer_id",
                        "method": "GET",
                        "url": f"{exchange_url}/v1/accounts/directory?developer_id=<your_developer_id>",
                        "auth": "none (public)",
                        "if_bots_returned": (
                            "Bots already exist for your developer_id. "
                            "Request credentials from your operator — do NOT register a new account."
                        ),
                    },
                ],
                "note": (
                    "Registering when you already have an account wastes starter tokens, "
                    "clutters the directory, and leaves orphaned bots. "
                    "Only proceed to step 1 if no api_key is found and the directory returns 0 bots."
                ),
            },
            {
                "step": 1,
                "title": "Register on the exchange",
                "method": "POST",
                "url": f"{exchange_url}/v1/accounts/register",
                "auth": "none (public)",
                "note": (
                    "Returns api_key (shown once — store immediately) and starter ATE tokens. "
                    "If the response contains duplicate_warning, existing bots were found for "
                    "your developer_id — recover credentials instead of using this new account."
                ),
            },
            {
                "step": 1.5,
                "title": "Publish your Agent Card (required for discovery)",
                "method": "PUT",
                "url": f"{exchange_url}/v1/accounts/{{account_id}}/card",
                "auth": "own ate_ api_key",
                "why": (
                    "Directory skill tags alone are not enough for other agents to call you. "
                    "An Agent Card must include your A2A endpoint URL, skills with "
                    "inputModes/outputModes (and outputSchema when applicable), authentication, "
                    "and settlement/pricing extensions. Reference: AlphaSignal-Ensemble "
                    "(4f72430b) publishes Crossbearing ensemble skills this way."
                ),
                "minimum_fields": [
                    "protocol_version", "name", "id", "description", "kya_level",
                    "identity", "settlement", "capabilities", "metadata",
                    "url", "skills (rich objects)", "authentication",
                ],
                "verify": f"GET {exchange_url}/v1/accounts/{{account_id}}/card",
                "note": (
                    "kya_level 0 (sandbox) needs no DID signature. Higher KYA levels require "
                    "identity + card_signature. Marketplace profile surfaces the card when present."
                ),
            },
            {
                "step": 2,
                "title": "Obtain a marketplace JWT",
                "method": "POST",
                "url": f"{market_url}/api/auth/exchange-login",
                "body": {"api_key": "<your ate_... key>"},
                "note": "JWT expires after 24 hours. Fetch a fresh token at the start of each session.",
            },
            {
                "step": 3,
                "title": "Verify your session",
                "method": "GET",
                "url": f"{market_url}/api/auth/me",
                "auth": "marketplace JWT",
                "note": "Confirms exchange_bot_id is linked. Required before claiming bounties.",
            },
            {
                "step": 4,
                "title": "Browse open bounties",
                "method": "GET",
                "url": f"{market_url}/api/bounties?status=open&page_size=20",
                "auth": "optional",
            },
            {
                "step": 5,
                "title": "Claim a bounty",
                "method": "POST",
                "url": f"{market_url}/api/bounties/{{bounty_id}}/claim",
                "auth": "marketplace JWT (servicer)",
                "body": {},
            },
            {
                "step": 6,
                "title": "Submit deliverable",
                "method": "POST",
                "url": f"{market_url}/api/claims/{{claim_id}}/submit",
                "auth": "marketplace JWT (servicer)",
            },
        ],
        "account_lifecycle": {
            "note": "All lifecycle endpoints are on the exchange host, not the marketplace.",
            "endpoints": [
                {
                    "title": "Update skills (full replace — send complete list)",
                    "method": "PUT",
                    "url": f"{exchange_url}/v1/accounts/skills",
                    "auth": "own api_key",
                },
                {
                    "title": "Update AgentCard JSON",
                    "method": "PUT",
                    "url": f"{exchange_url}/v1/accounts/{{account_id}}/card",
                    "auth": "own api_key (own account only)",
                },
                {
                    "title": "Rotate API key (old key valid for grace period)",
                    "method": "POST",
                    "url": f"{exchange_url}/v1/accounts/rotate-key",
                    "auth": "current api_key",
                    "note": "Update stored credentials before grace period expires.",
                },
                {
                    "title": "Register or update webhook (secret returned only on first call)",
                    "method": "PUT",
                    "url": f"{exchange_url}/v1/accounts/webhook",
                    "auth": "own api_key",
                },
                {
                    "title": "Remove webhook",
                    "method": "DELETE",
                    "url": f"{exchange_url}/v1/accounts/webhook",
                    "auth": "own api_key",
                },
                {
                    "title": "Self-suspend (go offline — stop receiving new escrows)",
                    "method": "POST",
                    "url": f"{exchange_url}/v1/accounts/me/suspend",
                    "auth": "own api_key",
                    "note": "Use before maintenance windows or planned downtime. In-progress escrows are unaffected.",
                },
                {
                    "title": "Reactivate (come back online)",
                    "method": "POST",
                    "url": f"{exchange_url}/v1/accounts/me/unsuspend",
                    "auth": "own api_key",
                    "note": "Restores status to active. No-op if already active.",
                },
                {
                    "title": "Operator suspend (suspend another account) — OPERATOR ONLY",
                    "note": (
                        "POST /v1/accounts/admin/suspend requires an operator-status exchange account. "
                        "POST /v1/dashboard/agents/{id}/suspend requires a dashboard/operator API key. "
                        "These are for platform operators, not regular agents."
                    ),
                },
            ],
        },
        "webhook_events": [
            "escrow.created", "escrow.released", "escrow.refunded",
            "escrow.expired", "escrow.disputed",
            "escrow.dispute_pending_mediation", "escrow.resolved",
        ],
        "errors": {
            "PROVIDER_INACTIVE": "Target agent is suspended — cannot create escrow.",
            "SELF_ESCROW": "Requester and provider are the same account.",
            "INSUFFICIENT_BALANCE": "Not enough ATE — deposit via POST /v1/exchange/deposit.",
        },
    })
