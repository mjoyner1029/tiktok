"""Tests for app/logging_config.py."""

import uuid
from unittest.mock import patch, Mock


class TestLoggingProcessors:
    def test_add_request_context_with_values(self):
        from app.logging_config import add_request_context, request_id_var, user_id_var
        request_id_var.set("req-123")
        user_id_var.set("user-456")
        event_dict = {}
        result = add_request_context(None, "info", event_dict)
        assert result["request_id"] == "req-123"
        assert result["user_id"] == "user-456"

    def test_add_request_context_without_values(self):
        from app.logging_config import add_request_context, request_id_var, user_id_var
        request_id_var.set(None)
        user_id_var.set(None)
        event_dict = {}
        result = add_request_context(None, "info", event_dict)
        assert "request_id" not in result
        assert "user_id" not in result

    def test_add_timestamp(self):
        from app.logging_config import add_timestamp
        event_dict = {}
        result = add_timestamp(None, "info", event_dict)
        assert "timestamp" in result
        assert "T" in result["timestamp"]

    def test_add_log_level(self):
        from app.logging_config import add_log_level
        event_dict = {}
        result = add_log_level(None, "warning", event_dict)
        assert result["level"] == "WARNING"


class TestConfigureLogging:
    def test_configure_json_logs(self):
        from app.logging_config import configure_logging
        configure_logging(level="INFO", json_logs=True)

    def test_configure_console_logs(self):
        from app.logging_config import configure_logging
        configure_logging(level="DEBUG", json_logs=False)

    def test_configure_with_sentry_dsn(self):
        """configure_logging with sentry_dsn calls sentry_sdk.init if available."""
        import sentry_sdk
        from app.logging_config import configure_logging
        with patch("sentry_sdk.init") as mock_init:
            configure_logging(level="INFO", json_logs=True, sentry_dsn="https://fake@sentry.io/123")
        mock_init.assert_called_once()

    def test_configure_with_sentry_import_error(self):
        """configure_logging with sentry_dsn logs warning if sentry_sdk missing."""
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "sentry_sdk":
                raise ImportError("sentry_sdk not installed")
            return real_import(name, *args, **kwargs)

        from app.logging_config import configure_logging
        with patch("builtins.__import__", side_effect=mock_import):
            # Should not raise, just log warning
            configure_logging(level="INFO", json_logs=True, sentry_dsn="https://fake@sentry.io/123")


class TestContextHelpers:
    def test_set_request_id_generates_uuid(self):
        from app.logging_config import set_request_id, request_id_var
        request_id_var.set(None)
        result = set_request_id()
        assert result is not None
        uuid.UUID(result)  # validates UUID format

    def test_set_request_id_custom(self):
        from app.logging_config import set_request_id
        result = set_request_id("custom-123")
        assert result == "custom-123"

    def test_set_user_id(self):
        from app.logging_config import set_user_id, user_id_var
        set_user_id("user-789")
        assert user_id_var.get() == "user-789"

    def test_get_logger_returns_structlog(self):
        from app.logging_config import get_logger
        logger = get_logger("test_logger")
        assert logger is not None

    def test_get_environment_default(self):
        from app.logging_config import get_environment
        import os
        os.environ.pop("ENVIRONMENT", None)
        env = get_environment()
        assert env == "development"

    def test_get_environment_custom(self):
        from app.logging_config import get_environment
        import os
        os.environ["ENVIRONMENT"] = "production"
        try:
            env = get_environment()
            assert env == "production"
        finally:
            os.environ.pop("ENVIRONMENT", None)


class TestLoggingMiddleware:
    import asyncio

    def test_http_request_adds_request_id(self):
        """Middleware adds x-request-id to response."""
        import asyncio
        from app.logging_config import LoggingMiddleware

        async def mock_app(scope, receive, send):
            await send({"type": "http.response.start", "headers": [], "status": 200})

        middleware = LoggingMiddleware(mock_app)

        async def run():
            scope = {"type": "http", "headers": []}
            received_messages = []

            async def async_send(msg):
                received_messages.append(msg)

            await middleware(scope, Mock(), async_send)
            return received_messages

        msgs = asyncio.run(run())
        response_start = msgs[0]
        header_names = [h[0] for h in response_start["headers"]]
        assert b"x-request-id" in header_names

    def test_non_http_scope_passes_through(self):
        """Non-http scopes are passed through unchanged."""
        import asyncio
        from app.logging_config import LoggingMiddleware

        called_with = []

        async def mock_app(scope, receive, send):
            called_with.append(scope["type"])

        middleware = LoggingMiddleware(mock_app)
        scope = {"type": "lifespan", "headers": []}

        async def run():
            await middleware(scope, Mock(), Mock())

        asyncio.run(run())
        assert "lifespan" in called_with

    def test_with_existing_request_id_header(self):
        """Middleware uses x-request-id from incoming headers."""
        import asyncio
        from app.logging_config import LoggingMiddleware

        async def mock_app(scope, receive, send):
            await send({"type": "http.response.start", "headers": [], "status": 200})

        middleware = LoggingMiddleware(mock_app)

        async def run():
            scope = {"type": "http", "headers": [(b"x-request-id", b"incoming-id-123")]}
            msgs = []
            async def async_append(msg):
                msgs.append(msg)
            await middleware(scope, Mock(), async_append)
            return msgs

        msgs = asyncio.run(run())
        response_start = msgs[0]
        headers = dict(response_start["headers"])
        assert headers.get(b"x-request-id") == b"incoming-id-123"
