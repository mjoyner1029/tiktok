"""
ReferenceAnalyzer — extracts a measurable edit fingerprint from a reference video.

Three analysis layers:
  1. FFmpeg/ffprobe (deterministic):  cut timestamps, shot durations, color grade
  2. Audio analysis (optional):       beat points, tempo, downbeats via librosa
  3. Claude Vision (qualitative):     caption style, hook, transition type, energy

Output format:
{
    "duration_sec": 21.4,
    "aspect_ratio": "9:16",
    "cut_points": [0.0, 0.72, 1.41, 2.03],
    "shot_durations": [0.72, 0.69, 0.62],
    "avg_shot_duration": 0.68,
    "num_cuts": 14,
    "pace": "fast",                     # slow / medium / fast / ultra-fast
    "beat_points": [0.31, 0.92, 1.54],
    "tempo_bpm": 92,
    "downbeats": [0.0, 0.92, 1.85],
    "cut_to_beat_alignment": 0.82,
    "transitions": ["hard_cut", "flash_cut"],
    "dominant_transition": "hard_cut",
    "motion_style": {
        "primary": "slow_push",
        "zoom_events": []
    },
    "caption_style": {
        "uses_text": true,
        "position": "center",
        "case": "uppercase",
        "words_per_caption": 2,
        "animation": "pop",
        "font_size_class": "large",
        "has_stroke": true,
        "all_caps": true,
        "max_words": 4
    },
    "color_profile": {
        "brightness": 0.05,
        "contrast": 1.1,
        "saturation": 1.2,
        "gamma": 0.9,
        "luma_avg": 128.0,
        "temperature": "cool",
        "black_level": "normal"
    },
    "hook_style": "bold statement",
    "energy_level": "high",
    "tone": "aspirational and bold",
    "ranking_profile": "fashion_montage",   # inferred: talking_head | vlog | fashion_montage |
                                             #           travel_reel | product_showcase | music_video
    "faces_central": false,                  # bool — faces/people are the primary subject
    "subject_focus": "person",               # presenter | person | scene | product | artist
    "content_type": "fashion",               # talking_head | vlog | fashion | travel |
                                             #   product_review | music_video | general
    "style_tags": ["fast_cuts", "cinematic", "aesthetic"]  # descriptive tags
}
"""
from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from app.services.media_analyzer import extract_visual_style, get_media_info

logger = logging.getLogger(__name__)

# Optional heavy deps — graceful degradation
try:
    import librosa as _librosa
    import numpy as _np
    _HAS_LIBROSA = True
except ImportError:
    _HAS_LIBROSA = False

_FFMPEG = (
    "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
    if Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg").exists()
    else "ffmpeg"
)
_FFPROBE = (
    "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe"
    if Path("/opt/homebrew/opt/ffmpeg-full/bin/ffprobe").exists()
    else "ffprobe"
)

_VISION_PROMPT = """\
You are analyzing frames from a TikTok video sampled at detected cut points.

Cut timestamps: {cut_timestamps}s  |  Duration: {duration}s  |  Avg shot: {avg_shot}s

Analyze the EDITING STYLE from these frames and return ONLY valid JSON.
For caption_style, look carefully at ANY on-screen text and describe its exact visual appearance:

{{
    "caption_style": {{
        "uses_text": true,
        "position": "center",
        "y_position_percent": 75,
        "case": "uppercase",
        "words_per_caption": 3,
        "animation": "pop",
        "font_size_class": "large",
        "font_family": "Impact",
        "has_stroke": true,
        "stroke_color": "black",
        "all_caps": true,
        "max_words": 4,
        "text_color": "white",
        "background_box": false,
        "background_color": "black",
        "background_opacity": 0.6
    }},
    "hook_style": "bold statement question over B-roll",
    "transitions": ["hard_cut", "flash_cut"],
    "dominant_transition": "hard_cut",
    "motion_style": {{
        "primary": "slow_push"
    }},
    "energy_level": "high",
    "tone": "aspirational and bold"
}}

Pick caption position from: top / center / bottom
Pick y_position_percent: 0 = very top, 50 = center, 75 = lower-center (most common for TikTok), 90 = near bottom
Pick animation from: pop / slide_up / fade / none
Pick font_family from: Impact / Arial-Black / bold-sans / serif / handwritten / unknown
Pick dominant_transition from: hard_cut / flash_cut / whip_pan_left / whip_pan_right / dissolve / fade / zoom_transition
Pick motion primary from: static / slow_push / zoom_in / zoom_out / shake
Pick energy_level from: low / medium / high
"""


