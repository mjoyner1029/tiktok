"""
ReferenceAnalyzer — extracts a measurable style fingerprint from reference TikTok URLs.

Two-layer analysis:
  1. FFmpeg-based (deterministic): actual cut timestamps, shot durations, color grade
  2. Claude Vision (qualitative): caption style, hook, transition type, energy

Output — reference_fingerprint dict:
{
    "duration": 37.4,
    "cuts": [0.0, 1.2, 2.0, 3.6],
    "shot_durations": [1.2, 0.8, 1.6, ...],
    "avg_shot_duration": 1.4,
    "num_cuts": 15,
    "color_grade": {"brightness": 0.05, "contrast": 1.1, "saturation": 1.2, "gamma": 0.9},
    "caption_style": {"position": "bottom", "font_size_class": "large", "all_caps": true,
                      "max_words": 4, "has_stroke": true, "animation": "pop"},
    "hook_style": "bold question over B-roll",
    "transitions": ["hard_cut", "flash_cut"],
    "dominant_transition": "hard_cut",
    "motion_pattern": "slow_push",
    "energy_level": "high",
    "tone": "aspirational and bold"
}
"""
from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from app.services.media_analyzer import extract_visual_style, get_media_info

logger = logging.getLogger(__name__)

# Use full ffmpeg if available (has drawtext/libfreetype)
_FFMPEG = (
    "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
    if Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg").exists()
    else "ffmpeg"
)

_VISION_PROMPT = """\
You are analyzing frames from a TikTok video sampled at the detected cut points.
Cut timestamps detected: {cut_timestamps}s
Total duration: {duration}s | Average shot length: {avg_shot}s

Analyze the EDITING STYLE from these frames:

1. CAPTIONS: What text appears on screen? Estimate:
   - position: bottom / center / top
   - font_size_class: small / medium / large
   - all_caps: true / false
   - max_words: integer (typical words per caption)
   - has_stroke: true (white text with black outline) / false
   - animation: pop / slide_up / fade / none

2. HOOK: What happens in the first shot? Describe the hook technique in ≤10 words.

3. TRANSITIONS: What types of transitions connect shots? Pick from:
   hard_cut, flash_cut, whip_pan_left, whip_pan_right, swipe_left, swipe_right,
   dissolve, fade, zoom_transition.

4. MOTION: What camera/zoom motion is used most? Pick one:
   static, slow_push, zoom_in, zoom_out, shake.

5. ENERGY: low / medium / high.

6. TONE: Emotional tone in 1-3 words (e.g., "aspirational and bold").

Return ONLY valid JSON (no markdown fences):
{{
    "caption_style": {{
        "position": "bottom",
        "font_size_class": "large",
        "all_caps": true,
        "max_words": 4,
        "has_stroke": true,
        "animation": "pop"
    }},
    "hook_style": "bold statement question over B-roll",
    "transitions": ["hard_cut", "flash_cut"],
    "dominant_transition": "hard_cut",
    "motion_pattern": "slow_push",
    "energy_level": "high",
    "tone": "aspirational and bold"
}}
"""


def _extract_frames_at(video_path: str, output_dir: str, timestamps: list[float], n: int = 8) -> list[str]:
    """Extract frames at the given timestamps (or evenly-spaced if no timestamps)."""
    frames: list[str] = []
    info = get_media_info(video_path)
    dur = info["duration_sec"]

    if timestamps:
        sample_ts = [t for t in timestamps if 0 <= t <= dur][:n]
    else:
        sample_ts = [dur * (i + 0.5) / n for i in range(n)]

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


