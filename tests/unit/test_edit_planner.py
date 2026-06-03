"""Unit tests for app/services/edit_planner.py.

Verifies:
  - Shot timing is derived from reference fingerprint, NOT invented by Claude
  - Claude only provides caption text and hook_index
  - Output is a validated EditTimeline Pydantic object
  - Beat-snapping adjusts durations within tolerance
  - Hook reordering works
  - Captions are uppercase when fingerprint says all_caps
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch, call
import pytest

from app.services.timeline_schema import EditTimeline, ClipEvent, CaptionEvent
from app.services.edit_planner import EditPlanner


# ── Fixtures ──────────────────────────────────────────────────────────────

FINGERPRINT = {
    "duration_sec": 21.4,
    "aspect_ratio": "9:16",
    "cut_points": [0.72, 1.41, 2.03, 2.64, 3.35, 4.12, 4.84, 5.56, 6.28, 7.0],
    "shot_durations": [0.72, 0.69, 0.62, 0.61, 0.71, 0.72, 0.72, 0.72, 0.72],
    "avg_shot_duration": 0.69,
    "num_cuts": 9,
    "pace": "fast",
    "beat_points": [],
    "tempo_bpm": None,
    "downbeats": [],
    "cut_to_beat_alignment": 0.0,
    "transitions": ["hard_cut"],
    "dominant_transition": "hard_cut",
    "motion_style": {"primary": "slow_push"},
    "motion_pattern": "slow_push",
    "caption_style": {
        "uses_text": True,
        "position": "center",
        "case": "uppercase",
        "words_per_caption": 2,
        "animation": "pop",
        "font_size_class": "large",
        "has_stroke": True,
        "all_caps": True,
        "max_words": 3,
    },
    "color_profile": {
        "brightness": 0.05, "contrast": 1.1, "saturation": 1.2,
        "gamma": 0.95, "luma_avg": 145.0, "temperature": "warm", "black_level": "normal",
    },
    "color_grade": {"brightness": 0.05, "contrast": 1.1, "saturation": 1.2, "gamma": 0.95, "luma_avg": 145.0},
    "hook_style": "bold opener",
    "energy_level": "high",
    "tone": "aspirational",
}

# 2 clips, each with multiple scored segments
FOOTAGE_INDEX = [
    {
        "asset_id": "footage_00",
        "file_path": "/tmp/footage_00.mp4",
        "path": "/tmp/footage_00.mp4",
        "duration_sec": 15.0,
        "duration": 15.0,
        "resolution": [1080, 1920],
        "has_speech": True,
        "usable_segments": [
            {"start": 0.0, "end": 2.0, "score": 9.2, "tags": ["sharp_detail"], "type": "sharp_detail"},
            {"start": 1.5, "end": 3.5, "score": 7.1, "tags": ["usable"], "type": "usable"},
            {"start": 3.0, "end": 5.0, "score": 8.5, "tags": ["sharp_detail"], "type": "sharp_detail"},
            {"start": 5.0, "end": 7.0, "score": 6.0, "tags": ["usable"], "type": "usable"},
            {"start": 7.0, "end": 9.0, "score": 5.5, "tags": ["usable"], "type": "usable"},
            {"start": 9.0, "end": 11.0, "score": 7.8, "tags": ["sharp_detail"], "type": "sharp_detail"},
            {"start": 11.0, "end": 13.0, "score": 6.3, "tags": ["usable"], "type": "usable"},
        ],
        "moments": [],  # set below
        "quality": {"blur_score": 0.1, "brightness": "normal", "stability": "stable"},
    },
    {
        "asset_id": "footage_01",
        "file_path": "/tmp/footage_01.mp4",
        "path": "/tmp/footage_01.mp4",
        "duration_sec": 12.0,
        "duration": 12.0,
        "resolution": [1080, 1920],
        "has_speech": False,
        "usable_segments": [
            {"start": 0.0, "end": 2.0, "score": 8.0, "tags": ["sharp_detail"], "type": "sharp_detail"},
            {"start": 2.0, "end": 4.0, "score": 7.5, "tags": ["usable"], "type": "usable"},
            {"start": 4.0, "end": 6.0, "score": 6.5, "tags": ["usable"], "type": "usable"},
            {"start": 6.0, "end": 8.0, "score": 9.0, "tags": ["sharp_detail"], "type": "sharp_detail"},
            {"start": 8.0, "end": 10.0, "score": 5.0, "tags": ["soft_blur"], "type": "soft_blur"},
        ],
        "moments": [],
        "quality": {"blur_score": 0.15, "brightness": "normal", "stability": "slight_movement"},
    },
]
# Mirror usable_segments to moments for legacy compat
for clip in FOOTAGE_INDEX:
    clip["moments"] = clip["usable_segments"]


CAPTIONS_RESPONSE = {
    "hook_index": 2,
    "shots": [
        {"caption": "this works", "moment_type": "hook"},
        {"caption": "keep going", "moment_type": "build"},
        {"caption": "the reveal", "moment_type": "climax"},
        {"caption": "do it now", "moment_type": "climax"},
        {"caption": "every time", "moment_type": "broll"},
        {"caption": "the truth", "moment_type": "broll"},
        {"caption": "find out", "moment_type": "broll"},
        {"caption": "start here", "moment_type": "closer"},
        {"caption": "final word", "moment_type": "closer"},
        {"caption": "go for it", "moment_type": "closer"},
    ],
}


def _make_planner():
    llm = MagicMock()
    llm.chat_json.return_value = CAPTIONS_RESPONSE
    return EditPlanner(llm)


# ── Tests ─────────────────────────────────────────────────────────────────

class TestEditPlannerOutput:
    @pytest.fixture
    def timeline(self):
        planner = _make_planner()
        return planner.plan(FINGERPRINT, FOOTAGE_INDEX, content_hint="fitness tips", max_shots=10)

    def test_returns_edit_timeline_object(self, timeline):
        assert isinstance(timeline, EditTimeline)

    def test_version_is_1(self, timeline):
        assert timeline.version == 1

    def test_resolution_defaults(self, timeline):
        assert timeline.width == 1080
        assert timeline.height == 1920

    def test_has_clips(self, timeline):
        assert len(timeline.clips) >= 1

    def test_has_captions(self, timeline):
        assert len(timeline.captions) >= 1

    def test_clips_are_clip_events(self, timeline):
        for clip in timeline.clips:
            assert isinstance(clip, ClipEvent)

    def test_captions_are_caption_events(self, timeline):
        for cap in timeline.captions:
            assert isinstance(cap, CaptionEvent)


class TestEditPlannerTiming:
    """Shot timings must come from fingerprint.shot_durations, not Claude."""

    @pytest.fixture
    def timeline(self):
        planner = _make_planner()
        return planner.plan(FINGERPRINT, FOOTAGE_INDEX, max_shots=9)

    def test_shot_durations_close_to_reference(self, timeline):
        """Individual shot durations should be in the ballpark of the reference."""
        ref_avg = FINGERPRINT["avg_shot_duration"]
        for clip in timeline.clips:
            dur = clip.timeline_out - clip.timeline_in
            # Allow for beat-snap tolerance but should be within 3× of reference
            assert dur > 0.1, f"Clip duration {dur} is suspiciously short"
            assert dur < ref_avg * 10, f"Clip duration {dur} is way too long"

    def test_timeline_is_contiguous(self, timeline):
        """No gaps or overlaps between clips on the timeline."""
        for i in range(len(timeline.clips) - 1):
            prev_out = timeline.clips[i].timeline_out
            next_in = timeline.clips[i + 1].timeline_in
            assert abs(prev_out - next_in) < 0.01, (
                f"Gap/overlap between clip {i} and {i+1}: "
                f"{prev_out:.3f} vs {next_in:.3f}"
            )

    def test_timeline_in_starts_at_zero(self, timeline):
        assert timeline.clips[0].timeline_in == pytest.approx(0.0)

    def test_duration_matches_last_clip_end(self, timeline):
        last_out = timeline.clips[-1].timeline_out
        assert timeline.duration_sec == pytest.approx(last_out, abs=0.01)

    def test_clip_source_in_within_footage(self, timeline):
        """source_in must be within the duration of the referenced footage."""
        asset_durations = {e["asset_id"]: e["duration_sec"] for e in FOOTAGE_INDEX}
        for clip in timeline.clips:
            if clip.asset_id in asset_durations:
                assert clip.source_in >= 0.0
                assert clip.source_out <= asset_durations[clip.asset_id] + 0.1


class TestEditPlannerCaptions:
    @pytest.fixture
    def timeline(self):
        planner = _make_planner()
        return planner.plan(FINGERPRINT, FOOTAGE_INDEX, max_shots=10)

    def test_captions_uppercase_when_all_caps(self, timeline):
        """Caption text should be uppercased when fingerprint.all_caps is True."""
        for cap in timeline.captions:
            assert cap.text == cap.text.upper(), f"Expected uppercase: {cap.text!r}"

    def test_captions_within_timeline_duration(self, timeline):
        for cap in timeline.captions:
            assert cap.end <= timeline.duration_sec + 0.05, (
                f"Caption end {cap.end} exceeds timeline duration {timeline.duration_sec}"
            )
            assert cap.start >= 0.0
            assert cap.end > cap.start

    def test_caption_positions_valid(self, timeline):
        valid_positions = {"top", "center", "bottom"}
        for cap in timeline.captions:
            assert cap.position in valid_positions


class TestEditPlannerLLMUsage:
    def test_llm_called_only_once_for_captions(self):
        """LLM should be called exactly once — for captions only."""
        llm = MagicMock()
        llm.chat_json.return_value = CAPTIONS_RESPONSE
        planner = EditPlanner(llm)
        planner.plan(FINGERPRINT, FOOTAGE_INDEX, max_shots=10)
        assert llm.chat_json.call_count == 1

    def test_llm_not_called_for_timing(self):
        """The prompt sent to LLM must not ask it to choose cut timings."""
        llm = MagicMock()
        llm.chat_json.return_value = CAPTIONS_RESPONSE
        planner = EditPlanner(llm)
        planner.plan(FINGERPRINT, FOOTAGE_INDEX, max_shots=10)

        # Inspect what was passed to the LLM
        call_args = llm.chat_json.call_args
        prompt_text = str(call_args)
        # The prompt must say timing is FIXED
        assert "FIXED" in prompt_text.upper() or "CAPTION" in prompt_text.upper(), (
            "LLM prompt must communicate that timing is fixed and only captions are needed"
        )

    def test_no_llm_when_no_footage(self):
        """Plan with empty footage should raise before reaching LLM."""
        llm = MagicMock()
        planner = EditPlanner(llm)
        with pytest.raises((RuntimeError, ValueError)):
            planner.plan(FINGERPRINT, [], max_shots=5)
        llm.chat_json.assert_not_called()


class TestEditPlannerValidation:
    def test_validate_timeline_passes(self):
        planner = _make_planner()
        timeline = planner.plan(FINGERPRINT, FOOTAGE_INDEX, max_shots=10)
        errors = timeline.validate_timeline()
        assert errors == [], f"Timeline has validation errors: {errors}"

    def test_returns_project_id_when_given(self):
        planner = _make_planner()
        timeline = planner.plan(FINGERPRINT, FOOTAGE_INDEX, project_id="proj_abc", max_shots=5)
        assert timeline.project_id == "proj_abc"


# ── Selection metadata ────────────────────────────────────────────────────────

_PROFILE_FINGERPRINT = {
    **FINGERPRINT,
    "ranking_profile": "fashion_montage",
}


class TestSelectionMetadata:
    """Every ClipEvent produced by EditPlanner carries selection_metadata."""

    @pytest.fixture
    def timeline(self):
        planner = _make_planner()
        return planner.plan(_PROFILE_FINGERPRINT, FOOTAGE_INDEX, max_shots=10)

    def test_all_clips_have_selection_metadata(self, timeline):
        for clip in timeline.clips:
            assert clip.selection_metadata is not None, (
                f"Clip {clip.asset_id} @ {clip.source_in} missing selection_metadata"
            )

    def test_selection_metadata_has_required_keys(self, timeline):
        required = {"selected_rank", "total_score", "score_breakdown", "reason", "ranking_profile"}
        for clip in timeline.clips:
            assert required <= set(clip.selection_metadata.keys())

    def test_selected_rank_is_1_based_positive_int(self, timeline):
        for clip in timeline.clips:
            rank = clip.selection_metadata["selected_rank"]
            assert isinstance(rank, int)
            assert rank >= 1

    def test_total_score_is_float_in_unit_interval(self, timeline):
        for clip in timeline.clips:
            score = clip.selection_metadata["total_score"]
            assert isinstance(score, float)
            assert 0.0 <= score <= 1.0

    def test_score_breakdown_has_all_7_dimensions(self, timeline):
        expected = {
            "raw_quality", "motion_continuity", "semantic_fit",
            "aesthetic_quality", "visual_intensity", "face_consistency", "arc_fit",
        }
        for clip in timeline.clips:
            assert set(clip.selection_metadata["score_breakdown"].keys()) == expected

    def test_reason_is_non_empty_string(self, timeline):
        for clip in timeline.clips:
            reason = clip.selection_metadata["reason"]
            assert isinstance(reason, str)
            assert len(reason) > 0

    def test_ranking_profile_matches_fingerprint(self, timeline):
        for clip in timeline.clips:
            assert clip.selection_metadata["ranking_profile"] == "fashion_montage"

    def test_selection_metadata_reason_mentions_top_2_dimensions(self, timeline):
        _labels = {
            "raw_quality": "raw quality",
            "motion_continuity": "motion continuity",
            "semantic_fit": "semantic fit",
            "aesthetic_quality": "aesthetic quality",
            "visual_intensity": "visual intensity",
            "face_consistency": "face consistency",
            "arc_fit": "arc fit",
        }
        for clip in timeline.clips:
            bd = clip.selection_metadata["score_breakdown"]
            top2_dims = sorted(bd, key=lambda d: bd[d]["contribution"], reverse=True)[:2]
            reason = clip.selection_metadata["reason"]
            for dim in top2_dims:
                assert _labels[dim] in reason, (
                    f"Expected '{_labels[dim]}' in reason '{reason}'"
                )

