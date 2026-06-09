"""
EditPlanner — turns (fingerprint + footage_index) into a validated EditTimeline.

Algorithm (deterministic first, LLM last):
  1. TIMING:     tile reference shot_durations → fixed timeline slots
  2. BEAT SYNC:  snap cut points to beat grid (if beat data available)
  3. SELECTION:  greedy best-scored moment per slot (variety enforced)
  4. CAPTIONS:   single LLM call — writes caption text + selects hook shot
  5. CONTRACT:   assemble + validate EditTimeline Pydantic model

The LLM never invents timing, transitions, or clip choices — only caption text.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.services.timeline_schema import (
    CaptionEvent,
    ClipEvent,
    ColorGrade,
    EditTimeline,
    MotionKeyframe,
    TransitionEvent,
)
from app.services.clip_scorer import ClipRanker

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
  Case: {caps_label}
  Pace: {pace}

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

_TRANSITION_DURATIONS: dict[str, float] = {
    "cut": 0.0, "hard_cut": 0.0, "hard cut": 0.0,
    "flash_cut": 0.0, "flash cut": 0.0, "flash": 0.0,
    "whip_pan_left": 0.12, "whip pan left": 0.12,
    "whip_pan_right": 0.12, "whip pan right": 0.12,
    "whip_pan": 0.12,
    "swipe_left": 0.22, "swipe_right": 0.22,
    "swipe_up": 0.22, "swipe_down": 0.22,
    "fade": 0.45, "dissolve": 0.45,
    "zoom_transition": 0.25,
}

_MOTION_MAP: dict[str, dict[str, Any]] = {
    "slow_push":  {"scale_start": 1.0, "scale_end": 1.08},
    "zoom_in":    {"scale_start": 1.0, "scale_end": 1.12},
    "zoom_out":   {"scale_start": 1.1, "scale_end": 1.0},
    "static":     {"scale_start": 1.0, "scale_end": 1.0},
    "shake":      {"scale_start": 1.02, "scale_end": 1.02},
}

_CAPTION_POSITION_MAP = {
    "bottom": "bottom", "lower_third": "bottom",
    "top": "top", "upper_third": "top",
    "center": "center",
}

# Map font_size_class strings to concrete pixel sizes at 1080×1920
_FONT_SIZE_CLASS_PX = {
    "small": 52,
    "medium": 64,
    "large": 80,
    "xlarge": 96,
}

# Normalize font_family labels Claude Vision may return → ASS-compatible family names
_FONT_FAMILY_MAP = {
    "Impact": "Impact",
    "impact": "Impact",
    "Arial-Black": "Arial Black",
    "arial-black": "Arial Black",
    "Arial Black": "Arial Black",
    "bold-sans": "Arial Black",
    "bold_sans": "Arial Black",
    "serif": "Georgia",
    "handwritten": "Marker Felt",
    "unknown": "Arial Black",
}


class EditPlanner:
    """Turn fingerprint + footage_index into a validated EditTimeline."""

    # ── Rhythm preset definitions ─────────────────────────────────────────
    # tolerance : max seconds a cut may stray from its target beat
    # snap_to   : "beat" | "downbeat" | "phrase" | None
    # offset    : constant seconds added to ideal cut point (off-beat feel)
    _RHYTHM_PRESETS: dict[str, dict] = {
        "tight_sync": {"snap_to": "beat",     "tolerance": 0.08, "offset": 0.0},
        "loose_sync": {"snap_to": "downbeat",  "tolerance": 0.20, "offset": 0.0},
        "cinematic":  {"snap_to": "phrase",    "tolerance": 0.40, "offset": 0.0},
        "chaotic":    {"snap_to": "beat",      "tolerance": 0.30, "offset": 0.10},
    }

    def __init__(self, llm):
        self.llm = llm

    def plan(
        self,
        fingerprint: dict[str, Any],
        footage_index: list[dict[str, Any]],
        content_hint: str = "",
        max_shots: int = 40,
        project_id: str = "",
        width: int = 1080,
        height: int = 1920,
        fps: int = 30,
        render_style: dict[str, Any] | None = None,
        rhythm_preset: str = "loose_sync",
        target_duration_sec: float | None = None,
        min_clip_variety: int | None = None,
    ) -> EditTimeline:
        """
        Returns a validated EditTimeline.
        Raises RuntimeError if no usable footage moments are found.

        Args:
            render_style:       Optional pre-built RenderStyle dict.  When omitted
                                it is generated from *fingerprint* via
                                ``StyleRenderer.from_fingerprint()``.
            rhythm_preset:      How aggressively cut points are snapped to beats.
                                One of: ``tight_sync``, ``loose_sync``,
                                ``cinematic``, ``chaotic``.
            target_duration_sec: When set, the shot sequence is trimmed / extended
                                so the total timeline length approximates this value.
                                Takes priority over max_shots when both are given.
            min_clip_variety:   Minimum number of distinct source clips that must
                                appear in the final timeline (variety enforcement).
                                Falls back to settings if None.
        """
        from app.config import get_settings
        _s = get_settings()
        if min_clip_variety is None:
            min_clip_variety = _s.min_clip_variety
        if target_duration_sec is None:
            target_duration_sec = _s.target_duration_sec

        # Strip batch report sentinel so it doesn't pollute the footage pool
        real_footage = [c for c in footage_index if c.get("asset_id") != "__batch_report__"]
        batch_report = next(
            (c for c in footage_index if c.get("asset_id") == "__batch_report__"), None
        )

        # ── Step 1: Build render_style if not supplied ───────────────────
        if render_style is None:
            from app.services.style_renderer import StyleRenderer
            render_style = StyleRenderer.from_fingerprint(fingerprint)

        # ── Step 2: Derive shot duration sequence from reference ─────────
        shot_durations = self._build_shot_sequence(
            fingerprint, real_footage, max_shots,
            target_duration_sec=target_duration_sec,
        )

        # ── Step 3: Resolve rhythm preset ────────────────────────────────
        r_cfg = self._RHYTHM_PRESETS.get(rhythm_preset, self._RHYTHM_PRESETS["loose_sync"])
        snap_to    = r_cfg["snap_to"]
        tolerance  = r_cfg["tolerance"]
        beat_offset = r_cfg["offset"]

        # Choose the appropriate beat grid for snapping
        if snap_to == "phrase":
            snap_pts = fingerprint.get("phrase_boundaries") or fingerprint.get("downbeats") or []
        elif snap_to == "downbeat":
            snap_pts = fingerprint.get("downbeats") or fingerprint.get("beat_grid") or fingerprint.get("beat_points") or []
        elif snap_to == "beat":
            snap_pts = fingerprint.get("beat_grid") or fingerprint.get("beat_points") or []
        else:
            snap_pts = []

        if snap_pts:
            shot_durations = self._snap_to_beats(
                shot_durations, snap_pts,
                tolerance=tolerance, offset=beat_offset,
            )

        # ── Step 4: Assign best footage moments to each slot ─────────────
        ranker = ClipRanker.from_fingerprint(fingerprint)
        clip_slots, descriptions = self._assign_footage(
            shot_durations, real_footage, ranker,
            min_clip_variety=min_clip_variety,
        )
        if not clip_slots:
            raise RuntimeError("No usable footage moments found in footage_index")

        # ── Step 5: LLM writes captions + selects hook ───────────────────
        caption_data = self._get_captions(fingerprint, clip_slots, descriptions, content_hint)
        hook_idx = caption_data.get("hook_index", 0)
        shots_info = caption_data.get("shots", [])

        # ── Step 6: Reorder for hook-first ───────────────────────────────
        if hook_idx and 0 < hook_idx < len(clip_slots):
            clip_slots.insert(0, clip_slots.pop(hook_idx))
            descriptions.insert(0, descriptions.pop(hook_idx))
            if len(shots_info) > hook_idx:
                shots_info.insert(0, shots_info.pop(hook_idx))

        # ── Step 7: Build EditTimeline ────────────────────────────────────
        transitions = fingerprint.get("transitions") or ["hard_cut"]
        cap_style = fingerprint.get("caption_style") or {}
        motion_str = (
            fingerprint.get("motion_style", {}).get("primary")
            or fingerprint.get("motion_pattern", "slow_push")
        )

        # Typography from render_style caption_preset takes priority over
        # fingerprint cap_style so the profile's design intent wins.
        cap_preset = (render_style or {}).get("caption_preset") or {}

        clips: list[ClipEvent] = []
        captions: list[CaptionEvent] = []
        timeline_pos = 0.0

        for i, slot in enumerate(clip_slots):
            shot_dur = slot["shot_dur"]
            trans_type = transitions[i % len(transitions)]
            trans_dur = _TRANSITION_DURATIONS.get(trans_type.lower(), 0.0)

            # Alternate scale direction every 3 shots for visual variety
            motion_def = _MOTION_MAP.get(motion_str.lower().replace(" ", "_"), _MOTION_MAP["slow_push"]).copy()
            scale_start = motion_def["scale_start"]
            scale_end = motion_def["scale_end"]
            if scale_end > scale_start and i % 3 == 2:          # reverse zoom every 3rd
                scale_start, scale_end = scale_end, scale_start

            keyframes = [
                MotionKeyframe(t=0.0, scale=scale_start),
                MotionKeyframe(t=shot_dur, scale=scale_end),
            ] if scale_start != scale_end else []

            clips.append(ClipEvent(
                asset_id=slot["asset_id"],
                source_in=slot["source_in"],
                source_out=round(slot["source_in"] + shot_dur, 3),
                timeline_in=round(timeline_pos, 3),
                timeline_out=round(timeline_pos + shot_dur, 3),
                speed=1.0,
                motion_keyframes=keyframes,
                transition_out=TransitionEvent(type=trans_type, duration=trans_dur),
                selection_metadata=slot.get("selection_metadata"),
            ))

            # Caption
            shot_info = shots_info[i] if i < len(shots_info) else {}
            cap_text = shot_info.get("caption", "").strip()
            if cap_text:
                cap_start = round(timeline_pos + 0.15, 3)
                cap_end   = round(timeline_pos + shot_dur * 0.85, 3)
                # Skip caption if the shot is too short to display it
                if cap_end <= cap_start:
                    cap_end = round(timeline_pos + shot_dur - 0.05, 3)
                if cap_end <= cap_start:
                    cap_text = ""   # shot too short — suppress caption

            if cap_text:
                # Determine text_case — cap_preset wins over legacy cap_style
                tc = cap_preset.get("text_case") or (
                    "uppercase" if (cap_style.get("all_caps") or cap_style.get("case") == "uppercase") else "titlecase"
                )
                if tc == "uppercase":
                    cap_text = cap_text.upper()
                elif tc == "lowercase":
                    cap_text = cap_text.lower()
                elif tc == "titlecase":
                    cap_text = cap_text.title()

                position = _CAPTION_POSITION_MAP.get(
                    cap_preset.get("position") or cap_style.get("position", "center"), "center"
                )
                animation = cap_preset.get("animation") or cap_style.get("animation", "pop")
                if animation not in ("none", "fade", "pop", "slide_up", "typewriter"):
                    animation = "pop"
                anim_dur = int(cap_preset.get("animation_duration", 200))
                # font_family: cap_preset wins; fall back to fingerprint's
                # font_family (normalized via _FONT_FAMILY_MAP); then Arial Black
                fp_font_raw = cap_style.get("font_family", "Arial-Black")
                fp_font = _FONT_FAMILY_MAP.get(fp_font_raw, fp_font_raw)
                resolved_font = str(cap_preset.get("font_family") or fp_font or "Arial Black")

                # font_size: cap_preset wins; fall back to fingerprint's
                # font_size_class (e.g. "large" → 80px) then 72
                fp_size = _FONT_SIZE_CLASS_PX.get(
                    cap_style.get("font_size_class", "large"), 72
                )
                resolved_size = int(cap_preset.get("font_size") or fp_size or 72)

                captions.append(CaptionEvent(
                    text=cap_text,
                    start=cap_start,
                    end=cap_end,
                    position=position,
                    animation=animation,
                    case=tc if tc in ("uppercase", "titlecase", "asis") else "titlecase",
                    stroke=cap_style.get("has_stroke", True),
                    # Advanced typography — fingerprint is the fallback source
                    font_family=resolved_font,
                    font_weight=str(cap_preset.get("font_weight", "bold")),
                    text_case=tc,
                    stroke_width=float(cap_preset.get("stroke_width", 3.0)),
                    shadow=bool(cap_preset.get("shadow", False)),
                    tracking=float(cap_preset.get("tracking", 1.0)),
                    animation_duration=anim_dur,
                    font_size=resolved_size,
                    # Reference-matched visual style
                    text_color=str(cap_style.get("text_color", "white")),
                    background_box=bool(cap_style.get("background_box", False)),
                    background_color=str(cap_style.get("background_color", "black")),
                    background_opacity=float(cap_style.get("background_opacity", 0.6)),
                    y_position_percent=int(cap_style.get("y_position_percent", 75)),
                ))

            timeline_pos += shot_dur

        # Build color grade from fingerprint.
        # Treat 0.0 as "not provided" for contrast/saturation/gamma — a zero
        # contrast crushes every pixel to black in the FFmpeg eq filter.
        cg_data = fingerprint.get("color_grade") or fingerprint.get("color_profile") or {}
        color_grade = ColorGrade(
            brightness=cg_data.get("brightness") or 0.0,
            contrast=cg_data.get("contrast") or 1.0,
            saturation=cg_data.get("saturation") or 1.0,
            gamma=cg_data.get("gamma") or 1.0,
            luma_avg=cg_data.get("luma_avg") or 128.0,
            temperature=cg_data.get("temperature") or "neutral",
            black_level=cg_data.get("black_level") or "normal",
        )

        timeline = EditTimeline(
            project_id=project_id or "",
            duration_sec=round(timeline_pos, 3),
            width=width,
            height=height,
            fps=fps,
            clips=clips,
            captions=captions,
            color_grade=color_grade,
            render_style=render_style,
        )

        # ── Compute pacing curve and store on timeline ───────────────────
        try:
            from app.services.music_analysis import compute_pacing_curve, escalation_score
            clip_intensities = []
            for c in clips:
                sm = c.selection_metadata
                if sm and hasattr(sm, "score_breakdown") and sm.score_breakdown:
                    sb = sm.score_breakdown
                    clip_intensities.append(float(getattr(sb, "intensity", 0.5)))
                else:
                    clip_intensities.append(0.5)
            clip_durs = [c.timeline_out - c.timeline_in for c in clips]
            pacing = compute_pacing_curve(clip_durs, clip_intensities)
            esc   = escalation_score(pacing)
            timeline.pacing_metadata = {
                "rhythm_preset":    rhythm_preset,
                "pacing_curve":     pacing,
                "escalation_score": round(esc, 4),
                "clip_count":       len(clips),
            }
        except Exception as exc:
            logger.debug("Pacing curve computation failed: %s", exc)
            timeline.pacing_metadata = {"rhythm_preset": rhythm_preset}

        # Validate before returning
        errors = timeline.validate_timeline()
        if errors:
            logger.warning("Timeline validation warnings:\n  %s", "\n  ".join(errors))

        return timeline

    # ── Private helpers ────────────────────────────────────────────────────

    def _build_shot_sequence(
        self,
        fingerprint: dict[str, Any],
        footage_index: list[dict[str, Any]],
        max_shots: int,
        target_duration_sec: float | None = None,
    ) -> list[float]:
        # Hard floor: anything below 0.5 s is not a real TikTok shot —
        # the LLM may return frame-level timestamps (e.g. 0.083 s ≈ 2 frames)
        # that must be clamped before they propagate to the render engine.
        _MIN_SHOT_SEC = 0.5

        ref_durs_raw = fingerprint.get("shot_durations", [])
        # Clamp each reference duration; discard truly nonsensical values
        ref_durs = [max(float(d), _MIN_SHOT_SEC) for d in ref_durs_raw if float(d) > 0]

        avg_raw = fingerprint.get("avg_shot_duration", 1.5)
        avg = max(float(avg_raw), _MIN_SHOT_SEC)

        ref_shot_count = len(ref_durs) if ref_durs else max(10, round(avg * len(footage_index) * 3))
        target = min(max_shots, max(10, ref_shot_count))

        if not ref_durs:
            base = [avg] * target
        else:
            tiled: list[float] = []
            while len(tiled) < target:
                tiled.extend(ref_durs)
            base = tiled[:target]

        # When target_duration_sec is set, trim or extend the sequence so its
        # sum approximates the target.  We tile / truncate without distorting
        # individual shot durations.
        if target_duration_sec and target_duration_sec > 0:
            avg_dur = max(sum(base) / max(len(base), 1), _MIN_SHOT_SEC)
            needed_shots = max(1, round(target_duration_sec / avg_dur))
            # Rebuild tiled sequence at the new length
            tiled2: list[float] = []
            src = ref_durs if ref_durs else [avg]
            while len(tiled2) < needed_shots:
                tiled2.extend(src)
            base = tiled2[:needed_shots]

        # Filter out any shot durations below 1 frame @30fps — these would
        # produce zero-frame segments and cause FFmpeg "encoder before EOF".
        _MIN_FRAME = 1.0 / 30
        base = [d for d in base if d >= _MIN_FRAME]
        if not base:
            base = [avg] * 10

        return base

    def _snap_to_beats(
        self,
        shot_durations: list[float],
        beat_points: list[float],
        tolerance: float = 0.20,
        offset: float = 0.0,
    ) -> list[float]:
        """Adjust shot durations so cuts land close to beat timestamps.

        Args:
            beat_points: Sorted beat timestamps in seconds.
            tolerance:   Max deviation allowed for a snap (seconds).
            offset:      Seconds added to the snapped time (for chaotic preset).
        """
        if not beat_points or not shot_durations:
            return shot_durations

        import random as _random

        adjusted: list[float] = []
        cursor = 0.0
        for dur in shot_durations:
            ideal_end = cursor + dur
            # Find nearest beat within tolerance
            nearest = min(beat_points, key=lambda b: abs(b - ideal_end))
            if abs(nearest - ideal_end) <= tolerance and nearest > cursor + 0.3:
                snapped = nearest + offset
                # For chaotic mode the offset is random ±offset around the beat
                if offset > 0.0:
                    snapped = nearest + _random.uniform(-offset, offset)
                new_dur = snapped - cursor
                if new_dur > 0.2:   # guard against tiny / negative durations
                    adjusted.append(round(new_dur, 3))
                    cursor = snapped
                    continue
            adjusted.append(dur)
            cursor += dur
        return adjusted

    def _assign_footage(
        self,
        shot_durations: list[float],
        footage_index: list[dict[str, Any]],
        ranker: ClipRanker,
        min_clip_variety: int = 3,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Assign the best-ranked footage segment to each timeline slot.

        Selection criteria (via ClipRanker, in priority order):
          1. Reference pacing fit      (raw_quality × arc_fit)
          2. Motion continuity         (Gaussian penalty for abrupt jumps)
          3. Semantic fit              (CLIP-proxy cosine vs reference style)
          4. Aesthetic quality         (sharpness, exposure, stability)
          5. Visual intensity          (energy at this arc position)
          6. Face/person consistency   (same-person continuity)

        The asset-variety constraint (prefer different source than previous clip)
        is enforced here, outside the ranker, so we keep separation of concerns.

        The ``min_clip_variety`` constraint guarantees at least that many distinct
        source clips appear in the final output — useful for 50+ clip batches where
        the top-ranked segments might all come from a single high-quality clip.
        """
        # Build flat candidate list; inject parent clip quality for aesthetics scoring
        all_moments: list[dict[str, Any]] = [
            {
                **m,
                "asset_id":    clip["asset_id"],
                "clip_path":   clip.get("file_path", clip.get("path", "")),
                "clip_quality": m.get("clip_quality") or clip.get("quality", {}),
            }
            for clip in footage_index
            for m in (clip.get("moments") or clip.get("usable_segments") or [])
        ]

        # Distinct asset ids available
        available_assets = {m["asset_id"] for m in all_moments}
        effective_variety = min(min_clip_variety, len(available_assets))

        n              = len(shot_durations)
        clip_slots:    list[dict[str, Any]] = []
        descriptions:  list[str]            = []
        used_ids:      set[str]             = set()
        asset_use_counts: dict[str, int]    = {}
        prev_segment:  dict[str, Any] | None = None

        for i, shot_dur in enumerate(shot_durations):
            # Slots remaining and variety requirement
            slots_remaining  = n - i
            assets_used      = len(asset_use_counts)
            variety_deficit  = max(0, effective_variety - assets_used)

            ranked = ranker.rank(
                all_moments,
                used_ids     = used_ids,
                shot_dur     = shot_dur,
                prev_segment = prev_segment,
                slot_index   = i,
                total_slots  = n,
            )
            if not ranked:
                break

            # Enforce asset variety
            prev_asset       = prev_segment["asset_id"] if prev_segment else None
            has_alternatives = any(s["asset_id"] != prev_asset for s in ranked)

            # Variety enforcement: if we still need more distinct clips and there
            # are enough slots left, deprioritise already-over-used assets.
            best: dict[str, Any] | None = None
            best_rank: int = 1

            # Build a "freshness-aware" ranking: bias toward unused assets if
            # variety_deficit > 0 and we still have slots left
            if variety_deficit > 0 and slots_remaining > variety_deficit:
                unused_assets = available_assets - set(asset_use_counts.keys())
                for rank_idx, candidate in enumerate(ranked):
                    if candidate["asset_id"] in unused_assets and candidate["asset_id"] != prev_asset:
                        best = candidate
                        best_rank = rank_idx + 1
                        break

            if best is None:
                for rank_idx, candidate in enumerate(ranked):
                    if candidate["asset_id"] != prev_asset or not has_alternatives:
                        best = candidate
                        best_rank = rank_idx + 1
                        break

            if best is None:
                best = ranked[0]
                best_rank = 1

            uid = f"{best['asset_id']}_{best['start']:.3f}"
            used_ids.add(uid)
            prev_segment = best
            asset_use_counts[best["asset_id"]] = asset_use_counts.get(best["asset_id"], 0) + 1

            explain = best.get("_explainability", {})
            clip_slots.append({
                "asset_id":  best["asset_id"],
                "source_in": best["start"],
                "shot_dur":  shot_dur,
                "selection_metadata": {
                    "selected_rank":   best_rank,
                    "total_score":     explain.get("total_score", 0.0),
                    "score_breakdown": explain.get("score_breakdown", {}),
                    "reason":          explain.get("reason", ""),
                    "ranking_profile": explain.get("profile_used"),
                },
            })
            descriptions.append(
                best.get("description", f"{best['asset_id']} [{best['start']:.1f}s]")
            )

        return clip_slots, descriptions

    def _get_captions(
        self,
        fingerprint: dict[str, Any],
        clip_slots: list[dict[str, Any]],
        descriptions: list[str],
        content_hint: str,
    ) -> dict[str, Any]:
        cap_style = fingerprint.get("caption_style") or {}
        max_words = cap_style.get("max_words", 4)
        all_caps = cap_style.get("all_caps", True)
        caps_label = "ALL CAPS" if all_caps else "Title Case"
        cap_desc = (
            f"{caps_label}, "
            f"{cap_style.get('font_size_class', 'large')} font, "
            f"{'stroke outline' if cap_style.get('has_stroke') else 'no stroke'}, "
            f"{cap_style.get('position', 'center')} of screen"
        )

        timeline_text = "\n".join(
            f"  [{i}] {desc} ({slot['shot_dur']:.1f}s)"
            for i, (slot, desc) in enumerate(zip(clip_slots, descriptions))
        )

        prompt = _CAPTION_PROMPT.format(
            tone=fingerprint.get("tone", "engaging"),
            energy_level=fingerprint.get("energy_level", "medium"),
            hook_style=fingerprint.get("hook_style", "bold opener"),
            caption_style_desc=cap_desc,
            max_words=max_words,
            all_caps=all_caps,
            caps_label=caps_label,
            pace=fingerprint.get("pace", "medium"),
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
                "shots": [{"caption": "", "moment_type": "broll"} for _ in clip_slots],
            }
