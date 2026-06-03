"""
EditPlanner — builds a RenderEngine-compatible contract from fingerprint + footage_index.

Algorithm (deterministic first, LLM last):
  1. TIMING:     reference shot_durations → timeline slots (not guessed by LLM)
  2. SELECTION:  footage moments scored → best moment per slot (greedy, no LLM)
  3. VARIETY:    alternates clips to avoid repetition
  4. NARRATIVE:  single LLM call assigns caption text + selects hook shot
  5. CONTRACT:   assembles RenderEngine-compatible spec

The LLM is NOT responsible for timing, clip selection, or transitions.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_CAPTION_SYSTEM = "You are a TikTok caption writer. Be punchy, direct, energetic. No fluff."

_CAPTION_PROMPT = """\
You are assigning ON-SCREEN TEXT CAPTIONS to an already-planned TikTok edit.

REFERENCE STYLE:
  Tone: {tone}
  Energy: {energy_level}
  Hook technique: {hook_style}
  Caption rules: {caption_style_desc}
  Max words per caption: {max_words}
  All caps: {all_caps}

CONTENT DIRECTION: {content_hint}

EDIT TIMELINE (timing and clips are FIXED — only write the captions):
{timeline_text}

For EACH shot, provide:
  - caption: ≤{max_words} words, {caps_label}, punchy
  - moment_type: hook / build / climax / closer / broll

Also set hook_index: which shot index (0-based) should play FIRST as the hook.

