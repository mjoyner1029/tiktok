"""Style Presets API — Save and reuse style profiles."""

from __future__ import annotations

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.db import User
from app.services.style_presets import StylePresetService

router = APIRouter(prefix="/presets", tags=["presets"])


# ── Schemas ──────────────────────────────────────────────────────────────────


class SavePresetRequest(BaseModel):
    """Request to save a new style preset."""
    
    name: str = Field(..., max_length=100)
    description: str = Field("", max_length=500)
    style_profile: dict = Field(..., description="Style extraction result")
    is_public: bool = Field(False, description="Share with community")


class PresetResponse(BaseModel):
    """Style preset information."""
    
    id: uuid.UUID
    name: str
    description: str
    style_profile: dict
    is_builtin: bool
    is_public: bool
    created_at: str
    
    class Config:
        from_attributes = True


class ApplyPresetRequest(BaseModel):
    """Request to apply a preset to a project."""
    
    preset_id: uuid.UUID
    project_id: uuid.UUID


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.post("", response_model=PresetResponse, status_code=status.HTTP_201_CREATED)
async def save_preset(
    req: SavePresetRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save a style profile as a reusable preset."""
    preset = await StylePresetService.save_preset(
        user_id=current_user.id,
        name=req.name,
        style_profile=req.style_profile,
        description=req.description,
        db=db,
    )
    
    return PresetResponse(
        id=preset.id,
        name=preset.name,
        description=preset.profile_json.get("description", ""),
        style_profile=preset.profile_json,
        is_builtin=preset.profile_json.get("is_builtin", False),
        is_public=preset.profile_json.get("is_public", False),
        created_at=preset.created_at.isoformat(),
    )


@router.get("", response_model=List[PresetResponse])
async def list_presets(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all available presets (built-in + user's personal)."""
    presets = await StylePresetService.list_presets(
        user_id=current_user.id,
        db=db,
    )
    
    return [
        PresetResponse(
            id=p.id,
            name=p.name,
            description=p.profile_json.get("description", ""),
            style_profile=p.profile_json,
            is_builtin=p.profile_json.get("is_builtin", False),
            is_public=p.profile_json.get("is_public", False),
            created_at=p.created_at.isoformat(),
        )
        for p in presets
    ]


@router.get("/{preset_id}", response_model=PresetResponse)
async def get_preset(
    preset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific preset by ID."""
    preset = await StylePresetService.get_preset(preset_id, db)
    
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")
    
    return PresetResponse(
        id=preset.id,
        name=preset.name,
        description=preset.profile_json.get("description", ""),
        style_profile=preset.profile_json,
        is_builtin=preset.profile_json.get("is_builtin", False),
        is_public=preset.profile_json.get("is_public", False),
        created_at=preset.created_at.isoformat(),
    )


@router.post("/apply", status_code=status.HTTP_201_CREATED)
async def apply_preset(
    req: ApplyPresetRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Apply a saved preset to a project."""
    try:
        style = await StylePresetService.apply_preset(
            preset_id=req.preset_id,
            project_id=req.project_id,
            db=db,
        )
        
        return {
            "message": "Preset applied successfully",
            "style_id": str(style.id),
        }
    
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.delete("/{preset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_preset(
    preset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a user's custom preset."""
    try:
        await StylePresetService.delete_preset(preset_id, current_user.id, db)
    
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