def _extract_frames(video_path: str, output_dir: str, timestamps: list[float], n: int = 8) -> list[str]:
    """Extract JPG frames at given timestamps (or evenly spaced if none)."""
    info = get_media_info(video_path)
    dur = info["duration_sec"]
    sample_ts = [t for t in timestamps if 0 <= t <= dur][:n] if timestamps else [
        dur * (i + 0.5) / n for i in range(n)
    ]
    frames: list[str] = []
    for i, ts in enumerate(sample_ts):
        out = f"{output_dir}/frame_{i:03d}.jpg"
        r = subprocess.run(
            [_FFMPEG, "-y", "-ss", f"{ts:.3f}", "-i", video_path,
             "-vframes", "1", "-vf", "scale=540:-2", "-q:v", "5", out],
            capture_output=True,
        )
        if r.returncode == 0 and Path(out).exists():
            frames.append(out)
    return frames


def _detect_beats(video_path: str) -> dict[str, Any]:
    """Extract full music metadata via MusicAnalyzer.

    Returns a dict with beat_grid, beat_points (alias), downbeats,
    phrase_boundaries, energy_curve, intensity_curve, transient_peaks,
    tempo_bpm.  Returns empty dict when librosa is unavailable or audio
    extraction fails.
    """
    try:
        from app.services.music_analysis import MusicAnalyzer
        result = MusicAnalyzer().analyze_safe(video_path)
        if not result:
            return {}
        # Expose beat_points as an alias for backward compatibility
        beat_grid = result.get("beat_grid", [])
        result.setdefault("beat_points", beat_grid)
        return result
    except Exception as exc:
        logger.debug("Beat detection failed: %s", exc)
        return {}


def _pace_label(avg_shot: float) -> str:
    if avg_shot < 0.5:
        return "ultra-fast"
    if avg_shot < 1.2:
        return "fast"
    if avg_shot < 2.5:
        return "medium"
    return "slow"


def _beat_alignment(cut_points: list[float], beat_points: list[float], tolerance: float = 0.15) -> float:
    """Fraction of cuts that land within tolerance of a beat."""
    if not cut_points or not beat_points:
        return 0.0
    aligned = sum(
        1 for c in cut_points
        if any(abs(c - b) <= tolerance for b in beat_points)
    )
    return round(aligned / len(cut_points), 3)


# ── Style profile inference ────────────────────────────────────────────────

