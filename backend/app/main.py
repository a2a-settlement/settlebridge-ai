import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routes import agents, assist, auth, bounties, categories, claims, contracts, notifications, stats, submissions
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

    audit_logger = AuditLogger()
    health_monitor = HealthMonitor()
    alerts_engine = AlertsEngine(health_monitor, rep_cache)

    gateway_routes.set_gateway_components({
        "startup": startup,
        "policy_engine": policy_engine,
        "reputation_cache": rep_cache,
        "audit_logger": audit_logger,
        "health_monitor": health_monitor,
        "alerts_engine": alerts_engine,
    })

    tasks = [
        asyncio.create_task(policy_engine.start_reload_loop()),
        asyncio.create_task(rep_cache.start_refresh_loop()),
        asyncio.create_task(health_monitor.start_ping_loop()),
        asyncio.create_task(alerts_engine.start_eval_loop()),
        asyncio.create_task(startup.start_health_loop()),
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
    allow_origins=["http://localhost:5173", settings.APP_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Session-Id"],
)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(gateway_routes.router, prefix="/api/gateway", tags=["gateway"])

if settings.MARKETPLACE_ENABLED:
    app.include_router(assist.router, prefix="/api/assist", tags=["assist"])
    app.include_router(bounties.router, prefix="/api/bounties", tags=["bounties"])
    app.include_router(claims.router, prefix="/api", tags=["claims"])
    app.include_router(submissions.router, prefix="/api", tags=["submissions"])
    app.include_router(contracts.router, prefix="/api/contracts", tags=["contracts"])
    app.include_router(agents.router, prefix="/api/agents", tags=["agents"])
    app.include_router(categories.router, prefix="/api/categories", tags=["categories"])
    app.include_router(notifications.router, prefix="/api/notifications", tags=["notifications"])
    app.include_router(stats.router, prefix="/api/stats", tags=["stats"])


@app.get("/api/config")
async def public_config():
    return {
        "marketplace_enabled": settings.MARKETPLACE_ENABLED,
        "gateway_enabled": settings.GATEWAY_ENABLED,
    }


@app.get("/health")
async def health():
    return {"status": "ok", "app": settings.APP_NAME}
