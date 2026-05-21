"""Rate limiting middleware for API endpoints."""

import time
from collections import defaultdict
from typing import Callable

from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limit AI-intensive endpoints to prevent abuse and cost overruns."""
    
    def __init__(self, app):
        super().__init__(app)
        self.settings = get_settings()
        # Store: {(user_id, endpoint): [(timestamp, timestamp, ...)]}
        self.requests = defaultdict(list)
        
        # Rate limits per endpoint pattern (requests per minute)
        self.limits = {
            "/api/v1/projects/*/analyze": 10,  # AI style analysis
            "/api/v1/projects/*/revise": 20,   # AI revisions
            "/api/v1/projects/*/render": 5,    # Heavy FFmpeg renders
            "/api/v1/chat/*/message": 30,      # Chat messages
        }
    
    async def dispatch(self, request: Request, call_next: Callable):
        # Skip rate limiting for health checks and auth
        if request.url.path in ["/health", "/metrics", "/api/v1/auth/login", "/api/v1/auth/register"]:
            return await call_next(request)
        
        # Get user identifier (user_id from JWT or IP address)
        user_id = self._get_user_id(request)
        endpoint_pattern = self._match_endpoint(request.url.path)
        
        if endpoint_pattern:
            limit = self.limits[endpoint_pattern]
            key = (user_id, endpoint_pattern)
            now = time.time()
            
            # Clean old requests (older than 60 seconds)
            self.requests[key] = [ts for ts in self.requests[key] if now - ts < 60]
            
            # Check if limit exceeded
            if len(self.requests[key]) >= limit:
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded: {limit} requests per minute for this endpoint"
                )
            
            # Record this request
            self.requests[key].append(now)
        
        return await call_next(request)
    
    def _get_user_id(self, request: Request) -> str:
        """Extract user ID from JWT token or fallback to IP address."""
        # Try to get user from JWT token
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.replace("Bearer ", "")
            try:
                from app.auth import decode_token
                payload = decode_token(token)
                return payload.get("sub", request.client.host)
            except Exception:
                pass
        
        # Fallback to IP address
        return request.client.host if request.client else "unknown"
    
    def _match_endpoint(self, path: str) -> str | None:
        """Match path to a rate-limited endpoint pattern."""
        import re
        for pattern in self.limits.keys():
            # Convert pattern with * wildcards to regex
            regex = pattern.replace("*", "[^/]+")
            if re.match(f"^{regex}$", path):
                return pattern
        return None