def _score_profile(fp: dict[str, Any]) -> dict[str, int]:
    """Return a confidence score for each of the 6 ranking profiles.

    Higher score = stronger signal for that profile.  Scores are additive;
    the caller picks the highest-scoring profile.

    Input signals used (all optional — gracefully ignored when absent):
      avg_shot_duration, pace, cut_to_beat_alignment, beat_points, tempo_bpm,
      motion_style.primary, energy_level, face_density, speech_density,
      scene_tags (list[str]).
    """
    avg_shot   = fp.get("avg_shot_duration", 1.5)
    alignment  = fp.get("cut_to_beat_alignment", 0.0)
    tempo      = fp.get("tempo_bpm") or 0
    beat_pts   = fp.get("beat_points") or []
    motion     = (fp.get("motion_style") or {}).get("primary", "slow_push")
    energy     = fp.get("energy_level", "medium")
    pace       = fp.get("pace", "medium")

    # Optional detection signals — None when not measured
    face_density   = fp.get("face_density")    # float 0-1 | None
    speech_density = fp.get("speech_density")  # float 0-1 | None
    scene_tags     = {str(t).lower() for t in (fp.get("scene_tags") or [])}

    scores: dict[str, int] = {
        "talking_head":    0,
        "vlog":            0,
        "fashion_montage": 0,
        "travel_reel":     0,
        "product_showcase": 0,
        "music_video":     0,
    }

    # ── music_video ────────────────────────────────────────────────────────
    if alignment > 0.5 and len(beat_pts) > 4:
        scores["music_video"] += 5
    elif alignment > 0.3 and len(beat_pts) > 2:
        scores["music_video"] += 2
    if tempo > 100:
        scores["music_video"] += 2
    if energy == "high":
        scores["music_video"] += 1
    if pace in ("fast", "ultra-fast"):
        scores["music_video"] += 1
    if scene_tags & {"music", "concert", "dancing", "dance", "performance", "artist"}:
        scores["music_video"] += 4

    # ── talking_head ────────────────────────────────────────────────────────
    if face_density is not None:
        if face_density > 0.6:
            scores["talking_head"] += 4
        elif face_density > 0.4:
            scores["talking_head"] += 2
    if speech_density is not None:
        if speech_density > 0.5:
            scores["talking_head"] += 3
        elif speech_density > 0.3:
            scores["talking_head"] += 1
    if avg_shot > 2.0:
        scores["talking_head"] += 2
    if motion == "static":
        scores["talking_head"] += 2
    if pace in ("slow", "medium"):
        scores["talking_head"] += 1
    if scene_tags & {"presenter", "interview", "talking", "speaker", "tutorial", "lecture"}:
        scores["talking_head"] += 4

    # ── vlog ────────────────────────────────────────────────────────────────
    if motion == "shake":
        scores["vlog"] += 4
    if face_density is not None and 0.3 <= face_density <= 0.7:
        scores["vlog"] += 2
    if speech_density is not None and speech_density > 0.2:
        scores["vlog"] += 1
    if pace == "medium":
        scores["vlog"] += 1
    if energy == "medium":
        scores["vlog"] += 1
    if scene_tags & {"vlog", "casual", "handheld", "daily", "lifestyle", "diary"}:
        scores["vlog"] += 4

    # ── fashion_montage ─────────────────────────────────────────────────────
    if scene_tags & {"fashion", "outfit", "clothing", "model", "style", "beauty", "aesthetic", "ootd"}:
        scores["fashion_montage"] += 5
    if face_density is not None and 0.2 <= face_density <= 0.7:
        scores["fashion_montage"] += 1
    if speech_density is not None and speech_density < 0.2:
        scores["fashion_montage"] += 2
    if motion in ("slow_push", "zoom_in"):
        scores["fashion_montage"] += 1
    if pace == "fast":
        scores["fashion_montage"] += 1
    if energy in ("medium", "high"):
        scores["fashion_montage"] += 1

    # ── travel_reel ─────────────────────────────────────────────────────────
    if scene_tags & {"travel", "landscape", "scenic", "outdoor", "nature", "city", "destination", "adventure"}:
        scores["travel_reel"] += 5
    if face_density is not None and face_density < 0.2:
        scores["travel_reel"] += 2
    if motion in ("slow_push", "zoom_in", "zoom_out"):
        scores["travel_reel"] += 1
    if pace in ("medium", "fast"):
        scores["travel_reel"] += 1
    if energy in ("medium", "high"):
        scores["travel_reel"] += 1

    # ── product_showcase ─────────────────────────────────────────────────────
    if scene_tags & {"product", "object", "item", "showcase", "closeup", "detail", "review", "unboxing"}:
        scores["product_showcase"] += 5
    if face_density is not None:
        if face_density < 0.15:
            scores["product_showcase"] += 3
        elif face_density < 0.3:
            scores["product_showcase"] += 1
    if pace == "slow":
        scores["product_showcase"] += 2
    elif pace == "medium":
        scores["product_showcase"] += 1
    if speech_density is not None and speech_density < 0.3:
        scores["product_showcase"] += 1
    if motion in ("static", "slow_push", "zoom_in"):
        scores["product_showcase"] += 1

    return scores


