"""Pydantic schemas for API request/response validation and the render contract."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════
#  REQUEST SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════

class ProjectCreate(BaseModel):
    title: str = "Untitled"
    goal: str = ""
    target_platform: str = "tiktok"


class ProjectUpdate(BaseModel):
    title: Optional[str] = None
    goal: Optional[str] = None


class RevisionRequest(BaseModel):
    feedback: str = Field(..., min_length=1, description="Natural-language revision note")


# ═══════════════════════════════════════════════════════════════════════════
#  RESPONSE SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════

class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: Optional[str]
    plan: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AssetOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    type: str
    filename: str
    storage_url: str
    duration_sec: Optional[float]
    width: Optional[int]
    height: Optional[int]
    transcript_status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class StyleProfileOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: Optional[str]
    profile_json: dict
    model_name: str
    created_at: datetime

    model_config = {"from_attributes": True}


class EditSpecOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    version: int
    spec_json: dict
    source: str
    revision_note: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class RenderOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    edit_spec_id: uuid.UUID
    status: str
    output_url: Optional[str]
    preview_url: Optional[str]
    thumbnail_url: Optional[str]
    duration_sec: Optional[float]
    error_message: Optional[str]
    created_at: datetime
    finished_at: Optional[datetime]

    model_config = {"from_attributes": True}


class ProjectOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    title: str
    status: str
    target_platform: str
    goal: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class JobOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    type: str
    status: str
    error_message: Optional[str]
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════════════════════════════════
#  RENDER CONTRACT — the strict JSON spec Claude must output
# ═══════════════════════════════════════════════════════════════════════════

class MotionInstruction(BaseModel):
    type: Literal[
        "zoom_in", "zoom_out", "slow_push", "slow_pull",
        "shake", "pan_left", "pan_right", "static"
    ] = "static"
    strength: float = Field(0.05, ge=0.0, le=0.5, description="Motion intensity 0→0.5")


class VideoTrackClip(BaseModel):
    asset_id: str
    start: float = Field(..., ge=0, description="Timeline start in seconds")
    end: float = Field(..., ge=0, description="Timeline end in seconds")
    source_in: float = Field(0.0, ge=0, description="Source clip start")
    source_out: float = Field(0.0, ge=0, description="Source clip end")
    crop: Literal["smart_center", "face_track", "manual", "none"] = "smart_center"
    motion: MotionInstruction = Field(default_factory=MotionInstruction)
    speed: float = Field(1.0, gt=0, le=4.0, description="Playback speed multiplier")


class TextTrackClip(BaseModel):
    start: float = Field(..., ge=0)
    end: float = Field(..., ge=0)
    text: str
    style: str = "bold_kinetic_1"
    position: Literal[
        "center", "lower_third", "upper_third", "top", "bottom"
    ] = "lower_third"
    font_size: int = Field(64, ge=12, le=200)
    color: str = "#FFFFFF"
    background_color: Optional[str] = "#00000088"
    animation: Literal[
        "none", "pop", "typewriter", "slide_up", "fade"
    ] = "pop"


class AudioTrackClip(BaseModel):
    asset_id: str
    start: float = Field(..., ge=0)
    end: float = Field(..., ge=0)
    source_in: float = 0.0
    source_out: Optional[float] = None
    gain_db: float = Field(0.0, ge=-60, le=20)
    fade_in_sec: float = 0.0
    fade_out_sec: float = 0.0
    duck_under_speech: bool = True


class OutputSpec(BaseModel):
    width: int = 1080
    height: int = 1920
    fps: int = 30
    duration_sec: float = 0.0
    video_bitrate: str = "8M"
    audio_bitrate: str = "192k"


class RenderContract(BaseModel):
    """The strict JSON edit spec that Claude produces and FFmpeg consumes."""
    project_id: str
    output: OutputSpec = Field(default_factory=OutputSpec)
    tracks: dict = Field(default_factory=lambda: {
        "video": [],
        "text": [],
        "audio": [],
    })

    model_config = {"json_schema_extra": {
        "example": {
            "project_id": "proj_123",
            "output": {"width": 1080, "height": 1920, "fps": 30, "duration_sec": 27.4},
            "tracks": {
                "video": [{
                    "asset_id": "clip_1", "start": 0.0, "end": 2.1,
                    "source_in": 14.3, "source_out": 16.4,
                    "crop": "smart_center",
                    "motion": {"type": "zoom_in", "strength": 0.08},
                }],
                "text": [{
                    "start": 0.2, "end": 1.1,
                    "text": "THIS IS WHY",
                    "style": "bold_kinetic_1",
                    "position": "lower_third",
                }],
                "audio": [{
                    "asset_id": "music_1",
                    "start": 0.0, "end": 27.4,
                    "gain_db": -18,
                }],
            },
        }
    }}


# ═══════════════════════════════════════════════════════════════════════════
#  STYLE PROFILE SCHEMA
# ═══════════════════════════════════════════════════════════════════════════

class StyleProfileData(BaseModel):
    hook_style: str = ""
    avg_cut_duration_sec: float = 1.5
    caption_style: str = ""
    caption_position: str = "lower_third"
    caption_max_words: int = 4
    zoom_pattern: str = ""
    structure: str = ""
    tone: str = ""
    energy_curve: str = ""  # e.g. "high_start → dip → peak_end"
    ideal_duration_sec: float = 30.0
    music_style: str = ""
