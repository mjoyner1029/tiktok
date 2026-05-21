"""Caching utilities for performance optimization."""

from __future__ import annotations

import hashlib
import json
import logging
from functools import wraps
from typing import Any, Callable, Optional

import redis.asyncio as aioredis
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Global Redis client
_redis_client: Optional[aioredis.Redis] = None


async def get_redis() -> aioredis.Redis:
    """Get or create Redis client for caching."""
    global _redis_client
    
    if not settings.enable_cache:
        raise RuntimeError("Caching is disabled")
    
    if _redis_client is None:
        _redis_client = await aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    
    return _redis_client


def cache_key(*parts: Any) -> str:
    """Generate a cache key from parts."""
    key_str = ":".join(str(p) for p in parts)
    return hashlib.md5(key_str.encode()).hexdigest()


async def get_cached(key: str) -> Optional[Any]:
    """Get a value from cache."""
    if not settings.enable_cache:
        return None
    
    try:
        redis = await get_redis()
        value = await redis.get(key)
        if value:
            return json.loads(value)
        return None
    except Exception as exc:
        logger.warning(f"Cache get failed: {exc}")
        return None


async def set_cached(key: str, value: Any, ttl: Optional[int] = None):
    """Set a value in cache with optional TTL."""
    if not settings.enable_cache:
        return
    
    try:
        redis = await get_redis()
        ttl = ttl or settings.cache_ttl_seconds
        await redis.setex(key, ttl, json.dumps(value))
    except Exception as exc:
        logger.warning(f"Cache set failed: {exc}")


async def delete_cached(key: str):
    """Delete a key from cache."""
    if not settings.enable_cache:
        return
    
    try:
        redis = await get_redis()
        await redis.delete(key)
    except Exception as exc:
        logger.warning(f"Cache delete failed: {exc}")


async def clear_pattern(pattern: str):
    """Clear all keys matching a pattern."""
    if not settings.enable_cache:
        return
    
    try:
        redis = await get_redis()
        cursor = 0
        while True:
            cursor, keys = await redis.scan(cursor, match=pattern, count=100)
            if keys:
                await redis.delete(*keys)
            if cursor == 0:
                break
    except Exception as exc:
        logger.warning(f"Cache clear pattern failed: {exc}")


def cached(
    ttl: Optional[int] = None,
    key_prefix: str = "",
):
    """Decorator to cache function results.
    
    Usage:
        @cached(ttl=300, key_prefix="styles")
        async def get_style_profile(project_id: str):
            ...
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Generate cache key from function name and arguments
            key_parts = [key_prefix or func.__name__]
            key_parts.extend(str(arg) for arg in args)
            key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
            
            key = cache_key(*key_parts)
            
            # Try to get from cache
            cached_result = await get_cached(key)
            if cached_result is not None:
                logger.debug(f"Cache hit: {key}")
                return cached_result
            
            # Call function
            logger.debug(f"Cache miss: {key}")
            result = await func(*args, **kwargs)
            
            # Store in cache
            await set_cached(key, result, ttl)
            
            return result
        
        return wrapper
    return decorator


# ── Model Caching for Whisper ────────────────────────────────────────────────

_whisper_model_cache = {}


def cache_whisper_model(model_name: str, model):
    """Cache a Whisper model in memory."""
    if settings.whisper_cache_models:
        _whisper_model_cache[model_name] = model
        logger.info(f"Cached Whisper model: {model_name}")


def get_whisper_model(model_name: str):
    """Get cached Whisper model."""
    return _whisper_model_cache.get(model_name)


# ── Asset Thumbnail Caching ──────────────────────────────────────────────────


async def cache_thumbnail(asset_id: str, thumbnail_data: bytes, ttl: int = 86400):
    """Cache a video thumbnail (24 hour default)."""
    try:
        redis = await get_redis()
        key = f"thumbnail:{asset_id}"
        # Store as bytes, not JSON
        await redis.setex(key, ttl, thumbnail_data)
    except Exception as exc:
        logger.warning(f"Thumbnail cache failed: {exc}")


async def get_thumbnail(asset_id: str) -> Optional[bytes]:
    """Get cached thumbnail."""
    try:
        redis = await get_redis()
        key = f"thumbnail:{asset_id}"
        data = await redis.get(key)
        return data.encode() if data else None
    except Exception as exc:
        logger.warning(f"Thumbnail cache retrieval failed: {exc}")
        return None