def infer_style_profile(fp: dict[str, Any]) -> dict[str, Any]:
    """Infer style profile metadata from a reference fingerprint.

    Returns a dict with five keys to be merged into the fingerprint:

    ``ranking_profile``  — one of: talking_head, vlog, fashion_montage,
                           travel_reel, product_showcase, music_video
    ``faces_central``    — bool: whether faces/people are the primary subject
    ``subject_focus``    — str: e.g. "presenter", "person", "scene", "product"
    ``content_type``     — str: e.g. "talking_head", "vlog", "travel", etc.
    ``style_tags``       — list[str]: descriptive tags for downstream use

    All inference is rule-based — no LLM call.  When signals are absent the
    function falls back to structural heuristics and returns sensible defaults.
    """
    scores  = _score_profile(fp)
    profile = max(scores, key=lambda k: (scores[k], -ord(k[0])))  # deterministic tie-break

    avg_shot       = fp.get("avg_shot_duration", 1.5)
    alignment      = fp.get("cut_to_beat_alignment", 0.0)
    face_density   = fp.get("face_density")
    speech_density = fp.get("speech_density")
    energy         = fp.get("energy_level", "medium")
    motion         = (fp.get("motion_style") or {}).get("primary", "slow_push")
    pace           = fp.get("pace", "medium")

    # ── faces_central ────────────────────────────────────────────────────
    if face_density is not None:
        faces_central: bool = face_density > 0.4
    else:
        faces_central = profile in ("talking_head", "vlog", "fashion_montage")

    # ── subject_focus ─────────────────────────────────────────────────────
    _SUBJECT_MAP: dict[str, str] = {
        "talking_head":    "presenter",
        "vlog":            "person",
        "fashion_montage": "person",
        "travel_reel":     "scene",
        "product_showcase": "product",
        "music_video":     "artist",
    }
    subject_focus = _SUBJECT_MAP.get(profile, "scene")

    # ── content_type ──────────────────────────────────────────────────────
    _CONTENT_MAP: dict[str, str] = {
        "talking_head":    "talking_head",
        "vlog":            "vlog",
        "fashion_montage": "fashion",
        "travel_reel":     "travel",
        "product_showcase": "product_review",
        "music_video":     "music_video",
    }
    content_type = _CONTENT_MAP.get(profile, "general")

    # ── style_tags ────────────────────────────────────────────────────────
    style_tags: list[str] = []
    if pace in ("fast", "ultra-fast"):
        style_tags.append("fast_cuts")
    elif pace == "slow":
        style_tags.append("slow_paced")
    if energy == "high":
        style_tags.append("high_energy")
    elif energy == "low":
        style_tags.append("calm")
    if alignment > 0.4:
        style_tags.append("beat_synced")
    if motion in ("slow_push", "zoom_in"):
        style_tags.append("cinematic")
    elif motion == "shake":
        style_tags.append("handheld")
    if faces_central:
        style_tags.append("face_centric")

    _PROFILE_TAGS: dict[str, str] = {
        "fashion_montage":  "aesthetic",
        "travel_reel":      "scenic",
        "product_showcase": "product_focus",
        "music_video":      "visual_beats",
    }
    if profile in _PROFILE_TAGS:
        style_tags.append(_PROFILE_TAGS[profile])

    # Stable deduplication
    seen: set[str] = set()
    style_tags = [t for t in style_tags if not (t in seen or seen.add(t))]  # type: ignore[func-returns-value]

    return {
        "ranking_profile": profile,
        "faces_central":   faces_central,
        "subject_focus":   subject_focus,
        "content_type":    content_type,
        "style_tags":      style_tags,
    }


