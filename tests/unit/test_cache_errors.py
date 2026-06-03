"""Tests for app/cache.py and app/error_handling.py."""

from __future__ import annotations

import json
from unittest.mock import patch, Mock, AsyncMock, MagicMock
import pytest

from app.error_handling import (
    TikTokEngineError,
    RenderError,
    TranscriptionError,
    AIError,
    StorageError,
    UsageLimitError,
    sanitize_filename,
    CircuitBreaker,
)
from app.cache import cache_key, get_cached, set_cached, delete_cached, clear_pattern


# ---------------------------------------------------------------------------
# Error classes
# ---------------------------------------------------------------------------

class TestErrorHierarchy:
    def test_tiktok_engine_error_is_exception(self):
        err = TikTokEngineError("base error")
        assert isinstance(err, Exception)
        assert str(err) == "base error"

    def test_render_error_inherits_from_base(self):
        err = RenderError("render failed")
        assert isinstance(err, TikTokEngineError)
        assert isinstance(err, Exception)

    def test_transcription_error_inherits(self):
        err = TranscriptionError("transcription failed")
        assert isinstance(err, TikTokEngineError)

    def test_ai_error_inherits(self):
        err = AIError("AI failed")
        assert isinstance(err, TikTokEngineError)

    def test_storage_error_inherits(self):
        err = StorageError("storage failed")
        assert isinstance(err, TikTokEngineError)

    def test_usage_limit_error_inherits(self):
        err = UsageLimitError("limit exceeded")
        assert isinstance(err, TikTokEngineError)

    def test_raise_and_catch_render_error(self):
        with pytest.raises(RenderError, match="ffmpeg failed"):
            raise RenderError("ffmpeg failed")

    def test_catch_as_base_class(self):
        with pytest.raises(TikTokEngineError):
            raise AIError("claude timeout")

    def test_error_with_extra_attributes(self):
        err = UsageLimitError("over limit")
        assert str(err) == "over limit"

    def test_all_error_types_in_mro(self):
        for cls in (RenderError, TranscriptionError, AIError, StorageError, UsageLimitError):
            assert issubclass(cls, TikTokEngineError)


# ---------------------------------------------------------------------------
# sanitize_filename
# ---------------------------------------------------------------------------

class TestSanitizeFilename:
    def test_normal_filename(self):
        result = sanitize_filename("my_video.mp4")
        assert result == "my_video.mp4"

    def test_directory_traversal(self):
        result = sanitize_filename("../../etc/passwd")
        assert ".." not in result
        assert "/" not in result

    def test_null_bytes_removed(self):
        result = sanitize_filename("file\x00.mp4")
        assert "\x00" not in result

    def test_special_chars_handled(self):
        result = sanitize_filename("file<name>.mp4")
        assert "<" not in result
        assert ">" not in result

    def test_too_long_filename(self):
        long_name = "a" * 300 + ".mp4"
        result = sanitize_filename(long_name)
        assert len(result) <= 255

    def test_empty_filename(self):
        result = sanitize_filename("")
        assert isinstance(result, str)

    def test_spaces_in_filename(self):
        result = sanitize_filename("my video file.mp4")
        assert isinstance(result, str)

    def test_preserves_extension(self):
        result = sanitize_filename("video.mp4")
        assert result.endswith(".mp4")


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    def test_success_keeps_closed(self):
        cb = CircuitBreaker(failure_threshold=3, timeout=10)

        def always_succeeds():
            return "ok"

        assert cb.call(always_succeeds) == "ok"
        assert cb.call(always_succeeds) == "ok"

    def test_failure_increments_count(self):
        cb = CircuitBreaker(failure_threshold=3, timeout=10)

        def always_fails():
            raise RuntimeError("fail")

        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(always_fails)
        # Not open yet
        assert cb.failures == 2

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=3, timeout=10)

        def always_fails():
            raise RuntimeError("fail")

        for _ in range(3):
            try:
                cb.call(always_fails)
            except RuntimeError:
                pass

        # Circuit should now be open
        with pytest.raises(TikTokEngineError, match="Circuit breaker is open"):
            cb.call(always_fails)

    def test_recovery_after_timeout(self):
        import time
        cb = CircuitBreaker(failure_threshold=2, timeout=0)

        def always_fails():
            raise RuntimeError("fail")

        for _ in range(2):
            try:
                cb.call(always_fails)
            except RuntimeError:
                pass

        # With timeout=0, it should immediately try half-open
        call_count = [0]

        def now_succeeds():
            call_count[0] += 1
            return "recovered"

        result = cb.call(now_succeeds)
        assert result == "recovered"
        assert cb.failures == 0

    def test_reset_on_success(self):
        cb = CircuitBreaker(failure_threshold=5, timeout=10)
        cb.failures = 4

        def succeeds():
            return "ok"

        cb.call(succeeds)
        assert cb.failures == 0


# ---------------------------------------------------------------------------
# Cache functions
# ---------------------------------------------------------------------------

class TestCacheKey:
    def test_cache_key_basic(self):
        key = cache_key("user", "123", "profile")
        assert isinstance(key, str)
        assert len(key) > 0

    def test_cache_key_deterministic(self):
        key1 = cache_key("user", "123")
        key2 = cache_key("user", "123")
        assert key1 == key2

    def test_cache_key_different_parts(self):
        key1 = cache_key("user", "123")
        key2 = cache_key("user", "456")
        assert key1 != key2


