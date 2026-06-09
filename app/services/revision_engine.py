"""
RevisionEngine — applies natural-language feedback to an existing EditTimeline.

Input:
  current_timeline  — EditTimeline (loaded from timeline_vN.json)
  reference_fingerprint — dict
  footage_index     — list[dict]
  feedback          — str (e.g. "make it faster", "use more closeups")

Output:
  new EditTimeline (version incremented)

Supported feedback patterns (case-insensitive, partial match):
  "faster" / "speed up" / "quicker"   → reduce all shot durations by 25%
  "slower" / "slow down"              → increase all shot durations by 25%
  "less text" / "fewer captions"      → drop bottom 50% of captions
  "more text" / "more captions"       → let LLM add captions to captionless shots
  "closeup" / "close-up"             → prefer sharp_detail moments from footage
  "cinematic" / "cinematic look"      → switch to slow_push motion + dissolve transitions
  "fast cuts" / "hard cuts"           → switch to hard_cut transitions, 20% shorter shots
  "replace first" / "swap first"      → reassign the first clip to next-best moment
  "match reference" / "closer to ref" → fully re-plan from fingerprint
  Anything else                       → LLM interprets and patches the timeline JSON
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.services.timeline_schema import (
    CaptionEvent,
    ClipEvent,
    ColorGrade,
    EditTimeline,
    MotionKeyframe,
    TransitionEvent,
)

logger = logging.getLogger(__name__)

_REVISION_SYSTEM = (
    "You are an expert TikTok video editor. You receive an EditTimeline JSON and "
    "user feedback. Apply the feedback precisely and return ONLY the modified JSON."
)

_REVISION_PROMPT = """\
Current EditTimeline (JSON):
{timeline_json}

User feedback:
{feedback}