Return ONLY valid JSON:
{{
    "hook_index": 0,
    "shots": [
        {{"caption": "CAPTION TEXT", "moment_type": "hook"}},
        {{"caption": "NEXT CAPTION", "moment_type": "build"}},
        ...
    ]
}}
"""

# Transition type → (RenderEngine-compatible name, duration in seconds)
_TRANSITION_DURATIONS: dict[str, float] = {
    "cut": 0.0, "hard_cut": 0.0, "hard cut": 0.0,
    "flash_cut": 0.0, "flash cut": 0.0, "flash": 0.0,
    "whip_pan_left": 0.12, "whip pan left": 0.12,
    "whip_pan_right": 0.12, "whip pan right": 0.12,
    "whip_pan": 0.12, "whip pan": 0.12,
    "swipe_left": 0.22, "swipe left": 0.22,
    "swipe_right": 0.22, "swipe right": 0.22,
    "swipe_up": 0.22, "swipe up": 0.22,
    "swipe_down": 0.22, "swipe down": 0.22,
    "fade": 0.45, "dissolve": 0.45,
    "zoom_transition": 0.25, "zoom transition": 0.25,
}

_MOTION_MAP: dict[str, dict[str, Any]] = {
    "slow_push":  {"type": "zoom_in",  "strength": 0.04},
    "zoom_in":    {"type": "zoom_in",  "strength": 0.06},
    "zoom_out":   {"type": "zoom_out", "strength": 0.06},
    "static":     {"type": "static",   "strength": 0.0},
    "shake":      {"type": "shake",    "strength": 0.03},
}

_CAPTION_POSITION_MAP = {
    "bottom": "lower_third", "lower_third": "lower_third",
    "top": "upper_third", "upper_third": "upper_third",
    "center": "center",
}

_CAPTION_ANIM_MAP = {
    "pop": "pop", "slide_up": "slide_up", "fade": "fade", "none": "fade",
}


class EditPlanner:
    """Turn fingerprint + footage_index into a RenderEngine-compatible contract."""

    def __init__(self, llm):
        self.llm = llm

    def plan(
        self,
        fingerprint: dict[str, Any],
        footage_index: list[dict[str, Any]],
        content_hint: str = "",
        max_shots: int = 32,
    ) -> dict[str, Any]:
        """
        Returns:
            {
                "tracks": {
                    "video": [{asset_id, source_in, source_out, start, end, motion,
                               transition_out, speed}],
                    "text":  [{start, end, text, position, animation}],
                },
                "_fingerprint": fingerprint,   # pass-through for color grade
            }
        """
        # ── Step 1: Derive shot duration sequence from reference ────────────
        shot_durations = self._build_shot_sequence(fingerprint, footage_index, max_shots)

        # ── Step 2: Assign best footage moments to each shot slot ───────────
        video_clips, descriptions = self._assign_footage(shot_durations, footage_index)
        if not video_clips:
            raise RuntimeError("No usable footage moments found in footage_index")

        # ── Step 3: LLM writes captions + selects hook ──────────────────────
        caption_data = self._get_captions(fingerprint, video_clips, descriptions, content_hint)
        hook_idx = caption_data.get("hook_index", 0)
        shots_info = caption_data.get("shots", [])

        # ── Step 4: Reorder for hook ──────────────────────────────────────
        if hook_idx and 0 < hook_idx < len(video_clips):
            video_clips.insert(0, video_clips.pop(hook_idx))
            descriptions.insert(0, descriptions.pop(hook_idx))
            if len(shots_info) > hook_idx:
                shots_info.insert(0, shots_info.pop(hook_idx))

        # ── Step 5: Build tracks ──────────────────────────────────────────
        transitions = fingerprint.get("transitions") or ["hard_cut"]
        caption_style = fingerprint.get("caption_style") or {}
        motion_pattern = fingerprint.get("motion_pattern", "slow_push")

        video_track: list[dict[str, Any]] = []
        text_track: list[dict[str, Any]] = []
        timeline_pos = 0.0

        for i, vc in enumerate(video_clips):
            shot_dur = vc["shot_dur"]
            trans_type = transitions[i % len(transitions)]
            trans_dur = _TRANSITION_DURATIONS.get(trans_type.lower(), 0.0)

            # Alternate zoom-in / zoom-out every 3 shots for visual variety
            motion_cfg = _MOTION_MAP.get(motion_pattern.lower().replace(" ", "_"),
                                         {"type": "zoom_in", "strength": 0.04}).copy()
            if motion_cfg["type"] == "zoom_in" and i % 3 == 2:
                motion_cfg = {"type": "zoom_out", "strength": motion_cfg["strength"]}

            video_track.append({
                "asset_id": vc["asset_id"],
                "source_in": vc["source_in"],
                "source_out": round(vc["source_in"] + shot_dur, 3),
                "start": round(timeline_pos, 3),
                "end": round(timeline_pos + shot_dur, 3),
                "motion": motion_cfg,
                "transition_out": {"type": trans_type, "duration": trans_dur},
                "speed": 1.0,
            })

            # Caption — appears from 0.15s in to 85% of the shot
            shot_info = shots_info[i] if i < len(shots_info) else {}
            caption_text = shot_info.get("caption", "").strip()
            if caption_text:
                if caption_style.get("all_caps"):
                    caption_text = caption_text.upper()
                text_track.append({
                    "start": round(timeline_pos + 0.15, 3),
                    "end": round(timeline_pos + shot_dur * 0.85, 3),
                    "text": caption_text,
                    "position": _CAPTION_POSITION_MAP.get(
                        caption_style.get("position", "bottom"), "lower_third"
                    ),
                    "animation": _CAPTION_ANIM_MAP.get(
                        caption_style.get("animation", "pop"), "pop"
                    ),
                })

            timeline_pos += shot_dur

        return {
            "tracks": {"video": video_track, "text": text_track},
            "_fingerprint": fingerprint,
        }

    # ── Private helpers ────────────────────────────────────────────────────

    def _build_shot_sequence(
        self,
        fingerprint: dict[str, Any],
        footage_index: list[dict[str, Any]],
        max_shots: int,
    ) -> list[float]:
        """Return a list of shot durations mirroring the reference rhythm."""
        ref_durs = fingerprint.get("shot_durations", [])
        avg = fingerprint.get("avg_shot_duration", 1.5)
        n_clips = len(footage_index)
        # Match the reference's actual shot count so rhythm stays faithful;
        # fall back to a time-based estimate when no reference durations exist.
        ref_shot_count = len(ref_durs) if ref_durs else max(10, round(avg * n_clips * 3))
        target = min(max_shots, max(10, ref_shot_count))

        if not ref_durs:
            return [avg] * target

        # Tile the reference duration distribution to fill target length
        tiled: list[float] = []
        while len(tiled) < target:
            tiled.extend(ref_durs)
        return tiled[:target]

    def _assign_footage(
        self,
        shot_durations: list[float],
        footage_index: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Greedy match: highest-scored, unused, long-enough moment per slot."""
        # Flatten all moments with source metadata
        all_moments: list[dict[str, Any]] = sorted(
            [
                {**m, "asset_id": clip["asset_id"], "clip_path": clip["file_path"]}
                for clip in footage_index
                for m in clip["moments"]
            ],
            key=lambda x: -x["score"],
        )

        video_clips: list[dict[str, Any]] = []
        descriptions: list[str] = []
        used_ids: set[str] = set()

        for shot_dur in shot_durations:
            best = self._find_best_moment(all_moments, used_ids, shot_dur, video_clips)
            if best is None:
                break
            uid = f"{best['asset_id']}_{best['start']:.3f}"
            used_ids.add(uid)
            video_clips.append({
                "asset_id": best["asset_id"],
                "source_in": best["start"],
                "shot_dur": shot_dur,
            })
            descriptions.append(best["description"])

        return video_clips, descriptions

    def _find_best_moment(
        self,
        all_moments: list[dict[str, Any]],
        used_ids: set[str],
        shot_dur: float,
        already_assigned: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        prev_asset = already_assigned[-1]["asset_id"] if already_assigned else None
        has_alternatives = len(all_moments) > len(already_assigned) + 1

        # First pass: prefer different clip than previous (variety)
        for m in all_moments:
            uid = f"{m['asset_id']}_{m['start']:.3f}"
            if uid in used_ids:
                continue
            if m["end"] - m["start"] < shot_dur - 0.05:
                continue
            if m["asset_id"] == prev_asset and has_alternatives:
                continue
            return m

        # Second pass: allow same clip if no alternatives
        for m in all_moments:
            uid = f"{m['asset_id']}_{m['start']:.3f}"
            if uid in used_ids:
                continue
            if m["end"] - m["start"] < shot_dur - 0.05:
                continue
            return m

        # Ultimate fallback: allow reuse — pick best-scored moment that fits the duration
        for m in all_moments:
            if m["end"] - m["start"] >= shot_dur - 0.05:
                return m
        # Last resort: return best moment regardless of duration fit
        return all_moments[0] if all_moments else None

    def _get_captions(
        self,
        fingerprint: dict[str, Any],
        video_clips: list[dict[str, Any]],
        descriptions: list[str],
        content_hint: str,
    ) -> dict[str, Any]:
        """Single LLM call: caption text + hook selection only."""
        caption_style = fingerprint.get("caption_style") or {}
        max_words = caption_style.get("max_words", 4)
        all_caps = caption_style.get("all_caps", True)
        caps_label = "ALL CAPS" if all_caps else "Title Case"
        cap_desc = (
            f"{'ALL CAPS' if all_caps else 'Title Case'}, "
            f"{caption_style.get('font_size_class', 'large')} font, "
            f"{'stroke outline' if caption_style.get('has_stroke') else 'no stroke'}, "
            f"{caption_style.get('position', 'bottom')} of screen"
        )

        timeline_text = "\n".join(
            f"  [{i}] {desc} ({vc['shot_dur']:.1f}s)"
            for i, (vc, desc) in enumerate(zip(video_clips, descriptions))
        )

        prompt = _CAPTION_PROMPT.format(
            tone=fingerprint.get("tone", "engaging"),
            energy_level=fingerprint.get("energy_level", "medium"),
            hook_style=fingerprint.get("hook_style", "bold opener"),
            caption_style_desc=cap_desc,
            max_words=max_words,
            all_caps=all_caps,
            caps_label=caps_label,
            content_hint=content_hint or "Make it engaging for TikTok viewers",
            timeline_text=timeline_text,
        )

        try:
            result = self.llm.chat_json(_CAPTION_SYSTEM, prompt, max_tokens=4096)
            return result if isinstance(result, dict) else {}
        except Exception as exc:
            logger.warning("Caption LLM call failed: %s", exc)
            return {
                "hook_index": 0,
                "shots": [{"caption": "", "moment_type": "broll"} for _ in video_clips],
            }
