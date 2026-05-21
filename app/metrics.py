"""Prometheus metrics for monitoring."""

from prometheus_client import Counter, Histogram, Gauge, Info
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from fastapi import Response
from fastapi.routing import APIRoute
import time

# ── Info ─────────────────────────────────────────────────────────────────────

app_info = Info("tiktok_engine", "Application information")
app_info.info({"version": "0.1.0", "environment": "production"})

# ── Counters ─────────────────────────────────────────────────────────────────

http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)

renders_total = Counter(
    "renders_total",
    "Total renders created",
    ["status"],  # queued, completed, failed
)

transcriptions_total = Counter(
    "transcriptions_total",
    "Total transcriptions",
    ["status"],
)

ai_requests_total = Counter(
    "ai_requests_total",
    "Total AI API requests",
    ["provider", "model", "status"],
)

# ── Histograms ───────────────────────────────────────────────────────────────

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
)

render_duration_seconds = Histogram(
    "render_duration_seconds",
    "Render duration in seconds",
    buckets=[10, 30, 60, 120, 300, 600],  # 10s to 10min
)

ai_request_duration_seconds = Histogram(
    "ai_request_duration_seconds",
    "AI API request duration in seconds",
    ["provider", "model"],
    buckets=[0.5, 1, 2, 5, 10, 30],
)

transcription_duration_seconds = Histogram(
    "transcription_duration_seconds",
    "Transcription duration in seconds",
    buckets=[5, 10, 30, 60, 120, 300],
)

# ── Gauges ───────────────────────────────────────────────────────────────────

renders_in_progress = Gauge(
    "renders_in_progress",
    "Number of renders currently in progress",
)

active_projects = Gauge(
    "active_projects",
    "Number of active projects",
)

storage_bytes_used = Gauge(
    "storage_bytes_used",
    "Storage space used in bytes",
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def track_request(method: str, endpoint: str, status_code: int, duration: float):
    """Track HTTP request metrics."""
    http_requests_total.labels(method=method, endpoint=endpoint, status=status_code).inc()
    http_request_duration_seconds.labels(method=method, endpoint=endpoint).observe(duration)


def track_render(status: str, duration: float = None):
    """Track render metrics."""
    renders_total.labels(status=status).inc()
    if duration is not None:
        render_duration_seconds.observe(duration)


def track_ai_request(provider: str, model: str, status: str, duration: float):
    """Track AI API request metrics."""
    ai_requests_total.labels(provider=provider, model=model, status=status).inc()
    ai_request_duration_seconds.labels(provider=provider, model=model).observe(duration)


def track_transcription(status: str, duration: float):
    """Track transcription metrics."""
    transcriptions_total.labels(status=status).inc()
    transcription_duration_seconds.observe(duration)


# ── Metrics Endpoint ─────────────────────────────────────────────────────────


async def metrics_endpoint():
    """Prometheus metrics endpoint."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
