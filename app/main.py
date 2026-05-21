"""FastAPI application — TikTok Style Engine API."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import engine, Base
from app.error_handling import (
    TikTokEngineError,
    tiktok_engine_exception_handler,
    validation_exception_handler,
    general_exception_handler,
)
from app.logging_config import configure_logging, LoggingMiddleware
from app.metrics import metrics_endpoint

settings = get_settings()

# Validate production settings
if settings.environment == "production":
    try:
        settings.validate_production_settings()
    except AssertionError as exc:
        raise RuntimeError(f"Production configuration error: {exc}") from exc

# Configure logging and monitoring
configure_logging(
    level=settings.log_level,
    json_logs=settings.json_logs,
    sentry_dsn=settings.sentry_dsn if settings.sentry_dsn else None,
)

logger = logging.getLogger(__name__)


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
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url="/redoc" if settings.environment != "production" else None,
)

# ── Middleware ───────────────────────────────────────────────────────────

# Request logging and correlation IDs
app.add_middleware(LoggingMiddleware)

# Rate limiting for AI endpoints
from app.middleware import RateLimitMiddleware
app.add_middleware(RateLimitMiddleware)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Exception Handlers ───────────────────────────────────────────────────

app.add_exception_handler(TikTokEngineError, tiktok_engine_exception_handler)
app.add_exception_handler(ValueError, validation_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)

# ── Routers ──────────────────────────────────────────────────────────────

from app.api.auth import router as auth_router
from app.api.billing import router as billing_router
from app.api.projects import router as projects_router
from app.api.assets import router as assets_router
from app.api.renders import router as renders_router
from app.api.styles import router as styles_router
from app.api.presets import router as presets_router
from app.api.batch import router as batch_router
from app.api.downloads import router as downloads_router
from app.api.chat import router as chat_router

app.include_router(auth_router, prefix="/api/v1")
app.include_router(billing_router, prefix="/api/v1")
app.include_router(projects_router, prefix="/api/v1")
app.include_router(assets_router, prefix="/api/v1")
app.include_router(renders_router, prefix="/api/v1")
app.include_router(styles_router, prefix="/api/v1")
app.include_router(presets_router, prefix="/api/v1")
app.include_router(batch_router, prefix="/api/v1")
app.include_router(downloads_router, prefix="/api/v1")
app.include_router(chat_router, prefix="/api/v1")

# ── Health & Metrics ──────────────────────────────────────────────

@app.get("/health")
async def health():
    """Health check endpoint for load balancers."""
    return {"status": "ok", "version": "0.1.0", "environment": settings.environment}


@app.get("/metrics", include_in_schema=False)
async def metrics():
    """Prometheus metrics endpoint."""
    if not settings.enable_metrics:
        return {"error": "Metrics disabled"}
    return await metrics_endpoint()


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "app": settings.app_name,
        "version": "0.1.0",
        "docs": "/docs" if settings.environment != "production" else None,
        "health": "/health",
    }
