"""
EditTimeline schema — single source of truth for the edit contract.

Every component (ReferenceAnalyzer → EditPlanner → RenderEngine → RevisionEngine)
passes data through these Pydantic models so validation happens at the boundary,
not scattered through render code.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


# ── Motion ──────────────────────────────────────────────────────────────────

class MotionKeyframe(BaseModel):
    t: float                        # seconds from clip start
    scale: float = 1.0              # zoom factor (1.0 = no zoom)
    x: float = 0.0                  # pan offset X (-1..1 relative to frame)
    y: float = 0.0                  # pan offset Y (-1..1 relative to frame)
    rotation: float = 0.0           # degrees


# ── Captions ─────────────────────────────────────────────────────────────────

class CaptionEvent(BaseModel):
    text: str
    start: float
    end: float
    position: Literal["top", "center", "bottom"] = "center"
    animation: Literal["none", "fade", "pop", "slide_up", "typewriter"] = "pop"
    font_size: int = 72
    case: Literal["uppercase", "titlecase", "asis"] = "uppercase"  # legacy compat
    stroke: bool = True                  # legacy compat — prefer stroke_width
    tracking: float = 1.0
    safe_zone: float = 0.0               # extra margin from screen edge (pixels)

    # ── Advanced caption styling ─────────────────────────────────────────
    font_family: str = "Arial Black"
    font_weight: Literal["normal", "bold", "black"] = "bold"
    text_case: Literal["uppercase", "titlecase", "lowercase", "asis"] = "uppercase"
    stroke_width: float = 3.0
    shadow: bool = False
    animation_duration: int = 200        # ms; enter-animation duration
    emphasis_words: List[str] = []       # words to render in a highlighted style

    # ── Reference-matched visual style ───────────────────────────────────
    text_color: str = "white"            # white / yellow / black / other hex
    background_box: bool = False         # draw a filled box behind the text
    background_color: str = "black"      # box color (name or hex without #)
    background_opacity: float = 0.6      # 0.0–1.0; only used when background_box=True
    y_position_percent: int = 75         # 0=top 50=center 75=lower-center 90=bottom

    @model_validator(mode="after")
    def _check_times(self) -> "CaptionEvent":
        if self.start >= self.end:
            raise ValueError(f"Caption start ({self.start}) must be < end ({self.end})")
        return self


# ── Transitions ──────────────────────────────────────────────────────────────

class TransitionEvent(BaseModel):
    type: str = "hard_cut"
    duration: float = 0.0           # seconds; 0 = instantaneous cut


# ── Clips ────────────────────────────────────────────────────────────────────

class ClipEvent(BaseModel):
    asset_id: str
    source_path: str = ""           # filled in at render time
    source_in: float = 0.0          # seconds into source file
    source_out: float = 0.0         # seconds into source file
    timeline_in: float = 0.0        # position in output timeline
    timeline_out: float = 0.0       # position in output timeline
    speed: float = 1.0
    crop_mode: Literal["center", "face_track", "manual"] = "center"
    motion_keyframes: List[MotionKeyframe] = []
    transition_in: Optional[TransitionEvent] = None
    transition_out: Optional[TransitionEvent] = None
    selection_metadata: Optional[Dict[str, Any]] = None  # debug: why this clip was chosen

    # ── Motion polish ───────────────────────────────────────────────────
    zoom_keyframes: List[Dict[str, float]] = []   # [{"t": 0.0, "scale": 1.0}, ...]
    crop_anchor: Literal["center", "top", "bottom", "left", "right"] = "center"
    motion_easing: Literal["linear", "ease_in", "ease_out", "ease_in_out"] = "linear"
    speed_ramp: List[Dict[str, float]] = []       # [{"t": 0.0, "speed": 1.0}, ...]

    @model_validator(mode="after")
    def _check_times(self) -> "ClipEvent":
        if self.source_out > 0 and self.source_in >= self.source_out:
            raise ValueError(
                f"Clip {self.asset_id}: source_in ({self.source_in}) >= source_out ({self.source_out})"
            )
        if self.timeline_out > 0 and self.timeline_in >= self.timeline_out:
            raise ValueError(
                f"Clip {self.asset_id}: timeline_in ({self.timeline_in}) >= timeline_out ({self.timeline_out})"
            )
        return self


# ── Color ────────────────────────────────────────────────────────────────────

class ColorGrade(BaseModel):
    brightness: float = 0.0         # -1..1 offset
    contrast: float = 1.0
    saturation: float = 1.0
    gamma: float = 1.0
    luma_avg: float = 128.0
    temperature: str = "neutral"    # cool / neutral / warm
    black_level: str = "normal"     # normal / crushed / lifted


# ── Timeline ─────────────────────────────────────────────────────────────────

class EditTimeline(BaseModel):
    project_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    version: int = 1
    duration_sec: float = 0.0
    width: int = 1080
    height: int = 1920
    fps: int = 30
    clips: List[ClipEvent] = []
    captions: List[CaptionEvent] = []
    music_path: Optional[str] = None
    color_grade: ColorGrade = Field(default_factory=ColorGrade)
    render_style: Optional[Dict[str, Any]] = None  # set by StyleRenderer.from_fingerprint()

    # ── Audio mode ───────────────────────────────────────────────────────
    # reference_audio : use audio extracted from the reference video
    # uploaded_audio  : use an externally uploaded music file (music_path)
    # original_audio  : keep the raw footage audio as-is; no music mix
    # silent          : render with no audio track at all
    audio_mode: Literal[
        "reference_audio", "uploaded_audio", "original_audio", "silent"
    ] = "reference_audio"

    audio_mix_settings: Dict[str, Any] = Field(
        default_factory=lambda: {
            "music_volume":          -18.0,   # dB gain applied to background music
            "original_audio_volume": 0.0,     # dB gain on footage audio
            "duck_under_speech":     True,     # sidechain-compress music under speech
            "fade_in_sec":           1.0,
            "fade_out_sec":          2.0,
        }
    )

    # ── Pacing metadata (computed by EditPlanner) ─────────────────────────
    pacing_metadata: Optional[Dict[str, Any]] = None

    # ── Validation ───────────────────────────────────────────────────────

    def validate_timeline(self) -> list[str]:
        """Return list of human-readable errors. Empty list = valid."""
        errors: list[str] = []

        if not self.clips:
            errors.append("Timeline has no clips")
            return errors

        for i, clip in enumerate(self.clips):
            if not clip.asset_id:
                errors.append(f"Clip {i}: missing asset_id")
            if clip.source_out > 0 and clip.source_in >= clip.source_out:
                errors.append(
                    f"Clip {i} ({clip.asset_id}): source_in ({clip.source_in:.3f}) >= source_out ({clip.source_out:.3f})"
                )
            if clip.timeline_out > 0 and clip.timeline_in >= clip.timeline_out:
                errors.append(
                    f"Clip {i} ({clip.asset_id}): timeline_in ({clip.timeline_in:.3f}) >= timeline_out ({clip.timeline_out:.3f})"
                )

        # No unintentional overlaps
        ordered = sorted(self.clips, key=lambda c: c.timeline_in)
        for i in range(len(ordered) - 1):
            a, b = ordered[i], ordered[i + 1]
            if b.timeline_in < a.timeline_out - 0.02:
                errors.append(
                    f"Overlap: {a.asset_id} ends {a.timeline_out:.3f}s, "
                    f"{b.asset_id} starts {b.timeline_in:.3f}s"
                )

        # duration_sec must match last clip's timeline_out (within 0.1s tolerance)
        last_clip_end = self.clips[-1].timeline_out
        if abs(self.duration_sec - last_clip_end) > 0.1:
            errors.append(
                f"duration_sec ({self.duration_sec:.3f}) does not match "
                f"last clip end ({last_clip_end:.3f})"
            )

        total = self.duration_sec
        for cap in self.captions:
            if cap.end > total + 0.1:
                errors.append(
                    f"Caption '{cap.text[:20]}' ends at {cap.end:.2f}s "
                    f"but video is only {total:.2f}s"
                )

        return errors

    # ── Serialisation helpers ────────────────────────────────────────────

    def to_render_spec(self, asset_resolver=None) -> dict[str, Any]:
        """Convert to the flat dict expected by RenderEngine._render_impl()."""
        resolve = asset_resolver or (lambda aid: aid)

        video_track: list[dict[str, Any]] = []
        for clip in self.clips:
            # Derive simple motion dict from keyframes (first keyframe wins)
            if clip.motion_keyframes:
                kf = clip.motion_keyframes[0]
                if kf.scale > 1.0:
                    motion: dict[str, Any] = {"type": "zoom_in", "strength": round(kf.scale - 1.0, 3)}
                elif kf.scale < 1.0:
                    motion = {"type": "zoom_out", "strength": round(1.0 - kf.scale, 3)}
                else:
                    motion = {"type": "static", "strength": 0.0}
            else:
                motion = {"type": "slow_push", "strength": 0.04}

            trans_out: dict[str, Any] = {}
            if clip.transition_out:
                trans_out = {"type": clip.transition_out.type, "duration": clip.transition_out.duration}

            video_track.append({
                "asset_id": clip.asset_id,
                "source_path": clip.source_path or resolve(clip.asset_id),
                "source_in": clip.source_in,
                "source_out": clip.source_out,
                "start": clip.timeline_in,
                "end": clip.timeline_out,
                "speed": clip.speed,
                "motion": motion,
                "transition_out": trans_out,
                "crop_anchor": clip.crop_anchor,
                "motion_easing": clip.motion_easing,
                "zoom_keyframes": clip.zoom_keyframes,
                "speed_ramp": clip.speed_ramp,
            })

        text_track: list[dict[str, Any]] = []
        for cap in self.captions:
            # text_case takes priority over legacy `case`
            tc = cap.text_case
            if tc == "uppercase":
                rendered_text = cap.text.upper()
            elif tc == "lowercase":
                rendered_text = cap.text.lower()
            elif tc == "titlecase":
                rendered_text = cap.text.title()
            else:  # asis — still honour legacy `case` for old callers
                rendered_text = cap.text.upper() if cap.case == "uppercase" else cap.text
            text_track.append({
                "start": cap.start,
                "end": cap.end,
                "text": rendered_text,
                "position": "lower_third" if cap.position == "bottom" else cap.position,
                "animation": cap.animation,
                "safe_zone": cap.safe_zone,
                # Advanced styling — consumed by generate_ass_subtitles
                "font_family": cap.font_family,
                "font_size": cap.font_size,
                "font_weight": cap.font_weight,
                "text_case": cap.text_case,
                "stroke_width": cap.stroke_width,
                "shadow": cap.shadow,
                "tracking": cap.tracking,
                "animation_duration": cap.animation_duration,
                "emphasis_words": cap.emphasis_words,
            })

        return {
            "project_id": self.project_id,
            "output": {"width": self.width, "height": self.height, "fps": self.fps},
            "tracks": {"video": video_track, "text": text_track},
            # Audio settings consumed by RenderEngine._render_impl
            "audio_mode":        self.audio_mode,
            "music_path":        self.music_path,
            "audio_mix_settings": self.audio_mix_settings,
        }

    @classmethod
    def from_render_spec(cls, spec: dict[str, Any], **kwargs) -> "EditTimeline":
        """Reconstruct from the legacy dict format (reverse of to_render_spec)."""
        out = spec.get("output", {})
        clips: list[ClipEvent] = []
        captions: list[CaptionEvent] = []
        timeline_pos = 0.0

        for vc in spec.get("tracks", {}).get("video", []):
            dur = vc.get("end", 0) - vc.get("start", 0)
            t_out = vc.get("transition_out", {})
            clips.append(ClipEvent(
                asset_id=vc["asset_id"],
                source_path=vc.get("source_path", ""),
                source_in=vc.get("source_in", 0.0),
                source_out=vc.get("source_out", vc.get("source_in", 0) + dur),
                timeline_in=vc.get("start", timeline_pos),
                timeline_out=vc.get("end", timeline_pos + dur),
                speed=vc.get("speed", 1.0),
                transition_out=TransitionEvent(**t_out) if t_out else None,
            ))
            timeline_pos = vc.get("end", timeline_pos + dur)

        for tc in spec.get("tracks", {}).get("text", []):
            pos = "bottom" if tc.get("position") == "lower_third" else tc.get("position", "center")
            captions.append(CaptionEvent(
                text=tc["text"],
                start=tc["start"],
                end=tc["end"],
                position=pos,
                animation=tc.get("animation", "pop"),
                font_family=tc.get("font_family", "Arial Black"),
                font_size=tc.get("font_size", 72),
                font_weight=tc.get("font_weight", "bold"),
                text_case=tc.get("text_case", "uppercase"),
                stroke_width=tc.get("stroke_width", 3.0),
                shadow=tc.get("shadow", False),
                tracking=tc.get("tracking", 1.0),
                animation_duration=tc.get("animation_duration", 200),
                emphasis_words=tc.get("emphasis_words", []),
            ))

        duration = clips[-1].timeline_out if clips else 0.0
        return cls(
            duration_sec=round(duration, 3),
            width=out.get("width", 1080),
            height=out.get("height", 1920),
            fps=out.get("fps", 30),
            clips=clips,
            captions=captions,
            **kwargs,
        )
