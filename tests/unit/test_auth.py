"""Tests for app/auth.py utilities and app/api/auth.py endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from app.auth import (
    hash_password,
    verify_password,
    generate_api_key,
    create_access_token,
    create_refresh_token,
    verify_token,
    require_user,
    require_plan,
    authenticate_user,
    create_user,
)
from app.models.db import User


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

class TestPasswordHashing:
    def test_hash_is_not_plaintext(self):
        hashed = hash_password("mysecretpassword")
        assert hashed != "mysecretpassword"
        assert len(hashed) > 20

    def test_verify_correct_password(self):
        hashed = hash_password("correctpassword")
        assert verify_password("correctpassword", hashed) is True

    def test_verify_wrong_password(self):
        hashed = hash_password("correctpassword")
        assert verify_password("wrongpassword", hashed) is False

    def test_hashes_are_salted(self):
        hashed1 = hash_password("samepassword")
        hashed2 = hash_password("samepassword")
        # bcrypt salts make same password produce different hashes
        assert hashed1 != hashed2


# ---------------------------------------------------------------------------
# API key generation
# ---------------------------------------------------------------------------

class TestGenerateApiKey:
    def test_returns_string(self):
        key = generate_api_key()
        assert isinstance(key, str)

    def test_starts_with_sk_prefix(self):
        key = generate_api_key()
        assert key.startswith("sk_")

    def test_keys_are_unique(self):
        keys = {generate_api_key() for _ in range(20)}
        assert len(keys) == 20


# ---------------------------------------------------------------------------
# JWT tokens
# ---------------------------------------------------------------------------

class TestJWTTokens:
    def _make_user(self):
        user = User()
        user.id = uuid.uuid4()
        user.email = "jwt@example.com"
        return user

    def test_create_access_token_returns_string(self):
        user = self._make_user()
        token = create_access_token(user)
        assert isinstance(token, str)
        assert len(token) > 10

    def test_create_refresh_token_returns_string(self):
        user = self._make_user()
        token = create_refresh_token(user)
        assert isinstance(token, str)

    def test_verify_valid_access_token(self):
        user = self._make_user()
        token = create_access_token(user)
        data = verify_token(token)
        assert data.email == user.email
        assert data.user_id == str(user.id)

    def test_verify_valid_refresh_token(self):
        user = self._make_user()
        token = create_refresh_token(user)
        data = verify_token(token)
        assert data.email == user.email

    def test_verify_invalid_token_raises_http_401(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            verify_token("not.a.valid.jwt")
        assert exc_info.value.status_code == 401

    def test_verify_expired_token_raises(self):
        from fastapi import HTTPException
        user = self._make_user()
        token = create_access_token(user, expires_delta=timedelta(seconds=-1))
        with pytest.raises(HTTPException) as exc_info:
            verify_token(token)
        assert exc_info.value.status_code == 401

    def test_create_token_with_custom_expiry(self):
        user = self._make_user()
        token = create_access_token(user, expires_delta=timedelta(hours=1))
        data = verify_token(token)
        assert data.email == user.email


# ---------------------------------------------------------------------------
# require_user
# ---------------------------------------------------------------------------

class TestRequireUser:
    def _make_user(self):
        user = User()
        user.id = uuid.uuid4()
        user.email = "req@example.com"
        return user

    @pytest.mark.asyncio
    async def test_returns_user_when_authenticated(self):
        user = self._make_user()
        result = await require_user(user)
        assert result == user

    @pytest.mark.asyncio
    async def test_raises_401_when_no_user(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await require_user(None)
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# require_plan
# ---------------------------------------------------------------------------

class TestRequirePlan:
    def _make_user(self, plan="creator_pro"):
        user = User(plan=plan)
        user.id = uuid.uuid4()
        user.email = "plan@example.com"
        return user

    def test_plan_check_factory_returns_callable(self):
        checker = require_plan("creator_pro", "agency")
        assert callable(checker)


# ---------------------------------------------------------------------------
# Auth API endpoints
# ---------------------------------------------------------------------------

class TestRegister:
    def test_register_new_user(self, client: TestClient):
        response = client.post("/api/v1/auth/register", json={
            "email": "newuser@example.com",
            "password": "pass123",
            "name": "New User",
        })
        assert response.status_code == 201
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["user"]["email"] == "newuser@example.com"
        assert data["token_type"] == "bearer"

    def test_register_duplicate_email(self, client: TestClient):
        # Register once
        client.post("/api/v1/auth/register", json={
            "email": "dup@example.com",
            "password": "pass123",
            "name": "User 1",
        })
        # Register again with same email
        response = client.post("/api/v1/auth/register", json={
            "email": "dup@example.com",
            "password": "pass456",
            "name": "User 2",
        })
        assert response.status_code == 400
        assert "already registered" in response.json()["detail"]

    def test_register_invalid_email(self, client: TestClient):
        response = client.post("/api/v1/auth/register", json={
            "email": "not-an-email",
            "password": "pass123",
            "name": "User",
        })
        assert response.status_code == 422


class TestLogin:
    def test_login_success(self, client: TestClient, test_user):
        response = client.post(
            "/api/v1/auth/login",
            data={"username": test_user.email, "password": "anything"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data

    def test_login_unknown_email(self, client: TestClient):
        response = client.post(
            "/api/v1/auth/login",
            data={"username": "nobody@example.com", "password": "pass"},
        )
        assert response.status_code == 401


class TestRefreshToken:
    def test_refresh_success(self, client: TestClient, test_user):
        refresh = create_refresh_token(test_user)
        response = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data

    def test_refresh_invalid_token(self, client: TestClient):
        response = client.post("/api/v1/auth/refresh", json={"refresh_token": "bad.token.here"})
        assert response.status_code == 401

    def test_refresh_user_not_found(self, client: TestClient):
        # Create a token for a non-existent user
        ghost = User()
        ghost.id = uuid.uuid4()
        ghost.email = "ghost@example.com"
        refresh = create_refresh_token(ghost)
        response = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        assert response.status_code == 401


class TestAuthProtectedEndpoints:
    def test_me_authenticated(self, auth_client: TestClient, test_user):
        response = auth_client.get("/api/v1/auth/me")
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == test_user.email

    def test_me_unauthenticated(self, client: TestClient):
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401

    def test_api_key_authenticated(self, auth_client: TestClient):
        response = auth_client.post("/api/v1/auth/api-key")
        assert response.status_code == 200
        data = response.json()
        assert "api_key" in data
        assert data["api_key"].startswith("sk_")

    def test_api_key_unauthenticated(self, client: TestClient):
        response = client.post("/api/v1/auth/api-key")
        assert response.status_code == 401

    def test_logout_authenticated(self, auth_client: TestClient):
        response = auth_client.post("/api/v1/auth/logout")
        assert response.status_code == 200
        data = response.json()
        assert "message" in data

    def test_logout_unauthenticated(self, client: TestClient):
        response = client.post("/api/v1/auth/logout")
        assert response.status_code == 401
