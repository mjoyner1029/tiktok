"""Authentication and authorization utilities.

Provides JWT token generation/validation, password hashing, and auth dependencies.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.db import User

settings = get_settings()

# ── Password Hashing ────────────────────────────────────────────────────────

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)


def generate_api_key() -> str:
    """Generate a secure random API key."""
    return f"sk_{secrets.token_urlsafe(32)}"


# ── JWT Tokens ──────────────────────────────────────────────────────────────

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days
REFRESH_TOKEN_EXPIRE_DAYS = 30


class TokenData(BaseModel):
    """JWT token payload data."""
    user_id: str
    email: str
    exp: datetime


def create_access_token(user: User, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token for a user."""
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode = {
        "sub": str(user.id),
        "email": user.email,
        "exp": expire,
        "type": "access",
    }
    
    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)
    return encoded_jwt


def create_refresh_token(user: User) -> str:
    """Create a JWT refresh token for a user."""
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    
    to_encode = {
        "sub": str(user.id),
        "email": user.email,
        "exp": expire,
        "type": "refresh",
    }
    
    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)
    return encoded_jwt


def verify_token(token: str) -> TokenData:
    """Verify and decode a JWT token."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        email: str = payload.get("email")
        exp: int = payload.get("exp")
        
        if user_id is None or email is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        return TokenData(
            user_id=user_id,
            email=email,
            exp=datetime.fromtimestamp(exp, tz=timezone.utc),
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


# ── OAuth2 Scheme ───────────────────────────────────────────────────────────

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


# ── Dependencies ────────────────────────────────────────────────────────────


async def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """Get the current authenticated user from JWT token.
    
    Returns None if no token provided (for optional auth).
    Raises 401 if token is invalid.
    """
    if not token:
        return None
    
    token_data = verify_token(token)
    
    result = await db.execute(
        select(User).where(User.email == token_data.email)
    )
    user = result.scalar_one_or_none()
    
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return user


async def require_user(
    current_user: Optional[User] = Depends(get_current_user),
) -> User:
    """Require an authenticated user (raises 401 if not logged in)."""
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return current_user


def require_plan(
    *plans: str,
) -> callable:
    """Dependency factory to require specific subscription plans."""
    async def _check_plan(user: User = Depends(require_user)) -> User:
        if user.subscription_plan.value not in plans:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This feature requires one of: {', '.join(plans)}",
            )
        return user
    return _check_plan


# Convenience: require creator_pro or agency plan
require_pro = lambda: require_plan("creator_pro", "agency")
require_agency = lambda: require_plan("agency")


# ── User Registration & Login ───────────────────────────────────────────────


async def authenticate_user(email: str, password: str, db: AsyncSession) -> Optional[User]:
    """Authenticate a user by email and password."""
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    
    if not user:
        return None
    
    # For now, we don't have password storage in the model
    # This will be extended when we add password field to User model
    # For MVP, we'll use a simpler API key-based auth
    return user


async def create_user(
    email: str,
    name: str,
    password: str,
    plan: str = "starter",
    db: AsyncSession = None,
) -> User:
    """Create a new user account."""
    # Check if user already exists
    result = await db.execute(select(User).where(User.email == email))
    existing = result.scalar_one_or_none()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )
    
    user = User(
        email=email,
        name=name,
        plan=plan,
        # password_hash will be added to model
    )
    
    db.add(user)
    await db.commit()
    await db.refresh(user)
    
    return user
