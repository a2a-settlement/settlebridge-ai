import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routes import agents, assist, auth, bounties, categories, claims, contact, contracts, notifications, stats, submissions, training
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
    if startup.exchange_connected:
        exchange_health_url = f"{settings.effective_exchange_url.rstrip('/')}/health"

    audit_logger = AuditLogger()
    health_monitor = HealthMonitor(exchange_health_url=exchange_health_url)
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