Apply the feedback to the timeline. Rules:
- Do not change asset_id or source paths
- Keep timeline_in / timeline_out sequential and non-overlapping
- Captions must stay within video duration
- Return the complete modified EditTimeline JSON only
"""


class RevisionEngine:
    """Apply user feedback to an EditTimeline and return a new version."""

    def __init__(self, llm):
        self.llm = llm

    def revise(
        self,
        timeline: EditTimeline,
        feedback: str,
        footage_index: list[dict[str, Any]] | None = None,
        fingerprint: dict[str, Any] | None = None,
    ) -> EditTimeline:
        """
        Return a new EditTimeline with incremented version number.
        Never mutates the input timeline.
        """
        fb = feedback.strip().lower()
        new = timeline.model_copy(deep=True)
        new.version = timeline.version + 1

        # ── Rule-based patches (fast, deterministic) ──────────────────────
        if any(k in fb for k in ("faster", "speed up", "quicker", "fast cut")):
            new = self._change_pace(new, factor=0.75)
        elif any(k in fb for k in ("slower", "slow down")):
            new = self._change_pace(new, factor=1.3)

        if any(k in fb for k in ("less text", "fewer caption", "remove caption", "no text")):
            new = self._thin_captions(new, keep_fraction=0.5)

        if any(k in fb for k in ("cinematic", "film look")):
            new = self._apply_cinematic(new)

        if any(k in fb for k in ("hard cut", "fast cut")):
            new = self._switch_transitions(new, trans_type="hard_cut", duration=0.0)

        if any(k in fb for k in ("replace first", "swap first", "change first")):
            if footage_index:
                new = self._replace_clip(new, clip_index=0, footage_index=footage_index)

        if any(k in fb for k in ("closeup", "close-up", "close up")):
            if footage_index:
                new = self._prefer_closeups(new, footage_index)

        if any(k in fb for k in ("match reference", "closer to ref", "more like ref")) and fingerprint:
            from app.services.edit_planner import EditPlanner
            new = EditPlanner(self.llm).plan(
                fingerprint, footage_index or [],
                project_id=timeline.project_id,
                width=timeline.width,
                height=timeline.height,
                fps=timeline.fps,
            )
            new.version = timeline.version + 1
            return new

        # ── LLM fallback for unrecognised feedback ─────────────────────────
        if not self._was_patched(timeline, new):
            new = self._llm_patch(timeline, feedback)
            new.version = timeline.version + 1

        # Re-validate
        errors = new.validate_timeline()
        if errors:
            logger.warning("Revised timeline has %d validation issue(s):\n  %s",
                           len(errors), "\n  ".join(errors))

        return new

    # ── Rule-based operations ───────────────────────────────────────────────

    def _change_pace(self, tl: EditTimeline, factor: float) -> EditTimeline:
        """Scale all shot durations while keeping timeline contiguous."""
        cursor = 0.0
        new_clips: list[ClipEvent] = []
        for clip in tl.clips:
            old_dur = clip.timeline_out - clip.timeline_in
            new_dur = max(0.3, round(old_dur * factor, 3))
            # Adjust source_out proportionally
            src_dur = clip.source_out - clip.source_in
            new_src_dur = max(0.3, round(src_dur * factor, 3))
            new_clips.append(clip.model_copy(update={
                "timeline_in": round(cursor, 3),
                "timeline_out": round(cursor + new_dur, 3),
                "source_out": round(clip.source_in + new_src_dur, 3),
            }))
            cursor += new_dur

        new_dur_total = cursor
        # Scale caption timings proportionally
        scale = new_dur_total / tl.duration_sec if tl.duration_sec else 1.0
        new_captions = [
            cap.model_copy(update={
                "start": round(cap.start * scale, 3),
                "end": min(round(cap.end * scale, 3), new_dur_total - 0.05),
            })
            for cap in tl.captions
            if round(cap.start * scale, 3) < new_dur_total
        ]

        return tl.model_copy(update={
            "clips": new_clips,
            "captions": new_captions,
            "duration_sec": round(new_dur_total, 3),
        })

    def _thin_captions(self, tl: EditTimeline, keep_fraction: float = 0.5) -> EditTimeline:
        n = max(1, round(len(tl.captions) * keep_fraction))
        # Keep every Nth caption (spread throughout)
        kept = tl.captions[:n]
        return tl.model_copy(update={"captions": kept})

    def _apply_cinematic(self, tl: EditTimeline) -> EditTimeline:
        """Dissolve transitions + slow push-ins + crushed black level."""
        new_clips = [
            clip.model_copy(update={
                "transition_out": TransitionEvent(type="dissolve", duration=0.45),
                "motion_keyframes": [
                    MotionKeyframe(t=0.0, scale=1.0),
                    MotionKeyframe(t=clip.timeline_out - clip.timeline_in, scale=1.06),
                ],
            })
            for clip in tl.clips
        ]
        new_grade = tl.color_grade.model_copy(update={
            "contrast": min(tl.color_grade.contrast * 1.1, 1.5),
            "saturation": max(tl.color_grade.saturation * 0.9, 0.7),
            "black_level": "crushed",
        })
        return tl.model_copy(update={"clips": new_clips, "color_grade": new_grade})

    def _switch_transitions(self, tl: EditTimeline, trans_type: str, duration: float) -> EditTimeline:
        new_clips = [
            clip.model_copy(update={
                "transition_out": TransitionEvent(type=trans_type, duration=duration)
            })
            for clip in tl.clips
        ]
        return tl.model_copy(update={"clips": new_clips})

    def _replace_clip(
        self,
        tl: EditTimeline,
        clip_index: int,
        footage_index: list[dict[str, Any]],
    ) -> EditTimeline:
        """Swap clip at clip_index with the next best unused moment."""
        if clip_index >= len(tl.clips):
            return tl

        current = tl.clips[clip_index]
        used_asset_starts = {
            (c.asset_id, c.source_in) for c in tl.clips
        }

        all_moments: list[dict[str, Any]] = sorted(
            [
                {**m, "asset_id": clip["asset_id"]}
                for clip in footage_index
                for m in (clip.get("moments") or clip.get("usable_segments") or [])
            ],
            key=lambda x: -x["score"],
        )

        shot_dur = current.source_out - current.source_in
        for m in all_moments:
            if (m["asset_id"], m["start"]) in used_asset_starts:
                continue
            if m["end"] - m["start"] < shot_dur - 0.05:
                continue
            new_clip = current.model_copy(update={
                "asset_id": m["asset_id"],
                "source_in": m["start"],
                "source_out": round(m["start"] + shot_dur, 3),
            })
            new_clips = list(tl.clips)
            new_clips[clip_index] = new_clip
            return tl.model_copy(update={"clips": new_clips})

        logger.warning("_replace_clip: no suitable alternative found for clip %d", clip_index)
        return tl

    def _prefer_closeups(
        self,
        tl: EditTimeline,
        footage_index: list[dict[str, Any]],
    ) -> EditTimeline:
        """Re-assign clips to moments tagged 'sharp_detail' where possible."""
        high_quality: list[dict[str, Any]] = sorted(
            [
                {**m, "asset_id": clip["asset_id"]}
                for clip in footage_index
                for m in (clip.get("moments") or clip.get("usable_segments") or [])
                if "sharp_detail" in m.get("tags", [m.get("type", "")])
            ],
            key=lambda x: -x["score"],
        )

        if not high_quality:
            return tl

        used: set[tuple[str, float]] = set()
        new_clips: list[ClipEvent] = []
        hq_iter = iter(high_quality)

        for clip in tl.clips:
            shot_dur = clip.source_out - clip.source_in
            best = None
            for m in high_quality:
                if (m["asset_id"], m["start"]) in used:
                    continue
                if m["end"] - m["start"] < shot_dur - 0.05:
                    continue
                best = m
                break
            if best:
                used.add((best["asset_id"], best["start"]))
                new_clips.append(clip.model_copy(update={
                    "asset_id": best["asset_id"],
                    "source_in": best["start"],
                    "source_out": round(best["start"] + shot_dur, 3),
                }))
            else:
                new_clips.append(clip)

        return tl.model_copy(update={"clips": new_clips})

    def _was_patched(self, original: EditTimeline, revised: EditTimeline) -> bool:
        """Check whether any rule-based patch actually changed the timeline."""
        return original.model_dump() != revised.model_dump()

    def _llm_patch(self, tl: EditTimeline, feedback: str) -> EditTimeline:
        """Ask Claude to modify the timeline JSON based on free-form feedback."""
        tl_json = tl.model_dump_json(indent=2)
        prompt = _REVISION_PROMPT.format(timeline_json=tl_json, feedback=feedback)

        try:
            raw = self.llm.chat(
                _REVISION_SYSTEM, prompt,
                max_tokens=8192,
                response_format="json",
            )
            if not isinstance(raw, dict):
                from app.services.llm_client import _strip_markdown_fences
                import json as _json
                raw = _json.loads(_strip_markdown_fences(str(raw)))
            return EditTimeline.model_validate(raw)
        except Exception as exc:
            logger.warning("LLM revision failed (%s) — returning original timeline", exc)
            return tl


# ── Project artifact helpers ───────────────────────────────────────────────

from datetime import datetime, timezone


def save_project_artifacts(
    project_dir: Path,
    fingerprint: dict[str, Any],
    footage_index: list[dict[str, Any]],
    timeline: EditTimeline,
    embedding_status: dict[str, Any] | None = None,
) -> None:
    """Write reference_fingerprint.json, footage_index.json, timeline_vN.json, logs.json."""
    project_dir.mkdir(parents=True, exist_ok=True)

    (project_dir / "reference_fingerprint.json").write_text(
        json.dumps(fingerprint, indent=2, default=str)
    )
    (project_dir / "footage_index.json").write_text(
        json.dumps(footage_index, indent=2, default=str)
    )
    version_file = project_dir / f"timeline_v{timeline.version}.json"
    version_file.write_text(timeline.model_dump_json(indent=2))

    # Human-readable summary of what was produced
    validation_errors = timeline.validate_timeline()
    logs = {
        "project_id": project_dir.name,
        "saved_at": datetime.now(tz=timezone.utc).isoformat(),
        "timeline_version": timeline.version,
        "fingerprint_summary": {
            "num_cuts": fingerprint.get("num_cuts"),
            "avg_shot_duration": fingerprint.get("avg_shot_duration"),
            "pace": fingerprint.get("pace"),
            "dominant_transition": fingerprint.get("dominant_transition"),
            "energy_level": fingerprint.get("energy_level"),
            "tempo_bpm": fingerprint.get("tempo_bpm"),
            "beat_points_count": len(fingerprint.get("beat_points") or []),
        },
        "footage_summary": {
            "num_clips": len(footage_index),
            "total_segments": sum(
                len(e.get("usable_segments") or e.get("moments") or [])
                for e in footage_index
            ),
            "clips": [
                {
                    "asset_id": e.get("asset_id"),
                    "duration_sec": e.get("duration_sec", e.get("duration")),
                    "segments": len(e.get("usable_segments") or e.get("moments") or []),
                }
                for e in footage_index
            ],
        },
        "timeline_summary": {
            "duration_sec": timeline.duration_sec,
            "num_clips": len(timeline.clips),
            "num_captions": len(timeline.captions),
            "width": timeline.width,
            "height": timeline.height,
            "fps": timeline.fps,
        },
        "validation_errors": validation_errors,
    }
    if embedding_status is not None:
        logs["embedding_status"] = embedding_status
    (project_dir / "logs.json").write_text(
        json.dumps(logs, indent=2, default=str)
    )
    logger.info("Project artifacts saved to %s (timeline v%d)", project_dir, timeline.version)


def load_project(project_dir: Path) -> dict[str, Any]:
    """Load all artifacts from a project directory."""
    result: dict[str, Any] = {"project_id": project_dir.name}

    fp_file = project_dir / "reference_fingerprint.json"
    if fp_file.exists():
        result["fingerprint"] = json.loads(fp_file.read_text())

    fi_file = project_dir / "footage_index.json"
    if fi_file.exists():
        result["footage_index"] = json.loads(fi_file.read_text())

    # Find highest version timeline
    versions = sorted(project_dir.glob("timeline_v*.json"))
    if versions:
        result["timeline"] = EditTimeline.model_validate_json(versions[-1].read_text())
        result["timeline_version"] = int(
            re.search(r"v(\d+)", versions[-1].stem).group(1)
        )

    return result
