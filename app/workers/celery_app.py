"""Celery application configuration."""

from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "tiktok_engine",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=600,
    task_time_limit=900,
    # Route heavy tasks to dedicated queues
    task_routes={
        "app.workers.tasks.transcribe_asset": {"queue": "media"},
        "app.workers.tasks.analyze_and_generate": {"queue": "ai"},
        "app.workers.tasks.render_project": {"queue": "render"},
    },
)

celery_app.autodiscover_tasks(["app.workers"])
