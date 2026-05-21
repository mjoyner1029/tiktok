"""Structured logging configuration with context and correlation IDs.

Provides:
- JSON-formatted logs for production
- Correlation ID tracking across requests
- Contextual logging with user/project info
- Integration with Sentry for error tracking
"""

from __future__ import annotations

import logging
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from pythonjsonlogger import jsonlogger

# Context variables for request tracking
request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
user_id_var: ContextVar[Optional[str]] = ContextVar("user_id", default=None)


# ── Processors ───────────────────────────────────────────────────────────────


def add_request_context(
    logger: logging.Logger,
    method_name: str,
    event_dict: dict,
) -> dict:
    """Add request context (request_id, user_id) to log entries."""
    request_id = request_id_var.get()
    if request_id:
        event_dict["request_id"] = request_id
    
    user_id = user_id_var.get()
    if user_id:
        event_dict["user_id"] = user_id
    
    return event_dict


def add_timestamp(logger: logging.Logger, method_name: str, event_dict: dict) -> dict:
    """Add ISO timestamp to log entries."""
    event_dict["timestamp"] = datetime.now(timezone.utc).isoformat()
    return event_dict


def add_log_level(logger: logging.Logger, method_name: str, event_dict: dict) -> dict:
    """Add log level to event dict."""
    event_dict["level"] = method_name.upper()
    return event_dict


# ── Configuration ────────────────────────────────────────────────────────────


def configure_logging(
    level: str = "INFO",
    json_logs: bool = True,
    sentry_dsn: Optional[str] = None,
):
    """Configure structured logging for the application.
    
    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        json_logs: Output logs as JSON (True for production)
        sentry_dsn: Sentry DSN for error tracking (optional)
    """
    # Configure structlog
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        add_timestamp,
        add_request_context,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]
    
    if json_logs:
        # JSON output for production
        processors = shared_processors + [
            structlog.processors.JSONRenderer(),
        ]
    else:
        # Pretty console output for development
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True),
        ]
    
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    
    # Configure standard logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )
    
    # Configure Sentry if DSN provided
    if sentry_dsn:
        try:
            import sentry_sdk
            from sentry_sdk.integrations.fastapi import FastApiIntegration
            from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
            
            sentry_sdk.init(
                dsn=sentry_dsn,
                traces_sample_rate=0.1,  # 10% of transactions
                profiles_sample_rate=0.1,  # 10% for profiling
                integrations=[
                    FastApiIntegration(),
                    SqlalchemyIntegration(),
                ],
                environment=get_environment(),
            )
        except ImportError:
            logging.warning("Sentry SDK not installed, skipping error tracking")


def get_environment() -> str:
    """Get environment name for logging/monitoring."""
    import os
    return os.getenv("ENVIRONMENT", "development")


# ── Request Context Helpers ──────────────────────────────────────────────────


def set_request_id(request_id: Optional[str] = None) -> str:
    """Set request ID for the current context."""
    if not request_id:
        request_id = str(uuid.uuid4())
    request_id_var.set(request_id)
    return request_id


def set_user_id(user_id: str):
    """Set user ID for the current context."""
    user_id_var.set(user_id)


def get_logger(name: str) -> structlog.BoundLogger:
    """Get a logger instance with structured logging."""
    return structlog.get_logger(name)


# ── FastAPI Middleware ───────────────────────────────────────────────────────


class LoggingMiddleware:
    """Middleware to add request logging and correlation IDs."""
    
    def __init__(self, app):
        self.app = app
    
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            # Set request ID from header or generate new one
            request_id = None
            for header, value in scope["headers"]:
                if header == b"x-request-id":
                    request_id = value.decode()
                    break
            
            request_id = set_request_id(request_id)
            
            # Add request ID to response headers
            async def send_with_request_id(message):
                if message["type"] == "http.response.start":
                    headers = message.get("headers", [])
                    headers.append((b"x-request-id", request_id.encode()))
                    message["headers"] = headers
                await send(message)
            
            await self.app(scope, receive, send_with_request_id)
        else:
            await self.app(scope, receive, send)
