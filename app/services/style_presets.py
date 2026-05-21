"""Style presets management - saved style profiles for reuse.

Allows users to save successful style extractions and apply them to new projects.
"""

from __future__ import annotations

import logging
import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import cached, delete_cached, cache_key
from app.models.db import StyleProfile, User, Workspace

logger = logging.getLogger(__name__)


class StylePresetService:
    """Manage saved style presets for reuse across projects."""
    
    @staticmethod
    async def save_preset(
        user_id: uuid.UUID,
        name: str,
        style_profile: dict,
        description: str = "",
        db: AsyncSession = None,
    ) -> StyleProfile:
        """Save a style profile as a reusable preset."""
        # Create a preset (stored as StyleProfile with no project association)
        preset = StyleProfile(
            project_id=None,  # Global preset
            name=name,
            profile_json={
                **style_profile,
                "description": description,
                "is_preset": True,
                "created_by": str(user_id),
            },
            model_name="preset",
        )
        
        db.add(preset)
        await db.commit()
        await db.refresh(preset)
        
        logger.info(f"Saved style preset: {name}")
        return preset
    
    @staticmethod
    @cached(ttl=300, key_prefix="presets")
    async def list_presets(
        user_id: Optional[uuid.UUID] = None,
        db: AsyncSession = None,
    ) -> List[StyleProfile]:
        """List all available style presets.
        
        Returns global presets + user's personal presets.
        """
        query = select(StyleProfile).where(StyleProfile.project_id.is_(None))
        
        if user_id:
            # Filter to user's presets or public ones
            query = query.where(
                (StyleProfile.profile_json["created_by"].astext == str(user_id))
                | (StyleProfile.profile_json["is_public"].astext == "true")
            )
        
        result = await db.execute(query.order_by(StyleProfile.created_at.desc()))
        return result.scalars().all()
    
    @staticmethod
    async def get_preset(preset_id: uuid.UUID, db: AsyncSession) -> Optional[StyleProfile]:
        """Get a specific preset by ID."""
        return await db.get(StyleProfile, preset_id)
    
    @staticmethod
    async def apply_preset(
        preset_id: uuid.UUID,
        project_id: uuid.UUID,
        db: AsyncSession,
    ) -> StyleProfile:
        """Apply a saved preset to a project (creates a copy)."""
        preset = await db.get(StyleProfile, preset_id)
        if not preset:
            raise ValueError(f"Preset {preset_id} not found")
        
        # Create a project-specific copy
        project_style = StyleProfile(
            project_id=project_id,
            name=f"{preset.name} (applied)",
            profile_json={
                **preset.profile_json,
                "preset_id": str(preset_id),
            },
            model_name=preset.model_name,
        )
        
        db.add(project_style)
        await db.commit()
        await db.refresh(project_style)
        
        # Invalidate cache
        await delete_cached(cache_key("presets", str(project_id)))
        
        logger.info(f"Applied preset {preset.name} to project {project_id}")
        return project_style
    
    @staticmethod
    async def delete_preset(preset_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession):
        """Delete a preset (only if owned by user)."""
        preset = await db.get(StyleProfile, preset_id)
        if not preset:
            raise ValueError(f"Preset {preset_id} not found")
        
        # Check ownership
        created_by = preset.profile_json.get("created_by")
        if created_by != str(user_id):
            raise PermissionError("Cannot delete preset created by another user")
        
        await db.delete(preset)
        await db.commit()
        
        # Invalidate cache
        await delete_cached(cache_key("presets", str(user_id)))
        
        logger.info(f"Deleted preset: {preset.name}")


# ── Built-in Presets ─────────────────────────────────────────────────────────


BUILTIN_PRESETS = {
    "viral_educational": {
        "name": "Viral Educational",
        "description": "Fast-paced educational content with strong hooks",
        "hook_style": "curiosity",
        "avg_cut_duration_sec": 1.2,
        "caption_style": "bold, all caps, 2-3 words max",
        "caption_position": "lower_third",
        "caption_max_words": 3,
        "zoom_pattern": "zoom in on key terms, shake on numbers",
        "structure": "shocking hook → problem → evidence → solution → CTA",
        "tone": "educational",
        "energy_curve": "high_start → sustain → peak_end",
        "ideal_duration_sec": 25.0,
        "music_style": "upbeat electronic",
    },
    "storytelling_viral": {
        "name": "Storytelling Viral",
        "description": "Narrative-driven content with emotional hooks",
        "hook_style": "storytelling",
        "avg_cut_duration_sec": 2.0,
        "caption_style": "sentence case, dramatic pauses",
        "caption_position": "center",
        "caption_max_words": 5,
        "zoom_pattern": "slow push on emotional moments",
        "structure": "hook → setup → conflict → climax → resolution",
        "tone": "inspirational",
        "energy_curve": "intrigue → tension → release",
        "ideal_duration_sec": 45.0,
        "music_style": "cinematic ambient",
    },
    "meme_comedy": {
        "name": "Meme/Comedy",
        "description": "Fast, punchy, meme-format content",
        "hook_style": "shock",
        "avg_cut_duration_sec": 0.8,
        "caption_style": "impact font style, all caps, meme text",
        "caption_position": "top",
        "caption_max_words": 4,
        "zoom_pattern": "rapid zoom, shake, distortion",
        "structure": "setup → punchline → repeat",
        "tone": "casual",
        "energy_curve": "high_constant",
        "ideal_duration_sec": 15.0,
        "music_style": "trending audio / meme sound",
    },
    "product_showcase": {
        "name": "Product Showcase",
        "description": "Clean product demos with clear CTAs",
        "hook_style": "question",
        "avg_cut_duration_sec": 2.5,
        "caption_style": "clean, lowercase, minimal",
        "caption_position": "lower_third",
        "caption_max_words": 4,
        "zoom_pattern": "slow push, detail close-ups",
        "structure": "problem → product intro → features → benefits → CTA",
        "tone": "professional",
        "energy_curve": "steady_build",
        "ideal_duration_sec": 30.0,
        "music_style": "modern minimal",
    },
}


async def seed_builtin_presets(db: AsyncSession):
    """Seed database with built-in style presets (run once on deployment)."""
    for preset_id, data in BUILTIN_PRESETS.items():
        # Check if already exists
        result = await db.execute(
            select(StyleProfile).where(
                StyleProfile.name == data["name"],
                StyleProfile.profile_json["is_preset"].astext == "true",
            )
        )
        existing = result.scalar_one_or_none()
        
        if not existing:
            preset = StyleProfile(
                project_id=None,
                name=data["name"],
                profile_json={
                    **data,
                    "is_preset": True,
                    "is_builtin": True,
                    "is_public": True,
                    "preset_id": preset_id,
                },
                model_name="builtin",
            )
            db.add(preset)
    
    await db.commit()
    logger.info("Seeded built-in style presets")
