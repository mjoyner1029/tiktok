"""Style profile endpoints."""

from __future__ import annotations

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.db import StyleProfile
from app.models.schemas import StyleProfileOut

router = APIRouter(prefix="/styles", tags=["styles"])


@router.get("/", response_model=List[StyleProfileOut])
async def list_all_styles(db: AsyncSession = Depends(get_db)):
    """List all saved style profiles (across projects)."""
    result = await db.execute(
        select(StyleProfile).order_by(StyleProfile.created_at.desc()).limit(50)
    )
    return result.scalars().all()


@router.get("/{style_id}", response_model=StyleProfileOut)
async def get_style(style_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    style = await db.get(StyleProfile, style_id)
    if not style:
        raise HTTPException(404, "Style profile not found")
    return style


@router.delete("/{style_id}", status_code=204)
async def delete_style(style_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    style = await db.get(StyleProfile, style_id)
    if not style:
        raise HTTPException(404, "Style profile not found")
    await db.delete(style)
