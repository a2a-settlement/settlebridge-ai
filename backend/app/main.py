import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routes import agents, assist, auth, bounties, categories, claims, contracts, notifications, stats, submissions
from app.services.scheduler import run_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(run_scheduler())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title=settings.APP_NAME, version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", settings.APP_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Session-Id"],
)

app.include_router(assist.router, prefix="/api/assist", tags=["assist"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(bounties.router, prefix="/api/bounties", tags=["bounties"])
app.include_router(claims.router, prefix="/api", tags=["claims"])
app.include_router(submissions.router, prefix="/api", tags=["submissions"])
app.include_router(contracts.router, prefix="/api/contracts", tags=["contracts"])
app.include_router(agents.router, prefix="/api/agents", tags=["agents"])
app.include_router(categories.router, prefix="/api/categories", tags=["categories"])
app.include_router(notifications.router, prefix="/api/notifications", tags=["notifications"])
app.include_router(stats.router, prefix="/api/stats", tags=["stats"])


@app.get("/health")
async def health():
    return {"status": "ok", "app": settings.APP_NAME}
