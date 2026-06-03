"""
FootageAnalyzer — builds a scored footage_index from uploaded clips.

Analysis layers (all deterministic — zero LLM API calls):
  1. ffprobe:          duration, resolution, has_audio
  2. Scene detection:  internal cuts via extract_visual_style()
  3. Frame quality:    sharpness (Laplacian edge density via PIL)
  4. Motion:           optical flow magnitude via OpenCV (fallback: frame diff)
  5. Speech:           silence detection → speech segments
  6. Stability:        average inter-frame motion as a stability proxy

Batch extensions (50+ clip support):
  - Per-clip segment cap (max_segments_per_clip)
  - Near-duplicate deduplication via cosine similarity of feature vectors
  - Global cap on segments forwarded to EditPlanner (max_selected_segments)
  - Rejection log: every skipped/rejected segment records a reason
  - Variety enforcement: min_clip_variety distinct source clips guaranteed

Output per clip:
{
    "asset_id": "footage_00",
    "path": "/tmp/footage_00.mp4",
    "duration_sec": 18.7,
    "resolution": [1080, 1920],
    "usable_segments": [
        {
            "start": 2.1, "end": 3.6,
            "score": 8.7,
            "reason": "high sharpness, motion peak",
            "tags": ["sharp_detail", "motion"]
        }
    ],
    "quality": {
        "blur_score": 0.11,
        "brightness": "normal",
        "stability": "handheld"
    },
    "has_speech": true,
    "speech_segments": [{"start": 2.1, "end": 8.4}],
    # Legacy fields (kept for backward compatibility)
    "moments": [...],
    "scene_changes": [...]
}

Batch result also includes:
{
    "batch_report": {
        "total_clips": 52,
        "total_segments_before_dedup": 312,
        "total_segments_after_dedup": 234,
        "total_selected": 120,
        "rejection_log": [
            {"asset_id": "footage_03", "start": 4.1, "reason": "near-duplicate of footage_01@2.3"},
            ...
        ]
    }
}
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

# Optional OpenCV for optical flow
try:
    import cv2 as _cv2
    import numpy as _np
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False

# Optional face detection (Haar cascade bundled with OpenCV)
_FACE_CASCADE = None
if _HAS_CV2:
    try:
        _cascade_path = _cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        _fc = _cv2.CascadeClassifier(_cascade_path)
        if not _fc.empty():
            _FACE_CASCADE = _fc
    except Exception:
        pass

_FFMPEG = (
    "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
    if Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg").exists()
    else "ffmpeg"
)

# Window parameters for segment subdivision
_MAX_SEG = 3.5   # segments longer than this get split
_WIN = 2.0
_STEP = 1.5


def _frame_sharpness(frame_path: str) -> float:
    """Laplacian edge density (higher = sharper)."""
    try:
        img = Image.open(frame_path).convert("L").resize((150, 150))
        edges = img.filter(ImageFilter.FIND_EDGES)
        return sum(edges.getdata()) / (150 * 150)
    except Exception:
        return 0.0


def _frame_brightness(frame_path: str) -> float:
    """Average pixel brightness 0–255."""
    try:
        img = Image.open(frame_path).convert("L").resize((50, 50))
        return sum(img.getdata()) / (50 * 50)
    except Exception:
        return 128.0


def _brightness_label(brightness: float) -> str:
    if brightness < 60:
        return "dark"
    if brightness > 200:
        return "overexposed"
    if brightness < 90:
        return "underexposed"
    return "normal"


def _extract_frame(video_path: str, timestamp: float, out_path: str) -> bool:
    r = subprocess.run(
        [_FFMPEG, "-y", "-ss", f"{timestamp:.3f}", "-i", video_path,
         "-vframes", "1", "-vf", "scale=300:-2", "-q:v", "5", out_path],
        capture_output=True,
    )
    return r.returncode == 0 and Path(out_path).exists()


def _detect_face(frame_path: str) -> tuple[bool, int | None]:
    """Detect a frontal face in *frame_path*; return (face_present, face_hash).

    *face_hash* is a coarse perceptual hash of the largest face region (81
    possible values).  Two clips sharing the same hash very likely show the
    same person, which is used by ClipRanker for face-consistency scoring.
    Returns ``(False, None)`` when OpenCV or the cascade are unavailable.
    """
    if not _HAS_CV2 or _FACE_CASCADE is None:
        return False, None
    try:
        img = _cv2.imread(frame_path, _cv2.IMREAD_GRAYSCALE)
        if img is None:
            return False, None
        faces = _FACE_CASCADE.detectMultiScale(
            img, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30)
        )
        if not len(faces):
            return False, None
        # Hash the largest face region for consistency comparison
        x, y, w, h = max(faces, key=lambda f: int(f[2]) * int(f[3]))
        face_region = img[y : y + h, x : x + w]
        face_small  = _cv2.resize(face_region, (16, 16))
        q = face_small.shape[0] // 2
        quads = [
            int(_np.sum(face_small[:q, :q])),
            int(_np.sum(face_small[:q, q:])),
            int(_np.sum(face_small[q:, :q])),
            int(_np.sum(face_small[q:, q:])),
        ]
        # Discretise each quadrant to 3 levels → 81 possible hashes
        lvl      = [min(v // 1000, 2) for v in quads]
        face_hash = lvl[0] * 27 + lvl[1] * 9 + lvl[2] * 3 + lvl[3]
        return True, face_hash
    except Exception:
        return False, None


def _dominant_color(frame_path: str) -> list[int]:
    """Average RGB [r, g, b] of *frame_path*, downsampled for speed."""
    try:
        img    = Image.open(frame_path).convert("RGB").resize((32, 32))
        pixels = list(img.getdata())
        n      = len(pixels)
        r      = sum(p[0] for p in pixels) // n
        g      = sum(p[1] for p in pixels) // n
        b      = sum(p[2] for p in pixels) // n
        return [r, g, b]
    except Exception:
        return [128, 128, 128]


def _motion_score_cv2(video_path: str, start: float, end: float) -> float:
    """Estimate average optical flow magnitude over a segment using OpenCV."""
    if not _HAS_CV2:
        return 0.0
    try:
        cap = _cv2.VideoCapture(video_path)
        fps = cap.get(_cv2.CAP_PROP_FPS) or 30
        cap.set(_cv2.CAP_PROP_POS_MSEC, start * 1000)

        scores: list[float] = []
        prev_gray = None
        frame_step = max(1, int(fps / 5))   # ~5 samples per second
        frame_count = 0

        while cap.get(_cv2.CAP_PROP_POS_MSEC) < end * 1000:
            ret, frame = cap.read()
            if not ret:
                break
            if frame_count % frame_step == 0:
                gray = _cv2.cvtColor(frame, _cv2.COLOR_BGR2GRAY)
                if prev_gray is not None:
                    flow = _cv2.calcOpticalFlowFarneback(
                        prev_gray, gray, None,
                        0.5, 3, 15, 3, 5, 1.2, 0,
                    )
                    mag, _ = _cv2.cartToPolar(flow[..., 0], flow[..., 1])
                    scores.append(float(_np.mean(mag)))
                prev_gray = gray
            frame_count += 1

        cap.release()
        return round(sum(scores) / len(scores), 3) if scores else 0.0
    except Exception:
        return 0.0


def _stability_label(avg_motion: float) -> str:
    if avg_motion < 0.5:
        return "stable"
    if avg_motion < 2.0:
        return "slight_movement"
    if avg_motion < 5.0:
        return "handheld"
    return "shaky"


class FootageAnalyzer:
    """Score and index uploaded footage clips — zero LLM calls.

    Args:
        max_segments_per_clip:       Keep at most this many top-scoring segments
                                     per source clip (default: settings value).
        max_selected_segments:       Hard global cap on segments passed to planner.
        duplicate_similarity_threshold: Cosine distance above which two segments
                                     are treated as near-duplicates.
    """

    def __init__(
        self,
        max_segments_per_clip: int | None = None,
        max_selected_segments: int | None = None,
        duplicate_similarity_threshold: float | None = None,
    ):
        from app.config import get_settings
        s = get_settings()
        self.max_segments_per_clip = max_segments_per_clip if max_segments_per_clip is not None else s.max_segments_per_clip
        self.max_selected_segments = max_selected_segments if max_selected_segments is not None else s.max_selected_segments
        self.duplicate_similarity_threshold = (
            duplicate_similarity_threshold
            if duplicate_similarity_threshold is not None
            else s.duplicate_similarity_threshold
        )

    def analyze_all(self, footage_paths: list[Path]) -> list[dict[str, Any]]:
        """Analyze all footage clips and return a footage_index.

        For 50+ clips this calls ``analyze_batch`` which applies deduplication
        and the per-clip segment cap.  The returned index always includes a
        ``batch_report`` key at position 0 (as a sentinel entry with
        ``asset_id == "__batch_report__"``).
        """
        index: list[dict[str, Any]] = []
        for i, fp in enumerate(footage_paths):
            clip_id = f"footage_{i:02d}"
            try:
                entry = self._analyze_clip(str(fp), clip_id)
                index.append(entry)
                logger.debug(
                    "%s: %.1fs, %d segments, has_speech=%s, stability=%s",
                    clip_id, entry["duration_sec"],
                    len(entry["usable_segments"]),
                    entry["has_speech"],
                    entry["quality"]["stability"],
                )
            except Exception as exc:
                logger.warning("FootageAnalyzer skipped %s: %s", fp.name, exc)
                index.append(self._fallback_entry(str(fp), clip_id))

        # Apply batch post-processing (per-clip cap + dedup + global cap)
        index, batch_report = self._post_process_batch(index)
        # Attach report as a sentinel entry so callers can log/save it without
        # needing a separate return channel.
        index.append({"asset_id": "__batch_report__", **batch_report})
        return index

    # ── Batch post-processing ─────────────────────────────────────────────

    def _post_process_batch(
        self,
        raw_index: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Apply per-clip cap, deduplication, and global cap.

        Returns:
            (processed_index, batch_report)

        The returned index has real clips only (no sentinel).
        """
        rejection_log: list[dict[str, Any]] = []
        total_before = 0

        # ── Step 1: per-clip segment cap (keep top-N by score) ────────────
        for clip in raw_index:
            segs = clip.get("usable_segments") or []
            total_before += len(segs)
            if len(segs) > self.max_segments_per_clip:
                segs_sorted = sorted(segs, key=lambda s: s.get("score", 0.0), reverse=True)
                kept = segs_sorted[: self.max_segments_per_clip]
                rejected = segs_sorted[self.max_segments_per_clip :]
                for r in rejected:
                    rejection_log.append({
                        "asset_id": clip["asset_id"],
                        "start": r.get("start"),
                        "score": r.get("score"),
                        "reason": f"per-clip cap ({self.max_segments_per_clip}): lower-scored segment dropped",
                    })
                clip["usable_segments"] = kept
                clip["moments"] = kept   # keep legacy alias in sync

        total_after_cap = sum(len(c.get("usable_segments") or []) for c in raw_index)

        # ── Step 2: near-duplicate deduplication ─────────────────────────
        all_segs_with_owner: list[tuple[dict, dict]] = [
            (clip, seg)
            for clip in raw_index
            for seg in (clip.get("usable_segments") or [])
        ]
        retained_segs = self._deduplicate_segments(all_segs_with_owner, rejection_log)
        total_after_dedup = len(retained_segs)

        # Re-build usable_segments on each clip from retained set
        retained_ids: set[str] = set()
        for clip, seg in retained_segs:
            uid = f"{clip['asset_id']}@{seg.get('start', 0):.3f}"
            retained_ids.add(uid)

        for clip in raw_index:
            filtered = [
                s for s in (clip.get("usable_segments") or [])
                if f"{clip['asset_id']}@{s.get('start', 0):.3f}" in retained_ids
            ]
            clip["usable_segments"] = filtered
            clip["moments"] = filtered

        # ── Step 3: global cap (sort all segs by score, keep top-N) ──────
        all_flat = [
            (clip, seg)
            for clip in raw_index
            for seg in (clip.get("usable_segments") or [])
        ]
        total_selected = len(all_flat)

        if len(all_flat) > self.max_selected_segments:
            all_flat_sorted = sorted(all_flat, key=lambda cs: cs[1].get("score", 0.0), reverse=True)
            kept_global = set()
            # Ensure variety: first pick the best from each clip, then fill remainder
            for clip in raw_index:
                clip_segs = [
                    (c, s) for c, s in all_flat_sorted if c["asset_id"] == clip["asset_id"]
                ]
                if clip_segs:
                    c, s = clip_segs[0]
                    kept_global.add((c["asset_id"], round(s.get("start", 0), 3)))

            remaining = [
                (c, s) for c, s in all_flat_sorted
                if (c["asset_id"], round(s.get("start", 0), 3)) not in kept_global
            ]
            slots_left = self.max_selected_segments - len(kept_global)
            for c, s in remaining[:max(0, slots_left)]:
                kept_global.add((c["asset_id"], round(s.get("start", 0), 3)))

            # Reject anything not in kept_global
            for clip in raw_index:
                new_segs = []
                for s in (clip.get("usable_segments") or []):
                    key = (clip["asset_id"], round(s.get("start", 0), 3))
                    if key in kept_global:
                        new_segs.append(s)
                    else:
                        rejection_log.append({
                            "asset_id": clip["asset_id"],
                            "start": s.get("start"),
                            "score": s.get("score"),
                            "reason": f"global cap ({self.max_selected_segments}): lower-priority segment dropped",
                        })
                clip["usable_segments"] = new_segs
                clip["moments"] = new_segs

            total_selected = self.max_selected_segments

        batch_report: dict[str, Any] = {
            "total_clips": len(raw_index),
            "total_segments_before_dedup": total_before,
            "total_segments_after_cap": total_after_cap,
            "total_segments_after_dedup": total_after_dedup,
            "total_selected": total_selected,
            "rejection_log": rejection_log,
        }

        logger.info(
            "Batch: %d clips | %d segs raw → %d after cap → %d after dedup → %d selected | %d rejected",
            batch_report["total_clips"],
            total_before,
            total_after_cap,
            total_after_dedup,
            total_selected,
            len(rejection_log),
        )

        return raw_index, batch_report

    def _deduplicate_segments(
        self,
        segs_with_owner: list[tuple[dict, dict]],
        rejection_log: list[dict[str, Any]],
    ) -> list[tuple[dict, dict]]:
        """Remove near-duplicate segments using a proxy feature vector.

        Proxy vector: [sharpness/15, motion/10, r/255, g/255, b/255,
                       brightness/255, start_norm, duration_norm]

        Cosine similarity above ``self.duplicate_similarity_threshold`` → duplicate.
        We keep the higher-scored segment of each duplicate pair.

        Falls back to CLIP embeddings when available (from ``_embedding`` key).
        """
        if len(segs_with_owner) <= 1:
            return segs_with_owner

        # Build feature matrix
        import math

        def _proxy_vec(seg: dict) -> list[float]:
            dc = seg.get("dominant_color") or [128, 128, 128]
            return [
                min(seg.get("sharpness", 0.0) / 15.0, 1.0),
                min(seg.get("motion", 0.0) / 10.0, 1.0),
                dc[0] / 255.0,
                dc[1] / 255.0,
                dc[2] / 255.0,
                min(seg.get("intensity", 0.0) / 10.0, 1.0),
            ]

        def _cosine(a: list[float], b: list[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b))
            na = math.sqrt(sum(x * x for x in a))
            nb = math.sqrt(sum(y * y for y in b))
            if na == 0 or nb == 0:
                return 0.0
            return dot / (na * nb)

        def _embedding_cosine(ea: list[float], eb: list[float]) -> float:
            return _cosine(ea, eb)

        retained: list[tuple[dict, dict]] = []
        retained_vecs: list[list[float]] = []
        retained_embeddings: list[list[float] | None] = []

        threshold = self.duplicate_similarity_threshold

        for clip, seg in segs_with_owner:
            # Prefer CLIP embedding when available
            emb: list[float] | None = seg.get("_embedding")

            is_dup = False
            for j, (rc, rs) in enumerate(retained):
                # Never deduplicate segments from the same source clip:
                # different time windows of the same clip are intentionally distinct.
                if rc["asset_id"] == clip["asset_id"]:
                    continue

                if emb is not None and retained_embeddings[j] is not None:
                    sim = _embedding_cosine(emb, retained_embeddings[j])  # type: ignore[arg-type]
                else:
                    vec = _proxy_vec(seg)
                    sim = _cosine(vec, retained_vecs[j])

                if sim >= threshold:
                    is_dup = True
                    # Keep the higher-scored one
                    existing_score = rs.get("score", 0.0)
                    current_score  = seg.get("score", 0.0)
                    if current_score > existing_score:
                        # Replace the retained entry with the current (better) segment
                        rejection_log.append({
                            "asset_id": rc["asset_id"],
                            "start": rs.get("start"),
                            "score": existing_score,
                            "reason": (
                                f"near-duplicate of {clip['asset_id']}@{seg.get('start', 0):.2f}s "
                                f"(sim={sim:.3f}≥{threshold}); replaced by higher-scored segment"
                            ),
                        })
                        retained[j] = (clip, seg)
                        retained_vecs[j] = _proxy_vec(seg)
                        retained_embeddings[j] = emb
                    else:
                        rejection_log.append({
                            "asset_id": clip["asset_id"],
                            "start": seg.get("start"),
                            "score": current_score,
                            "reason": (
                                f"near-duplicate of {rc['asset_id']}@{rs.get('start', 0):.2f}s "
                                f"(sim={sim:.3f}≥{threshold})"
                            ),
                        })
                    break

            if not is_dup:
                retained.append((clip, seg))
                retained_vecs.append(_proxy_vec(seg))
                retained_embeddings.append(emb)

        # ── Per-clip survivor guarantee ────────────────────────────────────
        # Dedup can eliminate *all* segments from a source clip when its proxy
        # vectors happen to be near-identical to a different clip's vectors
        # (common with default/zero feature values when frame analysis is
        # unavailable, or with genuinely similar stock footage).  We always
        # keep the best-scored segment from every distinct source clip so that
        # the EditPlanner has material to pick from for variety.
        retained_asset_ids: set[str] = {rc["asset_id"] for rc, _ in retained}
        survivors: dict[str, tuple[dict, dict]] = {}
        for clip, seg in segs_with_owner:
            aid = clip["asset_id"]
            if aid in retained_asset_ids:
                continue
            existing = survivors.get(aid)
            if existing is None or seg.get("score", 0.0) > existing[1].get("score", 0.0):
                survivors[aid] = (clip, seg)

        for aid, (clip, seg) in survivors.items():
            retained.append((clip, seg))
            retained_vecs.append(_proxy_vec(seg))
            retained_embeddings.append(seg.get("_embedding"))
            # Remove rejection log entry for this specific (clip, segment) pair
            # since it is now retained — it was a near-duplicate but we're
            # keeping it for source variety.
            seg_start = seg.get("start")
            rejection_log[:] = [
                e for e in rejection_log
                if not (e["asset_id"] == aid and e.get("start") == seg_start)
            ]

        return retained

    # ── Private ────────────────────────────────────────────────────────────

    def _analyze_clip(self, video_path: str, clip_id: str) -> dict[str, Any]:
        info = get_media_info(video_path)
        duration = info["duration_sec"]
        w = info.get("width", 1080)
        h = info.get("height", 1920)

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
                            speech_segments.append({
                                "start": round(cursor, 3),
                                "end": round(s["start"], 3),
                            })
                        cursor = s["end"]
                    if cursor < duration - 0.5:
                        speech_segments.append({"start": round(cursor, 3), "end": round(duration, 3)})
            except Exception:
                pass

        # Build candidate windows from scene changes + subdivision of long segments
        all_ts = sorted([0.0] + scene_changes + [duration])
        candidate_windows: list[tuple[float, float]] = []
        for i in range(len(all_ts) - 1):
            seg_start, seg_end = all_ts[i], all_ts[i + 1]
            seg_dur = seg_end - seg_start
            if seg_dur < 0.4:
                continue
            if seg_dur <= _MAX_SEG:
                candidate_windows.append((seg_start, seg_end))
            else:
                t = seg_start
                while t < seg_end - 0.3:
                    w_start = round(t, 3)
                    w_end = round(min(t + _WIN, seg_end), 3)
                    if w_end - w_start >= 0.4:
                        candidate_windows.append((w_start, w_end))
                    t = round(t + _STEP, 3)
        if not candidate_windows:
            candidate_windows = [(0.0, round(duration, 3))]

        # Score each window
        usable_segments: list[dict[str, Any]] = []
        moments: list[dict[str, Any]] = []     # legacy compat
        motion_scores: list[float] = []
        brightness_sum = 0.0
        blur_sum = 0.0

        with tempfile.TemporaryDirectory() as tmp:
            for idx, (w_start, w_end) in enumerate(candidate_windows):
                w_dur = w_end - w_start
                mid = w_start + w_dur / 2.0
                frame_path = f"{tmp}/shot_{idx:04d}.jpg"
                sharp      = 0.0
                brightness = 128.0
                face_present   = False
                face_hash_val  = None
                dominant_color = [128, 128, 128]

                if _extract_frame(video_path, mid, frame_path):
                    sharp          = _frame_sharpness(frame_path)
                    brightness     = _frame_brightness(frame_path)
                    face_present, face_hash_val = _detect_face(frame_path)
                    dominant_color = _dominant_color(frame_path)

                brightness_sum += brightness
                blur_sum += max(0.0, 15.0 - sharp) / 15.0  # blur = inverse of sharpness

                # Motion via OpenCV (if available)
                motion = _motion_score_cv2(video_path, w_start, w_end)
                motion_scores.append(motion)

                # Quality classification
                if sharp > 8.0:
                    shot_type = "sharp_detail"
                elif sharp > 3.0:
                    shot_type = "usable"
                else:
                    shot_type = "soft_blur"

                # Tags
                tags: list[str] = [shot_type]
                if motion > 3.0:
                    tags.append("motion")
                if motion < 0.5:
                    tags.append("static")
                for seg in speech_segments:
                    if seg["start"] <= mid <= seg["end"]:
                        tags.append("speech")
                        break

                # Score: sharpness (60%) + duration fit (20%) + motion bonus (20%)
                dur_score = max(0.0, 3.0 - abs(w_dur - 2.0))
                motion_bonus = min(motion * 0.3, 2.0)  # cap contribution
                score = round(min(sharp, 15.0) * 0.6 + dur_score * 0.2 + motion_bonus, 2)

                reasons = []
                if sharp > 8.0:
                    reasons.append("high sharpness")
                if motion > 3.0:
                    reasons.append("motion peak")
                if any(t == "speech" for t in tags):
                    reasons.append("speech present")
                reason = ", ".join(reasons) or shot_type

                seg_entry = {
                    "start": w_start,
                    "end": w_end,
                    "score": score,
                    "reason": reason,
                    "tags": tags,
                    # Legacy fields for backward compat with edit_planner
                    "duration": round(w_dur, 3),
                    "type": shot_type,
                    "description": f"{clip_id} [{w_start:.1f}s–{w_end:.1f}s] {shot_type}",
                    "sharpness": round(sharp, 2),
                    "motion": round(motion, 3),                    # Semantic-scoring fields (used by ClipRanker)
                    "face_present":   face_present,
                    "face_hash":      face_hash_val,
                    "dominant_color": dominant_color,
                    "intensity":      round(min(motion * 0.7 + min(sharp, 15.0) / 15.0 * 3.0, 10.0), 2),                }
                usable_segments.append(seg_entry)
                moments.append(seg_entry)  # alias for edit_planner compat

        # Clip-level quality summary
        n = max(len(candidate_windows), 1)
        avg_brightness = brightness_sum / n
        avg_blur = blur_sum / n
        avg_motion = sum(motion_scores) / len(motion_scores) if motion_scores else 0.0

        quality = {
            "blur_score": round(avg_blur, 3),
            "brightness": _brightness_label(avg_brightness),
            "stability": _stability_label(avg_motion),
        }

        # Inject clip-level quality into every segment so ClipRanker can score
        # aesthetics without needing a separate lookup.
        for seg in usable_segments:
            seg["clip_quality"] = quality

        return {
            "asset_id": clip_id,
            "path": video_path,
            "file_path": video_path,          # legacy key
            "duration_sec": round(duration, 2),
            "duration": round(duration, 2),   # legacy key
            "resolution": [w, h],
            "usable_segments": usable_segments,
            "moments": moments,               # legacy key — edit_planner uses this
            "quality": quality,
            "has_speech": has_speech,
            "speech_segments": speech_segments,
            "scene_changes": scene_changes,
        }

    @staticmethod
    def _fallback_entry(video_path: str, clip_id: str) -> dict[str, Any]:
        try:
            dur = get_media_info(video_path)["duration_sec"]
        except Exception:
            dur = 5.0
        quality = {"blur_score": 0.5, "brightness": "normal", "stability": "unknown"}
        seg = {
            "start": 0.0, "end": round(dur, 3), "score": 4.0,
            "reason": "fallback (analysis failed)", "tags": ["usable"],
            "duration": round(dur, 3), "type": "usable",
            "description": f"{clip_id} full", "sharpness": 0.0, "motion": 0.0,
            "face_present": False, "face_hash": None,
            "dominant_color": [128, 128, 128], "intensity": 4.0,
            "clip_quality": quality,
        }
        return {
            "asset_id": clip_id,
            "path": video_path,
            "file_path": video_path,
            "duration_sec": round(dur, 2),
            "duration": round(dur, 2),
            "resolution": [1080, 1920],
            "usable_segments": [seg],
            "moments": [seg],
            "quality": quality,
            "has_speech": False,
            "speech_segments": [],
            "scene_changes": [],
        }
