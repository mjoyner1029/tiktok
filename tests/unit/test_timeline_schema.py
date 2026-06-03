"""Unit tests for app/services/timeline_schema.py — no I/O or subprocess calls."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.timeline_schema import (
    CaptionEvent,
    ClipEvent,
    ColorGrade,
    EditTimeline,
    MotionKeyframe,
    TransitionEvent,
)


# ── Fixtures ──────────────────────────────────────────────────────────────

def _clip(asset_id="a1", source_in=0.0, source_out=2.0, tl_in=0.0, tl_out=2.0, trans_type="hard_cut"):
    return ClipEvent(
        asset_id=asset_id,
        source_in=source_in,
        source_out=source_out,
        timeline_in=tl_in,
        timeline_out=tl_out,
        transition_out=TransitionEvent(type=trans_type, duration=0.0),
    )


def _caption(text="HELLO", start=0.2, end=1.8):
    return CaptionEvent(text=text, start=start, end=end)


def _minimal_timeline(**overrides):
    defaults = dict(
        project_id="proj1",
        duration_sec=4.0,
        clips=[_clip("a1", 0, 2, 0, 2), _clip("a2", 0, 2, 2, 4)],
        captions=[_caption("HOOK", 0.2, 1.8), _caption("CLOSE", 2.2, 3.8)],
    )
    defaults.update(overrides)
    return EditTimeline(**defaults)


# ── ClipEvent ─────────────────────────────────────────────────────────────

class TestClipEvent:
    def test_valid_clip(self):
        c = _clip()
        assert c.asset_id == "a1"
        assert c.source_out > c.source_in

    def test_source_out_before_source_in_rejected(self):
        with pytest.raises(ValidationError, match="source_in.*>=.*source_out"):
            ClipEvent(asset_id="x", source_in=5.0, source_out=3.0,
                      timeline_in=0.0, timeline_out=2.0)

    def test_timeline_out_before_timeline_in_rejected(self):
        with pytest.raises(ValidationError, match="timeline_in.*>=.*timeline_out"):
            ClipEvent(asset_id="x", source_in=0.0, source_out=2.0,
                      timeline_in=3.0, timeline_out=1.0)

    def test_motion_keyframes_optional(self):
        c = _clip()
        assert c.motion_keyframes == []

    def test_motion_keyframes_populated(self):
        c = ClipEvent(
            asset_id="x",
            source_in=0.0, source_out=2.0,
            timeline_in=0.0, timeline_out=2.0,
            motion_keyframes=[MotionKeyframe(t=0.0, scale=1.0), MotionKeyframe(t=2.0, scale=1.08)],
        )
        assert len(c.motion_keyframes) == 2
        assert c.motion_keyframes[1].scale == pytest.approx(1.08)


# ── CaptionEvent ──────────────────────────────────────────────────────────

class TestCaptionEvent:
    def test_valid_caption(self):
        cap = _caption()
        assert cap.text == "HELLO"
        assert cap.end > cap.start

    def test_end_before_start_rejected(self):
        with pytest.raises(ValidationError, match="start.*<.*end|end.*>.*start"):
            CaptionEvent(text="X", start=5.0, end=2.0)

    def test_valid_positions(self):
        for pos in ("top", "center", "bottom"):
            cap = CaptionEvent(text="X", start=0.0, end=1.0, position=pos)
            assert cap.position == pos

    def test_invalid_position_rejected(self):
        with pytest.raises(ValidationError):
            CaptionEvent(text="X", start=0.0, end=1.0, position="middle")


# ── ColorGrade ────────────────────────────────────────────────────────────

class TestColorGrade:
    def test_defaults(self):
        cg = ColorGrade()
        assert cg.brightness == pytest.approx(0.0)
        assert cg.contrast == pytest.approx(1.0)
        assert cg.saturation == pytest.approx(1.0)
        assert cg.gamma == pytest.approx(1.0)
        assert cg.luma_avg == pytest.approx(128.0)

    def test_custom_values(self):
        cg = ColorGrade(brightness=0.05, contrast=1.1, saturation=1.2, gamma=0.9)
        assert cg.brightness == pytest.approx(0.05)
        assert cg.contrast == pytest.approx(1.1)


# ── EditTimeline ──────────────────────────────────────────────────────────

class TestEditTimeline:
    def test_valid_minimal(self):
        tl = _minimal_timeline()
        assert tl.project_id == "proj1"
        assert tl.duration_sec == pytest.approx(4.0)
        assert len(tl.clips) == 2
        assert len(tl.captions) == 2

    def test_version_defaults_to_1(self):
        tl = _minimal_timeline()
        assert tl.version == 1

    def test_resolution_defaults(self):
        tl = _minimal_timeline()
        assert tl.width == 1080
        assert tl.height == 1920
        assert tl.fps == 30

    # ── validate_timeline ────────────────────────────────────────────────

    def test_validate_passes_clean_timeline(self):
        tl = _minimal_timeline()
        errors = tl.validate_timeline()
        assert errors == [], f"Unexpected errors: {errors}"

    def test_validate_detects_duration_mismatch(self):
        tl = _minimal_timeline(duration_sec=3.0)  # clips end at 4.0
        errors = tl.validate_timeline()
        assert any("duration" in e.lower() for e in errors)

    def test_validate_detects_overlapping_clips(self):
        clips = [
            _clip("a1", 0, 3, 0, 3),
            _clip("a2", 0, 2, 2, 4),   # timeline_in=2 overlaps previous (ends at 3)
        ]
        tl = EditTimeline(project_id="p", duration_sec=4.0, clips=clips, captions=[])
        errors = tl.validate_timeline()
        assert any("overlap" in e.lower() for e in errors)

    def test_validate_detects_caption_beyond_duration(self):
        cap = _caption("X", 3.5, 5.0)  # ends after duration 4.0
        tl = _minimal_timeline(captions=[cap])
        errors = tl.validate_timeline()
        assert any("caption" in e.lower() for e in errors)

    def test_validate_empty_clips(self):
        tl = EditTimeline(project_id="p", duration_sec=0.0, clips=[], captions=[])
        errors = tl.validate_timeline()
        assert any("clip" in e.lower() or "empty" in e.lower() for e in errors)

    # ── to_render_spec ────────────────────────────────────────────────────

    def test_to_render_spec_structure(self):
        tl = _minimal_timeline()
        spec = tl.to_render_spec(asset_resolver=lambda aid: f"/footage/{aid}.mp4")
        assert "output" in spec
        assert "tracks" in spec
        assert spec["output"]["width"] == 1080
        assert spec["output"]["height"] == 1920
        assert spec["output"]["fps"] == 30

    def test_to_render_spec_video_track(self):
        tl = _minimal_timeline()
        spec = tl.to_render_spec(asset_resolver=lambda aid: f"/footage/{aid}.mp4")
        video = spec["tracks"]["video"]
        assert len(video) == 2
        assert video[0]["asset_id"] == "a1"
        assert video[0]["source_in"] == pytest.approx(0.0)
        assert video[0]["source_out"] == pytest.approx(2.0)

    def test_to_render_spec_text_track(self):
        tl = _minimal_timeline()
        spec = tl.to_render_spec(asset_resolver=lambda aid: f"/footage/{aid}.mp4")
        text = spec["tracks"]["text"]
        assert len(text) == 2
        assert text[0]["text"] == "HOOK"

    def test_to_render_spec_resolves_asset_paths(self):
        tl = _minimal_timeline()
        spec = tl.to_render_spec(asset_resolver=lambda aid: f"/my/footage/{aid}.mp4")
        paths = [v["source_path"] for v in spec["tracks"]["video"]]
        assert all(p.startswith("/my/footage/") for p in paths)

    # ── round-trip serialization ──────────────────────────────────────────

    def test_json_round_trip(self):
        tl = _minimal_timeline()
        json_str = tl.model_dump_json()
        tl2 = EditTimeline.model_validate_json(json_str)
        assert tl2.project_id == tl.project_id
        assert tl2.duration_sec == pytest.approx(tl.duration_sec)
        assert len(tl2.clips) == len(tl.clips)

    def test_model_copy_does_not_mutate_original(self):
        tl = _minimal_timeline()
        tl2 = tl.model_copy(deep=True, update={"version": 2})
        assert tl.version == 1
        assert tl2.version == 2
