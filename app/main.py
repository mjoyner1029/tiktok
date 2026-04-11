"""FastAPI application — TikTok Style Engine API."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import engine, Base

settings = get_settings()

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create DB tables on startup (dev only — use Alembic in prod)."""
    if settings.environment == "development":
        async with engine.begin() as conn:
            # Import all models so they're registered
            import app.models.db  # noqa: F401
            await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="AI-powered TikTok-style video editing engine",
    lifespan=lifespan,
)

# ── CORS ─────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ──────────────────────────────────────────────────────────────

from app.api.projects import router as projects_router
from app.api.assets import router as assets_router
from app.api.renders import router as renders_router
from app.api.styles import router as styles_router

app.include_router(projects_router, prefix="/api/v1")
app.include_router(assets_router, prefix="/api/v1")
app.include_router(renders_router, prefix="/api/v1")
app.include_router(styles_router, prefix="/api/v1")


# ── Health check ─────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/")
async def root():
    return {
        "app": settings.app_name,
        "docs": "/docs",
        "health": "/health",
    }
