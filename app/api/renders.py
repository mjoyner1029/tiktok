"""Render & export endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.db import Render
from app.models.schemas import RenderOut
from app.services.storage import get_storage

router = APIRouter(prefix="/renders", tags=["renders"])


@router.get("/{render_id}", response_model=RenderOut)
async def get_render(render_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    render = await db.get(Render, render_id)
    if not render:
        raise HTTPException(404, "Render not found")
    return render


@router.get("/{render_id}/download")
async def download_render(render_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Download the rendered MP4."""
    render = await db.get(Render, render_id)
    if not render:
        raise HTTPException(404, "Render not found")
    if not render.output_url:
        raise HTTPException(400, "Render not ready")
    if render.status.value != "completed":
        raise HTTPException(400, f"Render status: {render.status.value}")

    storage = get_storage()
    local_path = storage.get_local_path(render.output_url)
    return FileResponse(
        local_path,
        media_type="video/mp4",
        filename=f"tiktok_{render.project_id}_{render.id}.mp4",
    )


@router.get("/{render_id}/thumbnail")
async def download_thumbnail(render_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Download the render thumbnail."""
    render = await db.get(Render, render_id)
    if not render:
        raise HTTPException(404, "Render not found")
    if not render.thumbnail_url:
        raise HTTPException(400, "No thumbnail available")

    storage = get_storage()
    local_path = storage.get_local_path(render.thumbnail_url)
    return FileResponse(local_path, media_type="image/jpeg")