class ReferenceAnalyzer:
    """Extract a measurable edit fingerprint from a local video file or URL."""

    def __init__(self, llm):
        self.llm = llm

    # ── Public API ─────────────────────────────────────────────────────────

    def analyze_urls(
        self,
        urls: list[str],
        embedding_service: Any | None = None,
    ) -> dict[str, Any]:
        """Download and analyze one or more reference URLs → merged fingerprint.

        Args:
            urls: HTTP(S) URLs to reference TikTok videos.
            embedding_service: Optional :class:`~app.services.embeddings.EmbeddingService`
                instance.  When provided, the CLIP reference embedding is computed
                while the downloaded video file is still on disk and stored in the
                fingerprint under ``_ref_embedding``.
        """
        fingerprints: list[dict[str, Any]] = []
        for url in (u for u in urls if u.strip().startswith("http")):
            try:
                fp = self._analyze_url(url, embedding_service=embedding_service)
                fingerprints.append(fp)
                logger.info(
                    "Fingerprint [%s…]: %d cuts, avg=%.2fs, pace=%s, bpm=%s",
                    url[-30:], fp["num_cuts"], fp["avg_shot_duration"],
                    fp["pace"], fp.get("tempo_bpm", "—"),
                )
            except Exception as exc:
                logger.warning("Reference analysis failed for %s: %s", url, exc)

        if not fingerprints:
            logger.warning("No references analyzed — using defaults")
            return self._default_fingerprint()
        return fingerprints[0] if len(fingerprints) == 1 else self._merge(fingerprints)

    def analyze_file(
        self,
        video_path: str,
        embedding_service: Any | None = None,
    ) -> dict[str, Any]:
        """Analyze a local video file → fingerprint dict.

        Args:
            video_path: Absolute path to the video file.
            embedding_service: Optional :class:`~app.services.embeddings.EmbeddingService`
                instance for computing the CLIP reference embedding in-place.
        """
        return self._analyze_file(video_path, embedding_service=embedding_service)

    # ── Private ────────────────────────────────────────────────────────────

    def _analyze_url(self, url: str, embedding_service: Any | None = None) -> dict[str, Any]:
        from tiktok_engine.video_ingest import download_video
        with tempfile.TemporaryDirectory() as tmp:
            video_path = str(download_video(url, tmp))
            return self._analyze_file(video_path, embedding_service=embedding_service)

    def _analyze_file(self, video_path: str, embedding_service: Any | None = None) -> dict[str, Any]:
        # Layer 1: FFmpeg — cut timestamps + color grade
        visual = extract_visual_style(video_path)
        info = get_media_info(video_path)

        cuts = visual["cut_timestamps"]
        duration = info["duration_sec"]
        all_ts = sorted([0.0] + cuts + [duration])
        shot_durations = [
            round(all_ts[i + 1] - all_ts[i], 3)
            for i in range(len(all_ts) - 1)
            if all_ts[i + 1] - all_ts[i] > 0.08
        ]
        avg_shot = visual["avg_cut_duration_sec"]

        # Aspect ratio
        w = info.get("width", 1080)
        h = info.get("height", 1920)
        from math import gcd
        g = gcd(w, h)
        aspect_ratio = f"{w // g}:{h // g}"

        # Layer 2: Beat detection (optional)
        beat_data = _detect_beats(video_path)

        # Layer 3: Claude Vision — qualitative style
        qualitative = self._vision_analyze(video_path, cuts, duration, avg_shot)

        # Build extended color profile
        cg = visual.get("color_grade", {})
        luma = cg.get("luma_avg", 128.0)
        contrast_val = cg.get("contrast", 1.0)
        temperature = "cool" if cg.get("brightness", 0) < -0.02 else (
            "warm" if cg.get("brightness", 0) > 0.05 else "neutral"
        )
        black_level = "crushed" if luma < 80 else ("lifted" if luma > 160 else "normal")

        cap_style_raw = qualitative.get("caption_style", {})
        caption_style = {
            "uses_text": cap_style_raw.get("uses_text", True),
            "position": cap_style_raw.get("position", "center"),
            "y_position_percent": cap_style_raw.get("y_position_percent", 75),
            "case": cap_style_raw.get("case", "uppercase"),
            "words_per_caption": cap_style_raw.get("words_per_caption", 3),
            "animation": cap_style_raw.get("animation", "pop"),
            "font_size_class": cap_style_raw.get("font_size_class", "large"),
            "font_family": cap_style_raw.get("font_family", "Arial-Black"),
            "has_stroke": cap_style_raw.get("has_stroke", True),
            "stroke_color": cap_style_raw.get("stroke_color", "black"),
            "all_caps": cap_style_raw.get("all_caps", True),
            "max_words": cap_style_raw.get("max_words", 4),
            "text_color": cap_style_raw.get("text_color", "white"),
            "background_box": cap_style_raw.get("background_box", False),
            "background_color": cap_style_raw.get("background_color", "black"),
            "background_opacity": cap_style_raw.get("background_opacity", 0.6),
        }

        motion_raw = qualitative.get("motion_style", {})
        if isinstance(motion_raw, str):
            motion_raw = {"primary": motion_raw}

        cut_pts = [0.0] + cuts
        alignment = _beat_alignment(cut_pts, beat_data.get("beat_points", []))

        fp = {
            "duration_sec": round(duration, 2),
            # Keep legacy key for backward compat
            "duration": round(duration, 2),
            "aspect_ratio": aspect_ratio,
            "cut_points": cuts,
            # Legacy key
            "cuts": cuts,
            "shot_durations": shot_durations,
            "avg_shot_duration": round(avg_shot, 3),
            "num_cuts": visual["num_cuts"],
            "pace": _pace_label(avg_shot),
            # Beat data (empty dicts when librosa not available)
            "beat_grid":         beat_data.get("beat_grid", []),
            "beat_points":       beat_data.get("beat_points", []),
            "tempo_bpm":         beat_data.get("tempo_bpm"),
            "downbeats":         beat_data.get("downbeats", []),
            "phrase_boundaries": beat_data.get("phrase_boundaries", []),
            "energy_curve":      beat_data.get("energy_curve", []),
            "intensity_curve":   beat_data.get("intensity_curve", []),
            "transient_peaks":   beat_data.get("transient_peaks", []),
            "cut_to_beat_alignment": alignment,
            "transitions": qualitative.get("transitions", ["hard_cut"]),
            "dominant_transition": qualitative.get("dominant_transition", "hard_cut"),
            "motion_style": motion_raw or {"primary": "slow_push"},
            # Legacy key
            "motion_pattern": motion_raw.get("primary", "slow_push") if motion_raw else "slow_push",
            "caption_style": caption_style,
            "color_profile": {
                "brightness": round(cg.get("brightness", 0.0), 4),
                "contrast": round(contrast_val, 3),
                "saturation": round(cg.get("saturation", 1.0), 3),
                "gamma": round(cg.get("gamma", 1.0), 3),
                "luma_avg": round(luma, 1),
                "temperature": temperature,
                "black_level": black_level,
            },
            # Legacy key
            "color_grade": cg,
            "hook_style": qualitative.get("hook_style", "bold opener"),
            "energy_level": qualitative.get("energy_level", "medium"),
            "tone": qualitative.get("tone", "engaging"),
        }
        # Merge inferred style profile (ranking_profile, faces_central, etc.)
        fp.update(infer_style_profile(fp))

        # Optional: embed reference video while the file is still accessible
        if embedding_service is not None:
            try:
                ref_emb = embedding_service.embed_reference_video(video_path)
                if ref_emb is not None:
                    fp["_ref_embedding"] = ref_emb
                    logger.debug("Reference CLIP embedding stored (%d dims)", len(ref_emb))
            except Exception as exc:
                logger.debug("Reference embedding skipped: %s", exc)

        return fp

    def _vision_analyze(self, video_path: str, cuts: list[float], duration: float, avg_shot: float) -> dict[str, Any]:
        try:
            from app.services.llm_client import _strip_markdown_fences
            from tiktok_engine.prompts import SYSTEM_PROMPT
        except ImportError:
            return {}

        with tempfile.TemporaryDirectory() as tmp:
            frames = _extract_frames(video_path, tmp, cuts, n=8)
            if not frames:
                return {}
            prompt = _VISION_PROMPT.format(
                cut_timestamps=[round(c, 2) for c in cuts[:8]],
                duration=round(duration, 1),
                avg_shot=round(avg_shot, 2),
            )
            try:
                raw = self.llm.chat_with_images(SYSTEM_PROMPT, prompt, frames)
                return json.loads(_strip_markdown_fences(raw))
            except Exception as exc:
                logger.warning("Vision analysis failed: %s", exc)
                return {}

    def _merge(self, fingerprints: list[dict[str, Any]]) -> dict[str, Any]:
        all_shots: list[float] = sorted(
            s for fp in fingerprints for s in fp.get("shot_durations", [])
        )
        all_transitions: list[str] = [
            t for fp in fingerprints for t in fp.get("transitions", [])
        ]
        dominant = Counter(all_transitions).most_common(1)[0][0] if all_transitions else "hard_cut"

        def _avg(key: str) -> float:
            vals = [fp.get(key, 0) for fp in fingerprints if fp.get(key) is not None]
            return round(sum(vals) / len(vals), 3) if vals else 0.0

        base = fingerprints[0]
        cg_keys = ("brightness", "contrast", "saturation", "gamma", "luma_avg")
        merged_cg = {k: _avg(k) for k in cg_keys}

        return {
            **base,
            "shot_durations": all_shots,
            "avg_shot_duration": _avg("avg_shot_duration"),
            "num_cuts": sum(fp.get("num_cuts", 0) for fp in fingerprints),
            "transitions": list(set(all_transitions)) or ["hard_cut"],
            "dominant_transition": dominant,
            "color_grade": {**base.get("color_grade", {}), **merged_cg},
            "color_profile": {**base.get("color_profile", {}), **merged_cg},
        }

    @staticmethod
    def _default_fingerprint() -> dict[str, Any]:
        cap = {
            "uses_text": True,
            "position": "center",
            "case": "uppercase",
            "words_per_caption": 3,
            "animation": "pop",
            "font_size_class": "large",
            "has_stroke": True,
            "all_caps": True,
            "max_words": 4,
        }
        fp = {
            "duration_sec": 30.0, "duration": 30.0,
            "aspect_ratio": "9:16",
            "cut_points": [], "cuts": [],
            "shot_durations": [1.5] * 20,
            "avg_shot_duration": 1.5,
            "num_cuts": 20,
            "pace": "medium",
            "beat_points": [], "beat_grid": [], "tempo_bpm": None,
            "downbeats": [], "phrase_boundaries": [],
            "energy_curve": [], "intensity_curve": [], "transient_peaks": [],
            "cut_to_beat_alignment": 0.0,
            "transitions": ["hard_cut"],
            "dominant_transition": "hard_cut",
            "motion_style": {"primary": "slow_push"},
            "motion_pattern": "slow_push",
            "caption_style": cap,
            "color_profile": {
                "brightness": 0.0, "contrast": 1.0, "saturation": 1.0,
                "gamma": 1.0, "luma_avg": 128.0, "temperature": "neutral", "black_level": "normal",
            },
            "color_grade": {
                "brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "gamma": 1.0, "luma_avg": 128.0,
            },
            "hook_style": "bold opener",
            "energy_level": "medium",
            "tone": "engaging",
        }
        fp.update(infer_style_profile(fp))
        return fp
