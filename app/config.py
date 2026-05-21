"""Centralised application settings — loaded from env / .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── app ──────────────────────────────────────────────────────────────
    app_name: str = "TikTok Style Engine"
    debug: bool = False
    environment: Literal["development", "staging", "production", "testing"] = "development"
    secret_key: str = "CHANGE_ME_IN_PRODUCTION_OR_APP_WILL_NOT_START"  # MUST override
    allowed_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]
    
    # ── security ─────────────────────────────────────────────────────────
    require_https: bool = False  # Enable in production
    cors_allow_credentials: bool = True
    rate_limit_per_minute: int = 60
    url_signing_key: str = ""  # For signed download URLs (should be different from secret_key)
    
    # ── database ─────────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/tiktok_engine"
    database_echo: bool = False

    # ── redis ────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── storage ──────────────────────────────────────────────────────────
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_root: str = "./storage"
    s3_bucket: str = ""
    s3_region: str = "us-east-1"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_endpoint_url: Optional[str] = None  # for MinIO / R2

    # ── anthropic / claude ───────────────────────────────────────────────
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"
    anthropic_max_tokens: int = 8192
    anthropic_temperature: float = 0.7
    anthropic_timeout: int = 120  # seconds
    
    # ── stripe billing ───────────────────────────────────────────────────
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_ids: dict[str, str] = {}  # {"starter": "price_xxx", ...}
    
    # ── observability ────────────────────────────────────────────────────
    sentry_dsn: str = ""
    enable_metrics: bool = True
    log_level: str = "INFO"
    json_logs: bool = False  # Enable in production

    # ── ffmpeg ───────────────────────────────────────────────────────────
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"
    render_output_dir: str = "./renders"

    # ── whisper (transcription) ──────────────────────────────────────────
    whisper_model: str = "base"  # tiny | base | small | medium | large-v3
    whisper_device: str = "cpu"

    # ── celery ───────────────────────────────────────────────────────────
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # ── limits ───────────────────────────────────────────────────────────
    max_upload_size_mb: int = 500
    max_clip_duration_sec: int = 300
    max_output_duration_sec: int = 180
    max_references_per_project: int = 5
    max_raw_clips_per_project: int = 10

    # ── export ───────────────────────────────────────────────────────────
    export_width: int = 1080
    export_height: int = 1920
    export_fps: int = 30
    export_video_bitrate: str = "8M"
    export_audio_bitrate: str = "192k"
    
    # ── content safety ───────────────────────────────────────────────────
    enable_content_moderation: bool = False
    max_video_duration_sec: int = 600  # 10 minutes
    allowed_video_formats: list[str] = ["mp4", "mov", "avi", "mkv", "webm"]
    
    # ── performance ──────────────────────────────────────────────────────
    enable_cache: bool = True
    cache_ttl_seconds: int = 3600
    max_concurrent_renders: int = 3
    whisper_cache_models: bool = True

    # ── derived helpers ──────────────────────────────────────────────────
    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024
    
    def validate_production_settings(self):
        """Validate critical settings for production deployment."""
        if self.environment == "production":
            assert self.secret_key != "CHANGE_ME_IN_PRODUCTION_OR_APP_WILL_NOT_START", "CRITICAL: Must set SECRET_KEY in production!"
            assert self.anthropic_api_key, "Must set ANTHROPIC_API_KEY"
            assert self.require_https, "Must enable HTTPS in production"
            assert self.json_logs, "Must enable JSON logs in production"
            if self.storage_backend == "s3":
                assert self.s3_bucket, "Must set S3_BUCKET when using S3 storage"


@lru_cache
def get_settings() -> Settings:
    return Settings()
