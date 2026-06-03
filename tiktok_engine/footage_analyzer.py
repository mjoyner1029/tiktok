"""
FootageAnalyzer — builds a scored footage_index from uploaded clips.

For each clip, uses only ffmpeg + PIL (no LLM API calls):
  - Detects internal scene changes (sub-shots)
  - Scores each sub-shot by sharpness (Laplacian edge density)
  - Detects speech presence via silence analysis
  - Labels shot quality: sharp_detail / usable / soft_blur

Output — list of footage_index entries:
[{
    "asset_id": "footage_00",
    "file_path": "/tmp/footage_00.MOV",
    "duration": 18.2,
    "moments": [
        {
            "start": 2.1, "end": 4.3, "duration": 2.2,
            "type": "sharp_detail",
            "description": "footage_00 [2.1s-4.3s] sharp_detail",
            "score": 8.5,
            "sharpness": 12.3
        }
    ],
    "scene_changes": [2.1, 5.3, 11.8],
    "has_speech": true,
    "speech_segments": [{"start": 2.1, "end": 8.4}]
}]
"""
from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter

from app.services.media_analyzer import detect_silence, extract_visual_style, get_media_info

logger = logging.getLogger(__name__)

_FFMPEG = (
    "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
    if Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg").exists()
    else "ffmpeg"
)


def _frame_sharpness(frame_path: str) -> float:
    """Edge density via PIL FIND_EDGES — higher value means sharper frame."""
    try:
        img = Image.open(frame_path).convert("L").resize((150, 150))
        edges = img.filter(ImageFilter.FIND_EDGES)
        return sum(edges.getdata()) / (150 * 150)
    except Exception:
        return 0.0


def _extract_frame(video_path: str, timestamp: float, out_path: str) -> bool:
    r = subprocess.run(
        [_FFMPEG, "-y", "-ss", f"{timestamp:.3f}", "-i", video_path,
         "-vframes", "1", "-vf", "scale=300:-2", "-q:v", "5", out_path],
        capture_output=True,
    )
    return r.returncode == 0 and Path(out_path).exists()


class FootageAnalyzer:
    """Score and index uploaded footage clips without LLM calls."""

    def analyze_all(self, footage_paths: list[Path]) -> list[dict[str, Any]]:
        """Analyze all clips → footage_index list."""
        index: list[dict[str, Any]] = []
        for i, fp in enumerate(footage_paths):
            clip_id = f"footage_{i:02d}"
            try:
                entry = self._analyze_clip(str(fp), clip_id)
                index.append(entry)
                logger.debug(
                    "%s: %.1fs, %d moments, has_speech=%s",
                    clip_id, entry["duration"], len(entry["moments"]), entry["has_speech"],
                )
            except Exception as exc:
                logger.warning("FootageAnalyzer skipped %s: %s", fp.name, exc)
                # Minimal fallback so clip remains usable
                index.append(self._fallback_entry(str(fp), clip_id))
        return index

    # ── Private ────────────────────────────────────────────────────────────

    def _analyze_clip(self, video_path: str, clip_id: str) -> dict[str, Any]:
        info = get_media_info(video_path)
        duration = info["duration_sec"]

        # Internal scene changes
        scene_changes: list[float] = []
        try:
            visual = extract_visual_style(video_path)
            scene_changes = visual["cut_timestamps"]
        except Exception:
            pass

        # Speech / silence detection
        has_speech = False
        speech_segments: list[dict[str, float]] = []
        if info.get("has_audio") and duration > 1.0:
            try:
                silences = detect_silence(video_path, noise_threshold_db=-40.0, min_duration=0.3)
                silent_dur = sum(s["duration"] for s in silences)
                has_speech = (duration - silent_dur) > 1.0
                if has_speech:
                    cursor = 0.0
                    for s in sorted(silences, key=lambda x: x["start"]):
                        if s["start"] > cursor + 0.5:
                            speech_segments.append({"start": round(cursor, 3), "end": round(s["start"], 3)})
                        cursor = s["end"]
                    if cursor < duration - 0.5:
                        speech_segments.append({"start": round(cursor, 3), "end": round(duration, 3)})
            except Exception:
                pass

        # Build candidate windows from scene-change boundaries.
        # Any segment longer than _MAX_SEG is subdivided into overlapping _WIN-second
        # windows so the edit planner has many independent choices even when raw
        # footage has no internal cuts (the most common case).
        _MAX_SEG = 3.5   # seconds: split segments longer than this
        _WIN = 2.0       # window duration in seconds
        _STEP = 1.5      # step between window starts (allows ~25% overlap)

        all_ts = sorted([0.0] + scene_changes + [duration])
        candidate_windows: list[tuple[float, float]] = []
        for i in range(len(all_ts) - 1):
            seg_start = all_ts[i]
            seg_end = all_ts[i + 1]
            seg_dur = seg_end - seg_start
            if seg_dur < 0.4:
                continue
            if seg_dur <= _MAX_SEG:
                candidate_windows.append((seg_start, seg_end))
            else:
                # Slide a _WIN-second window across the segment
                t = seg_start
                while t < seg_end - 0.3:
                    w_start = round(t, 3)
                    w_end = round(min(t + _WIN, seg_end), 3)
                    if w_end - w_start >= 0.4:
                        candidate_windows.append((w_start, w_end))
                    t = round(t + _STEP, 3)

        if not candidate_windows:
            candidate_windows = [(0.0, round(duration, 3))]

        moments: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory() as tmp:
            for idx, (w_start, w_end) in enumerate(candidate_windows):
                w_dur = w_end - w_start
                mid = w_start + w_dur / 2.0
                frame_path = f"{tmp}/shot_{idx:04d}.jpg"
                sharp = 0.0
                if _extract_frame(video_path, mid, frame_path):
                    sharp = _frame_sharpness(frame_path)

                if sharp > 8.0:
                    shot_type = "sharp_detail"
                elif sharp > 3.0:
                    shot_type = "usable"
                else:
                    shot_type = "soft_blur"

                dur_score = max(0.0, 3.0 - abs(w_dur - 2.0))
                score = round(min(sharp, 15.0) * 0.6 + dur_score, 2)

                moments.append({
                    "start": w_start,
                    "end": w_end,
                    "duration": round(w_dur, 3),
                    "type": shot_type,
                    "description": f"{clip_id} [{w_start:.1f}s–{w_end:.1f}s] {shot_type}",
                    "score": score,
                    "sharpness": round(sharp, 2),
                })

        if not moments:
            moments = [{
                "start": 0.0, "end": round(duration, 3), "duration": round(duration, 3),
                "type": "usable", "description": f"{clip_id} full", "score": 5.0, "sharpness": 0.0,
            }]

        return {
            "asset_id": clip_id,
            "file_path": video_path,
            "duration": round(duration, 2),
            "moments": moments,
            "scene_changes": scene_changes,
            "has_speech": has_speech,
            "speech_segments": speech_segments,
        }

    @staticmethod
    def _fallback_entry(video_path: str, clip_id: str) -> dict[str, Any]:
        try:
            info = get_media_info(video_path)
            dur = info["duration_sec"]
        except Exception:
            dur = 5.0
        return {
            "asset_id": clip_id,
            "file_path": video_path,
            "duration": round(dur, 2),
            "moments": [{"start": 0.0, "end": round(dur, 3), "duration": round(dur, 3),
                         "type": "usable", "description": f"{clip_id} full", "score": 4.0, "sharpness": 0.0}],
            "scene_changes": [],
            "has_speech": False,
            "speech_segments": [],
        }