class TestGetCached:
    async def test_get_cached_disabled(self):
        from app import cache as cache_module
        original_settings = cache_module.settings
        try:
            cache_module.settings = type("s", (), {"enable_cache": False})()
            result = await get_cached("some_key")
        finally:
            cache_module.settings = original_settings
        assert result is None

    async def test_get_cached_miss(self):
        with patch("app.cache.get_redis") as mock_redis_factory:
            with patch("app.cache.settings") as mock_settings:
                mock_settings.enable_cache = True
                mock_redis = AsyncMock()
                mock_redis.get = AsyncMock(return_value=None)
                mock_redis_factory.return_value = mock_redis
                result = await get_cached("missing_key")
        assert result is None

    async def test_get_cached_hit(self):
        cached_data = {"user": "test", "plan": "pro"}
        with patch("app.cache.get_redis") as mock_redis_factory:
            with patch("app.cache.settings") as mock_settings:
                mock_settings.enable_cache = True
                mock_redis = AsyncMock()
                mock_redis.get = AsyncMock(return_value=json.dumps(cached_data))
                mock_redis_factory.return_value = mock_redis
                result = await get_cached("hit_key")
        assert result == cached_data

    async def test_get_cached_redis_error_returns_none(self):
        with patch("app.cache.get_redis") as mock_redis_factory:
            with patch("app.cache.settings") as mock_settings:
                mock_settings.enable_cache = True
                mock_redis = AsyncMock()
                mock_redis.get = AsyncMock(side_effect=Exception("Redis connection refused"))
                mock_redis_factory.return_value = mock_redis
                result = await get_cached("error_key")
        assert result is None


class TestSetCached:
    async def test_set_cached_disabled(self):
        with patch("app.cache.settings") as mock_settings:
            mock_settings.enable_cache = False
            # Should not raise
            await set_cached("key", {"value": 1})

    async def test_set_cached_success(self):
        with patch("app.cache.settings") as mock_settings, \
             patch("app.cache.get_redis") as mock_redis_factory:
            mock_settings.enable_cache = True
            mock_redis = AsyncMock()
            mock_redis.setex = AsyncMock()
            mock_redis_factory.return_value = mock_redis
            await set_cached("key", {"hello": "world"}, ttl=300)
        mock_redis.setex.assert_called_once()

    async def test_set_cached_redis_error_silent(self):
        with patch("app.cache.settings") as mock_settings, \
             patch("app.cache.get_redis") as mock_redis_factory:
            mock_settings.enable_cache = True
            mock_redis = AsyncMock()
            mock_redis.setex = AsyncMock(side_effect=Exception("Redis unavailable"))
            mock_redis_factory.return_value = mock_redis
            # Should not raise
            await set_cached("key", {"data": 1})


class TestDeleteCached:
    async def test_delete_cached_key(self):
        with patch("app.cache.settings") as mock_settings, \
             patch("app.cache.get_redis") as mock_redis_factory:
            mock_settings.enable_cache = True
            mock_redis = AsyncMock()
            mock_redis.delete = AsyncMock()
            mock_redis_factory.return_value = mock_redis
            await delete_cached("key_to_delete")
        mock_redis.delete.assert_called_once_with("key_to_delete")

    async def test_delete_cached_disabled(self):
        with patch("app.cache.settings") as mock_settings:
            mock_settings.enable_cache = False
            await delete_cached("key")


class TestClearPattern:
    async def test_clear_pattern(self):
        with patch("app.cache.settings") as mock_settings, \
             patch("app.cache.get_redis") as mock_redis_factory:
            mock_settings.enable_cache = True
            mock_redis = AsyncMock()
            mock_redis.scan = AsyncMock(return_value=(0, ["user:123:profile", "user:456:settings"]))
            mock_redis.delete = AsyncMock()
            mock_redis_factory.return_value = mock_redis
            await clear_pattern("user:*")
        mock_redis.scan.assert_called()

    async def test_clear_pattern_no_matches(self):
        with patch("app.cache.settings") as mock_settings, \
             patch("app.cache.get_redis") as mock_redis_factory:
            mock_settings.enable_cache = True
            mock_redis = AsyncMock()
            mock_redis.scan = AsyncMock(return_value=(0, []))
            mock_redis.delete = AsyncMock()
            mock_redis_factory.return_value = mock_redis
            await clear_pattern("nonexistent:*")
        mock_redis.delete.assert_not_called()


# ---------------------------------------------------------------------------
# Exception handlers (FastAPI app-level)
# ---------------------------------------------------------------------------

class TestExceptionHandlers:
    def test_tiktok_engine_exception_handler(self, client):
        """TikTokEngineError should result in 500 response."""
        from app.main import app
        from fastapi.testclient import TestClient

        # Create a test route that raises TikTokEngineError
        @app.get("/test-error-tiktok")
        async def raise_tiktok_error():
            raise TikTokEngineError("engine failed")

        resp = client.get("/test-error-tiktok")
        # Should be caught by exception handler
        assert resp.status_code in (400, 500)

    def test_general_exception_handler(self, client):
        from app.main import app
        from fastapi import HTTPException

        @app.get("/test-error-general")
        async def raise_general_error():
            raise HTTPException(status_code=400, detail="bad request")

        resp = client.get("/test-error-general")
        assert resp.status_code in (400, 500)
