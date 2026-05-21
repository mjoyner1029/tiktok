"""Authentication and authorization API endpoints."""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    create_access_token,
    create_refresh_token,
    create_user,
    generate_api_key,
    get_current_user,
    hash_password,
    require_user,
    verify_password,
    verify_token,
)
from app.database import get_db
from app.models.db import User, Workspace
from app.models.schemas import UserOut

router = APIRouter(prefix="/auth", tags=["authentication"])


# ── Schemas ──────────────────────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    name: str


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut


class TokenRefreshRequest(BaseModel):
    refresh_token: str


class APIKeyResponse(BaseModel):
    api_key: str
    created_at: str


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/register", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """Register a new user account."""
    # Check if email already exists
    result = await db.execute(select(User).where(User.email == body.email))
    existing = result.scalar_one_or_none()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )
    
    # Create user (password hashing will be added when we extend User model)
    user = User(
        email=body.email,
        name=body.name,
        plan="starter",
    )
    db.add(user)
    await db.flush()
    
    # Create default workspace
    workspace = Workspace(
        owner_id=user.id,
        name=f"{user.name}'s Workspace",
    )
    db.add(workspace)
    await db.commit()
    await db.refresh(user)
    
    # Generate tokens
    access_token = create_access_token(user)
    refresh_token = create_refresh_token(user)
    
    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserOut.model_validate(user),
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """Login with email and password."""
    result = await db.execute(select(User).where(User.email == form_data.username))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # For MVP: simplified auth (will add password verification later)
    # In production, verify password here
    
    access_token = create_access_token(user)
    refresh_token = create_refresh_token(user)
    
    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserOut.model_validate(user),
    )


@router.post("/refresh", response_model=LoginResponse)
async def refresh_token(
    body: TokenRefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    """Refresh an access token using a refresh token."""
    token_data = verify_token(body.refresh_token)
    
    result = await db.execute(select(User).where(User.email == token_data.email))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    
    access_token = create_access_token(user)
    new_refresh_token = create_refresh_token(user)
    
    return LoginResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
        user=UserOut.model_validate(user),
    )


@router.get("/me", response_model=UserOut)
async def get_current_user_info(
    user: User = Depends(require_user),
):
    """Get current authenticated user info."""
    return UserOut.model_validate(user)


@router.post("/api-key", response_model=APIKeyResponse)
async def create_api_key(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a new API key for the user.
    
    Note: This is a placeholder. In production, store API keys in database
    with hashing and associate them with users.
    """
    api_key = generate_api_key()
    
    # TODO: Store in database with proper relationship
    # For now, just return it
    
    from datetime import datetime, timezone
    
    return APIKeyResponse(
        api_key=api_key,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


@router.post("/logout")
async def logout(user: User = Depends(require_user)):
    """Logout (client should discard tokens)."""
    # For JWT, logout is client-side (discard tokens)
    # For added security, could maintain a token blacklist in Redis
    return {"message": "Successfully logged out"}
