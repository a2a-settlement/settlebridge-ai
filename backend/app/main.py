from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routes import agents, auth, bounties, categories, claims, notifications, stats, submissions

app = FastAPI(title=settings.APP_NAME, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", settings.APP_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(bounties.router, prefix="/api/bounties", tags=["bounties"])
app.include_router(claims.router, prefix="/api", tags=["claims"])
app.include_router(submissions.router, prefix="/api", tags=["submissions"])
app.include_router(agents.router, prefix="/api/agents", tags=["agents"])
app.include_router(categories.router, prefix="/api/categories", tags=["categories"])
app.include_router(notifications.router, prefix="/api/notifications", tags=["notifications"])
app.include_router(stats.router, prefix="/api/stats", tags=["stats"])


@app.get("/health")
async def health():
    return {"status": "ok", "app": settings.APP_NAME}