class ReferenceAnalyzer:
    """Extract a measurable style fingerprint from reference TikTok URLs."""

    def __init__(self, llm):
        self.llm = llm

    # ── Public API ─────────────────────────────────────────────────────────

    def analyze_urls(self, urls: list[str]) -> dict[str, Any]:
        """Analyze one or more reference TikTok URLs → merged fingerprint."""
        fingerprints: list[dict[str, Any]] = []
        for url in urls:
            if not url.strip().startswith("http"):
                continue
            try:
                fp = self._analyze_url(url)
                fingerprints.append(fp)
                logger.info(
                    "Fingerprint [%s]: %d cuts, avg_shot=%.2fs, transition=%s",
                    url[-40:], fp["num_cuts"], fp["avg_shot_duration"], fp["dominant_transition"],
                )
            except Exception as exc:
                logger.warning("Reference analysis failed for %s: %s", url, exc)

        if not fingerprints:
            logger.warning("No references analyzed — using defaults")
            return self._default_fingerprint()
        if len(fingerprints) == 1:
            return fingerprints[0]
        return self._merge(fingerprints)

    # ── Private ────────────────────────────────────────────────────────────

    def _analyze_url(self, url: str) -> dict[str, Any]:
        from tiktok_engine.video_ingest import download_video
        with tempfile.TemporaryDirectory() as tmp:
            video_path = str(download_video(url, tmp))
            return self._analyze_file(video_path)

    def _analyze_file(self, video_path: str) -> dict[str, Any]:
        # Layer 1: FFmpeg — real cut timestamps + color grade
        visual = extract_visual_style(video_path)
        info = get_media_info(video_path)

        cuts = visual["cut_timestamps"]
        all_ts = sorted([0.0] + cuts + [info["duration_sec"]])
        shot_durations = [
            round(all_ts[i + 1] - all_ts[i], 3)
            for i in range(len(all_ts) - 1)
            if all_ts[i + 1] - all_ts[i] > 0.1  # filter sub-frame artifacts
        ]

        # Layer 2: Claude Vision — qualitative features
        qualitative = self._vision_analyze(video_path, cuts, info["duration_sec"], visual["avg_cut_duration_sec"])

        return {
            "duration": round(info["duration_sec"], 2),
            "cuts": cuts,
            "shot_durations": shot_durations,
            "avg_shot_duration": visual["avg_cut_duration_sec"],
            "num_cuts": visual["num_cuts"],
            "color_grade": visual["color_grade"],
            "caption_style": qualitative.get("caption_style", self._default_caption_style()),
            "hook_style": qualitative.get("hook_style", "bold opener"),
            "transitions": qualitative.get("transitions", ["hard_cut"]),
            "dominant_transition": qualitative.get("dominant_transition", "hard_cut"),
            "motion_pattern": qualitative.get("motion_pattern", "slow_push"),
            "energy_level": qualitative.get("energy_level", "medium"),
            "tone": qualitative.get("tone", "engaging"),
        }

    def _vision_analyze(self, video_path: str, cuts: list[float], duration: float, avg_shot: float) -> dict[str, Any]:
        from tiktok_engine.llm_client import _strip_markdown_fences
        from tiktok_engine.prompts import SYSTEM_PROMPT

        with tempfile.TemporaryDirectory() as tmp:
            frames = _extract_frames_at(video_path, tmp, cuts, n=8)
            if not frames:
                return {}

            prompt = _VISION_PROMPT.format(
                cut_timestamps=[round(c, 2) for c in cuts[:8]],
                duration=round(duration, 1),
                avg_shot=round(avg_shot, 2),
            )
            raw = self.llm.chat_with_images(SYSTEM_PROMPT, prompt, frames)

        try:
            return json.loads(_strip_markdown_fences(raw))
        except Exception:
            logger.warning("Vision JSON parse failed: %.200s", raw)
            return {}

    def _merge(self, fingerprints: list[dict[str, Any]]) -> dict[str, Any]:
        """Merge multiple reference fingerprints into one combined fingerprint."""
        all_shots: list[float] = []
        for fp in fingerprints:
            all_shots.extend(fp["shot_durations"])
        all_shots.sort()

        all_transitions: list[str] = []
        for fp in fingerprints:
            all_transitions.extend(fp.get("transitions", []))
        dominant = Counter(all_transitions).most_common(1)[0][0] if all_transitions else "hard_cut"

        def _avg(key: str, sub: str | None = None) -> float:
            vals = [
                fp.get(sub, fp).get(key, 0) if sub else fp.get(key, 0)
                for fp in fingerprints
            ]
            return round(sum(vals) / len(vals), 3) if vals else 0.0

        base = fingerprints[0]
        return {
            "duration": _avg("duration"),
            "cuts": base["cuts"],
            "shot_durations": all_shots,
            "avg_shot_duration": round(sum(fp["avg_shot_duration"] for fp in fingerprints) / len(fingerprints), 2),
            "num_cuts": sum(fp["num_cuts"] for fp in fingerprints),
            "color_grade": {
                "brightness": _avg("brightness", "color_grade"),
                "contrast": _avg("contrast", "color_grade"),
                "saturation": _avg("saturation", "color_grade"),
                "gamma": _avg("gamma", "color_grade"),
                "luma_avg": _avg("luma_avg", "color_grade"),
            },
            "caption_style": base.get("caption_style", self._default_caption_style()),
            "hook_style": base.get("hook_style", "bold opener"),
            "transitions": list(set(all_transitions)) or ["hard_cut"],
            "dominant_transition": dominant,
            "motion_pattern": base.get("motion_pattern", "slow_push"),
            "energy_level": base.get("energy_level", "medium"),
            "tone": base.get("tone", "engaging"),
        }

    @staticmethod
    def _default_caption_style() -> dict[str, Any]:
        return {
            "position": "bottom",
            "font_size_class": "large",
            "all_caps": True,
            "max_words": 4,
            "has_stroke": True,
            "animation": "pop",
        }

    @staticmethod
    def _default_fingerprint() -> dict[str, Any]:
        return {
            "duration": 30.0,
            "cuts": [],
            "shot_durations": [1.5] * 20,
            "avg_shot_duration": 1.5,
            "num_cuts": 20,
            "color_grade": {"brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "gamma": 1.0, "luma_avg": 128.0},
            "caption_style": ReferenceAnalyzer._default_caption_style(),
            "hook_style": "bold opener",
            "transitions": ["hard_cut"],
            "dominant_transition": "hard_cut",
            "motion_pattern": "slow_push",
            "energy_level": "medium",
            "tone": "engaging",
        }
