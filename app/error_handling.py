"""Error handling utilities and retry logic with exponential backoff."""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable, Optional, Type

from anthropic import APIError as AnthropicAPIError
from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)
import stripe

logger = logging.getLogger(__name__)


# ── Custom Exceptions ────────────────────────────────────────────────────────


class TikTokEngineError(Exception):
    """Base exception for TikTok Engine errors."""
    pass


class RenderError(TikTokEngineError):
    """Raised when rendering fails."""
    pass


class TranscriptionError(TikTokEngineError):
    """Raised when transcription fails."""
    pass


class AIError(TikTokEngineError):
    """Raised when AI service fails."""
    pass


class StorageError(TikTokEngineError):
    """Raised when storage operation fails."""
    pass


class UsageLimitError(TikTokEngineError):
    """Raised when user exceeds usage limits."""
    pass


# ── Retry Decorators ─────────────────────────────────────────────────────────


def retry_on_api_error(
    max_attempts: int = 3,
    min_wait: int = 1,
    max_wait: int = 10,
):
    """Retry decorator for API calls with exponential backoff."""
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(min=min_wait, max=max_wait),
        retry=retry_if_exception_type((
            AnthropicAPIError,
            stripe.error.APIConnectionError,
            stripe.error.RateLimitError,
        )),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )


def retry_on_network_error(max_attempts: int = 5):
    """Retry decorator for network failures."""
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(min=1, max=30),
        retry=retry_if_exception_type((
            ConnectionError,
            TimeoutError,
        )),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )


# ── Exception Handlers ───────────────────────────────────────────────────────


async def tiktok_engine_exception_handler(request: Request, exc: TikTokEngineError):
    """Handle custom TikTok Engine exceptions."""
    logger.error(f"{exc.__class__.__name__}: {exc}")
    
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    
    if isinstance(exc, UsageLimitError):
        status_code = status.HTTP_429_TOO_MANY_REQUESTS
    elif isinstance(exc, (RenderError, TranscriptionError, AIError)):
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    
    return JSONResponse(
        status_code=status_code,
        content={
            "error": exc.__class__.__name__,
            "message": str(exc),
            "request_id": request.headers.get("x-request-id"),
        },
    )


async def validation_exception_handler(request: Request, exc: Exception):
    """Handle validation errors."""
    logger.warning(f"Validation error: {exc}")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": "ValidationError",
            "message": str(exc),
            "request_id": request.headers.get("x-request-id"),
        },
    )


async def general_exception_handler(request: Request, exc: Exception):
    """Catch-all exception handler."""
    logger.exception(f"Unhandled exception: {exc}")
    
    # In production, don't expose internal errors
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "InternalServerError",
            "message": "An unexpected error occurred",
            "request_id": request.headers.get("x-request-id"),
        },
    )


# ── Graceful Degradation ─────────────────────────────────────────────────────


class CircuitBreaker:
    """Simple circuit breaker for external services."""
    
    def __init__(self, failure_threshold: int = 5, timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failures = 0
        self.last_failure_time = None
        self._is_open = False
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """Call a function with circuit breaker protection."""
        if self._is_open:
            import time
            if time.time() - self.last_failure_time > self.timeout:
                # Try half-open
                self._is_open = False
                self.failures = 0
            else:
                raise TikTokEngineError("Circuit breaker is open")
        
        try:
            result = func(*args, **kwargs)
            self.failures = 0
            return result
        except Exception as exc:
            self.failures += 1
            import time
            self.last_failure_time = time.time()
            
            if self.failures >= self.failure_threshold:
                self._is_open = True
                logger.error(f"Circuit breaker opened after {self.failures} failures")
            
            raise


# Global circuit breakers for external services
anthropic_circuit = CircuitBreaker(failure_threshold=5, timeout=60)
stripe_circuit = CircuitBreaker(failure_threshold=3, timeout=30)


# ── Dead Letter Queue ────────────────────────────────────────────────────────


async def send_to_dlq(task_id: str, error: Exception, payload: dict):
    """Send failed task to dead letter queue for manual inspection."""
    logger.error(
        f"Task {task_id} sent to DLQ",
        extra={
            "task_id": task_id,
            "error": str(error),
            "error_type": type(error).__name__,
            "payload": payload,
        },
    )
    
    # In production, send to Redis/SQS DLQ
    # For now, just log


# ── Input Validation ─────────────────────────────────────────────────────────


def sanitize_filename(filename: str) -> str:
    """Sanitize a filename to prevent path traversal."""
    import os
    import re
    
    # Remove any directory components
    filename = os.path.basename(filename)
    
    # Remove dangerous characters
    filename = re.sub(r'[^\w\s\-\.]', '', filename)
    
    # Limit length
    if len(filename) > 255:
        name, ext = os.path.splitext(filename)
        filename = name[:250] + ext
    
    return filename


def validate_ffmpeg_input(path: str):
    """Validate FFmpeg input path to prevent command injection."""
    import os
    
    # Must be absolute path
    if not os.path.isabs(path):
        raise ValueError("Path must be absolute")
    
    # Must exist
    if not os.path.exists(path):
        raise ValueError(f"Path does not exist: {path}")
    
    # No shell metacharacters
    dangerous_chars = [";", "|", "&", "$", "`", "\n", "\r"]
    if any(char in path for char in dangerous_chars):
        raise ValueError("Path contains dangerous characters")
    
    return True
