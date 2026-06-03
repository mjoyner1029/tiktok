"""Unit tests for app/services/reference_analyzer.py.

All ffmpeg/ffprobe calls and the LLM are mocked so no real video or API key is
needed.  We verify that:
  - real measurable data (cut_points, shot_durations, avg_shot_duration,
    beat_points, color_profile) are populated from ffprobe/ffmpeg output
  - Claude is only called for qualitative style (captions, transitions, energy)
  - Claude is never asked to invent cut timings
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock, Mock
import json
import pytest

from app.services.reference_analyzer import (
    ReferenceAnalyzer,
    _pace_label,
    _beat_alignment,
    _score_profile,
    infer_style_profile,
)

# ── Mock data representing ffprobe + ffmpeg output ─────────────────────────

MOCK_MEDIA_INFO = {
    "duration_sec": 21.4,
    "width": 1080,
    "height": 1920,
    "fps": 30.0,
    "has_video": True,
    "has_audio": True,
    "file_size_bytes": 5_000_000,
    "format_name": "mov,mp4",
    "bit_rate": 3_000_000,
}

MOCK_VISUAL_STYLE = {
    "cut_timestamps": [0.72, 1.41, 2.03, 2.64, 3.35, 4.12],
    "avg_cut_duration_sec": 0.68,
    "num_cuts": 6,
    "color_grade": {
        "brightness": 0.05,
        "contrast": 1.1,
        "saturation": 1.2,
        "gamma": 0.95,
        "luma_avg": 145.0,
    },
}

MOCK_CLAUDE_RESPONSE = {
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
    "hook_style": "bold opening statement",
    "transitions": ["hard_cut", "flash_cut"],
    "dominant_transition": "hard_cut",
    "motion_style": {"primary": "slow_push"},
    "energy_level": "high",
    "tone": "aspirational",
}


# ── Helpers ───────────────────────────────────────────────────────────────

def _make_analyzer():
    llm = MagicMock()
    llm.message.return_value = json.dumps(MOCK_CLAUDE_RESPONSE)
    return ReferenceAnalyzer(llm)


# ── _pace_label ───────────────────────────────────────────────────────────

class TestPaceLabel:
    def test_ultra_fast(self):
        assert _pace_label(0.3) == "ultra-fast"

    def test_fast(self):
        assert _pace_label(0.8) == "fast"

    def test_medium(self):
        assert _pace_label(1.8) == "medium"

    def test_slow(self):
        assert _pace_label(3.5) == "slow"

    def test_boundary_fast(self):
        assert _pace_label(1.2) == "medium"   # boundary is not "fast"


# ── _beat_alignment ───────────────────────────────────────────────────────

class TestBeatAlignment:
    def test_perfect_alignment(self):
        cuts = [0.0, 0.5, 1.0]
        beats = [0.0, 0.5, 1.0]
        assert _beat_alignment(cuts, beats) == pytest.approx(1.0)

    def test_no_alignment(self):
        cuts = [0.25, 0.75]
        beats = [0.0, 0.5, 1.0]
        # 0.25 is 0.25 away from 0.0 and 0.5 — outside tolerance of 0.15
        assert _beat_alignment(cuts, beats) == pytest.approx(0.0)

    def test_partial_alignment(self):
        cuts = [0.0, 0.5, 0.9]  # 0.9 is near beat 1.0 within 0.15
        beats = [0.0, 0.5, 1.0]
        result = _beat_alignment(cuts, beats)
        assert 0.0 < result <= 1.0

    def test_empty_inputs(self):
        assert _beat_alignment([], [0.5]) == pytest.approx(0.0)
        assert _beat_alignment([0.5], []) == pytest.approx(0.0)


# ── ReferenceAnalyzer.analyze_file ────────────────────────────────────────

class TestReferenceAnalyzerFile:
    @pytest.fixture
    def analyzer(self):
        return _make_analyzer()

    def _patch_analyze(self, analyzer):
        return [
            patch("app.services.reference_analyzer.get_media_info", return_value=MOCK_MEDIA_INFO),
            patch("app.services.reference_analyzer.extract_visual_style", return_value=MOCK_VISUAL_STYLE),
            patch("app.services.reference_analyzer._detect_beats", return_value={
                "beat_points": [0.31, 0.61, 0.92, 1.23],
                "tempo_bpm": 120.0,
                "downbeats": [0.31, 1.23],
            }),
            patch.object(analyzer, "_vision_analyze", return_value=MOCK_CLAUDE_RESPONSE),
        ]

    def test_returns_real_cut_points(self, analyzer):
        with (
            patch("app.services.reference_analyzer.get_media_info", return_value=MOCK_MEDIA_INFO),
            patch("app.services.reference_analyzer.extract_visual_style", return_value=MOCK_VISUAL_STYLE),
            patch("app.services.reference_analyzer._detect_beats", return_value={}),
            patch.object(analyzer, "_vision_analyze", return_value=MOCK_CLAUDE_RESPONSE),
        ):
            fp = analyzer.analyze_file("/fake/video.mp4")

        assert fp["cut_points"] == MOCK_VISUAL_STYLE["cut_timestamps"]
        assert len(fp["cut_points"]) == 6

    def test_returns_real_shot_durations(self, analyzer):
        with (
            patch("app.services.reference_analyzer.get_media_info", return_value=MOCK_MEDIA_INFO),
            patch("app.services.reference_analyzer.extract_visual_style", return_value=MOCK_VISUAL_STYLE),
            patch("app.services.reference_analyzer._detect_beats", return_value={}),
            patch.object(analyzer, "_vision_analyze", return_value=MOCK_CLAUDE_RESPONSE),
        ):
            fp = analyzer.analyze_file("/fake/video.mp4")

        # shot_durations derived from cut_points, not invented
        assert isinstance(fp["shot_durations"], list)
        assert len(fp["shot_durations"]) >= 1
        assert fp["avg_shot_duration"] == pytest.approx(MOCK_VISUAL_STYLE["avg_cut_duration_sec"])

    def test_returns_beat_points(self, analyzer):
        beat_data = {
            "beat_points": [0.31, 0.61, 0.92],
            "tempo_bpm": 120.0,
            "downbeats": [0.31],
        }
        with (
            patch("app.services.reference_analyzer.get_media_info", return_value=MOCK_MEDIA_INFO),
            patch("app.services.reference_analyzer.extract_visual_style", return_value=MOCK_VISUAL_STYLE),
            patch("app.services.reference_analyzer._detect_beats", return_value=beat_data),
            patch.object(analyzer, "_vision_analyze", return_value=MOCK_CLAUDE_RESPONSE),
        ):
            fp = analyzer.analyze_file("/fake/video.mp4")

        assert fp["beat_points"] == [0.31, 0.61, 0.92]
        assert fp["tempo_bpm"] == pytest.approx(120.0)
        assert fp["downbeats"] == [0.31]

    def test_returns_color_profile_from_ffmpeg(self, analyzer):
        with (
            patch("app.services.reference_analyzer.get_media_info", return_value=MOCK_MEDIA_INFO),
            patch("app.services.reference_analyzer.extract_visual_style", return_value=MOCK_VISUAL_STYLE),
            patch("app.services.reference_analyzer._detect_beats", return_value={}),
            patch.object(analyzer, "_vision_analyze", return_value=MOCK_CLAUDE_RESPONSE),
        ):
            fp = analyzer.analyze_file("/fake/video.mp4")

        cp = fp["color_profile"]
        # Values come from MOCK_VISUAL_STYLE["color_grade"], not Claude
        assert cp["brightness"] == pytest.approx(MOCK_VISUAL_STYLE["color_grade"]["brightness"])
        assert cp["contrast"] == pytest.approx(MOCK_VISUAL_STYLE["color_grade"]["contrast"])
        assert cp["luma_avg"] == pytest.approx(MOCK_VISUAL_STYLE["color_grade"]["luma_avg"])

    def test_returns_aspect_ratio(self, analyzer):
        with (
            patch("app.services.reference_analyzer.get_media_info", return_value=MOCK_MEDIA_INFO),
            patch("app.services.reference_analyzer.extract_visual_style", return_value=MOCK_VISUAL_STYLE),
            patch("app.services.reference_analyzer._detect_beats", return_value={}),
            patch.object(analyzer, "_vision_analyze", return_value=MOCK_CLAUDE_RESPONSE),
        ):
            fp = analyzer.analyze_file("/fake/video.mp4")

        assert fp["aspect_ratio"] == "9:16"

    def test_pace_derived_from_avg_shot(self, analyzer):
        with (
            patch("app.services.reference_analyzer.get_media_info", return_value=MOCK_MEDIA_INFO),
            patch("app.services.reference_analyzer.extract_visual_style", return_value=MOCK_VISUAL_STYLE),
            patch("app.services.reference_analyzer._detect_beats", return_value={}),
            patch.object(analyzer, "_vision_analyze", return_value=MOCK_CLAUDE_RESPONSE),
        ):
            fp = analyzer.analyze_file("/fake/video.mp4")

        # avg_shot=0.68 → should be "fast"
        assert fp["pace"] == "fast"

    def test_caption_style_comes_from_claude(self, analyzer):
        with (
            patch("app.services.reference_analyzer.get_media_info", return_value=MOCK_MEDIA_INFO),
            patch("app.services.reference_analyzer.extract_visual_style", return_value=MOCK_VISUAL_STYLE),
            patch("app.services.reference_analyzer._detect_beats", return_value={}),
            patch.object(analyzer, "_vision_analyze", return_value=MOCK_CLAUDE_RESPONSE),
        ):
            fp = analyzer.analyze_file("/fake/video.mp4")

        # Caption style is qualitative — comes from Claude Vision
        assert fp["caption_style"]["position"] == "center"
        assert fp["caption_style"]["case"] == "uppercase"

    def test_claude_not_called_for_cut_timing(self, analyzer):
        """Claude must not invent cut timestamps — those always come from ffmpeg."""
        call_count = {"n": 0}

        def mock_vision(video_path, cuts, duration, avg_shot):
            call_count["n"] += 1
            # Confirm cuts were already computed before Claude is called
            assert isinstance(cuts, list), "cuts must be pre-computed"
            return MOCK_CLAUDE_RESPONSE

        with (
            patch("app.services.reference_analyzer.get_media_info", return_value=MOCK_MEDIA_INFO),
            patch("app.services.reference_analyzer.extract_visual_style", return_value=MOCK_VISUAL_STYLE),
            patch("app.services.reference_analyzer._detect_beats", return_value={}),
            patch.object(analyzer, "_vision_analyze", side_effect=mock_vision),
        ):
            fp = analyzer.analyze_file("/fake/video.mp4")

        assert call_count["n"] == 1
        # Timing came from ffmpeg, not Claude
        assert fp["cut_points"] == MOCK_VISUAL_STYLE["cut_timestamps"]

    def test_graceful_fallback_when_no_beats(self, analyzer):
        with (
            patch("app.services.reference_analyzer.get_media_info", return_value=MOCK_MEDIA_INFO),
            patch("app.services.reference_analyzer.extract_visual_style", return_value=MOCK_VISUAL_STYLE),
            patch("app.services.reference_analyzer._detect_beats", return_value={}),
            patch.object(analyzer, "_vision_analyze", return_value=MOCK_CLAUDE_RESPONSE),
        ):
            fp = analyzer.analyze_file("/fake/video.mp4")

        assert fp["beat_points"] == []
        assert fp["tempo_bpm"] is None
        assert fp["cut_to_beat_alignment"] == pytest.approx(0.0)

    def test_required_keys_present(self, analyzer):
        required = [
            "duration_sec", "aspect_ratio", "cut_points", "shot_durations",
            "avg_shot_duration", "num_cuts", "pace", "beat_points",
            "tempo_bpm", "downbeats", "cut_to_beat_alignment",
            "transitions", "dominant_transition", "motion_style",
            "caption_style", "color_profile", "hook_style",
            "energy_level", "tone",
        ]
        with (
            patch("app.services.reference_analyzer.get_media_info", return_value=MOCK_MEDIA_INFO),
            patch("app.services.reference_analyzer.extract_visual_style", return_value=MOCK_VISUAL_STYLE),
            patch("app.services.reference_analyzer._detect_beats", return_value={}),
            patch.object(analyzer, "_vision_analyze", return_value=MOCK_CLAUDE_RESPONSE),
        ):
            fp = analyzer.analyze_file("/fake/video.mp4")

        missing = [k for k in required if k not in fp]
        assert missing == [], f"Missing fingerprint keys: {missing}"

    def test_style_profile_keys_present(self, analyzer):
        """analyze_file must include the 5 inferred style profile fields."""
        required = ["ranking_profile", "faces_central", "subject_focus", "content_type", "style_tags"]
        with (
            patch("app.services.reference_analyzer.get_media_info", return_value=MOCK_MEDIA_INFO),
            patch("app.services.reference_analyzer.extract_visual_style", return_value=MOCK_VISUAL_STYLE),
            patch("app.services.reference_analyzer._detect_beats", return_value={}),
            patch.object(analyzer, "_vision_analyze", return_value=MOCK_CLAUDE_RESPONSE),
        ):
            fp = analyzer.analyze_file("/fake/video.mp4")

        missing = [k for k in required if k not in fp]
        assert missing == [], f"Missing style profile keys: {missing}"


# ── Style profile inference ────────────────────────────────────────────────

_BASE_FP = {
    "avg_shot_duration": 1.0,
    "pace": "fast",
    "cut_to_beat_alignment": 0.0,
    "beat_points": [],
    "tempo_bpm": None,
    "motion_style": {"primary": "slow_push"},
    "energy_level": "medium",
}


class TestInferStyleProfile:
    """infer_style_profile() classifies each of the 6 profiles correctly."""

    def test_music_video_beat_alignment(self):
        fp = {
            **_BASE_FP,
            "cut_to_beat_alignment": 0.72,
            "beat_points": [0.3, 0.6, 0.9, 1.2, 1.5, 1.8],
            "tempo_bpm": 128.0,
            "energy_level": "high",
        }
        result = infer_style_profile(fp)
        assert result["ranking_profile"] == "music_video"
        assert result["content_type"] == "music_video"
        assert "visual_beats" in result["style_tags"]

    def test_music_video_scene_tags(self):
        fp = {**_BASE_FP, "scene_tags": ["dancing", "concert", "performance"]}
        result = infer_style_profile(fp)
        assert result["ranking_profile"] == "music_video"

    def test_talking_head_face_and_speech(self):
        fp = {
            **_BASE_FP,
            "face_density": 0.75,
            "speech_density": 0.65,
            "avg_shot_duration": 3.0,
            "pace": "slow",
            "motion_style": {"primary": "static"},
        }
        result = infer_style_profile(fp)
        assert result["ranking_profile"] == "talking_head"
        assert result["faces_central"] is True
        assert result["subject_focus"] == "presenter"
        assert result["content_type"] == "talking_head"

    def test_talking_head_scene_tags(self):
        fp = {**_BASE_FP, "scene_tags": ["interview", "presenter"], "avg_shot_duration": 2.5}
        result = infer_style_profile(fp)
        assert result["ranking_profile"] == "talking_head"

    def test_vlog_shake_motion(self):
        fp = {
            **_BASE_FP,
            "motion_style": {"primary": "shake"},
            "face_density": 0.5,
            "speech_density": 0.35,
            "pace": "medium",
            "energy_level": "medium",
        }
        result = infer_style_profile(fp)
        assert result["ranking_profile"] == "vlog"
        assert result["faces_central"] is True
        assert "handheld" in result["style_tags"]

    def test_vlog_scene_tags(self):
        fp = {**_BASE_FP, "scene_tags": ["vlog", "lifestyle"], "motion_style": {"primary": "shake"}}
        result = infer_style_profile(fp)
        assert result["ranking_profile"] == "vlog"

    def test_fashion_montage_scene_tags(self):
        fp = {
            **_BASE_FP,
            "scene_tags": ["fashion", "outfit", "aesthetic"],
            "speech_density": 0.05,
            "face_density": 0.4,
        }
        result = infer_style_profile(fp)
        assert result["ranking_profile"] == "fashion_montage"
        assert result["content_type"] == "fashion"
        assert "aesthetic" in result["style_tags"]

    def test_fashion_montage_no_speech_signal(self):
        fp = {
            **_BASE_FP,
            "face_density": 0.45,
            "speech_density": 0.05,
            "scene_tags": ["clothing", "model"],
        }
        result = infer_style_profile(fp)
        assert result["ranking_profile"] == "fashion_montage"

    def test_travel_reel_scene_tags(self):
        fp = {
            **_BASE_FP,
            "scene_tags": ["landscape", "outdoor", "nature"],
            "face_density": 0.05,
        }
        result = infer_style_profile(fp)
        assert result["ranking_profile"] == "travel_reel"
        assert result["subject_focus"] == "scene"
        assert result["faces_central"] is False
        assert "scenic" in result["style_tags"]

    def test_travel_reel_low_face_density(self):
        fp = {
            **_BASE_FP,
            "face_density": 0.08,
            "scene_tags": ["destination", "adventure"],
            "pace": "medium",
        }
        result = infer_style_profile(fp)
        assert result["ranking_profile"] == "travel_reel"

    def test_product_showcase_scene_tags(self):
        fp = {
            **_BASE_FP,
            "scene_tags": ["product", "closeup", "unboxing"],
            "face_density": 0.05,
            "pace": "slow",
        }
        result = infer_style_profile(fp)
        assert result["ranking_profile"] == "product_showcase"
        assert result["subject_focus"] == "product"
        assert result["content_type"] == "product_review"
        assert "product_focus" in result["style_tags"]

    def test_product_showcase_very_low_face(self):
        fp = {
            **_BASE_FP,
            "face_density": 0.03,
            "scene_tags": ["detail", "showcase"],
            "pace": "slow",
        }
        result = infer_style_profile(fp)
        assert result["ranking_profile"] == "product_showcase"

    # ── Output contract ──────────────────────────────────────────────────

    def test_required_keys_always_present(self):
        required = {"ranking_profile", "faces_central", "subject_focus", "content_type", "style_tags"}
        result = infer_style_profile(_BASE_FP)
        assert required <= set(result.keys())

    def test_style_tags_is_list_of_strings(self):
        result = infer_style_profile(_BASE_FP)
        assert isinstance(result["style_tags"], list)
        assert all(isinstance(t, str) for t in result["style_tags"])

    def test_faces_central_is_bool(self):
        for fd in (None, 0.0, 0.5, 1.0):
            fp = {**_BASE_FP}
            if fd is not None:
                fp["face_density"] = fd
            result = infer_style_profile(fp)
            assert isinstance(result["faces_central"], bool)

    def test_fallback_no_signals_returns_valid_profile(self):
        """When there are no specific signals, a valid profile is still returned."""
        fp = {"avg_shot_duration": 1.5, "pace": "medium", "energy_level": "medium"}
        result = infer_style_profile(fp)
        valid = {"talking_head", "vlog", "fashion_montage", "travel_reel", "product_showcase", "music_video"}
        assert result["ranking_profile"] in valid

    def test_faces_central_true_when_high_face_density(self):
        fp = {**_BASE_FP, "face_density": 0.8}
        assert infer_style_profile(fp)["faces_central"] is True

    def test_faces_central_false_when_low_face_density(self):
        fp = {**_BASE_FP, "face_density": 0.1}
        assert infer_style_profile(fp)["faces_central"] is False

    def test_beat_synced_tag_when_high_alignment(self):
        fp = {
            **_BASE_FP,
            "cut_to_beat_alignment": 0.65,
            "beat_points": [0.3, 0.6, 0.9, 1.2, 1.5, 1.8],
            "tempo_bpm": 128.0,
        }
        result = infer_style_profile(fp)
        assert "beat_synced" in result["style_tags"]

    def test_style_tags_no_duplicates(self):
        fp = {
            **_BASE_FP,
            "face_density": 0.7,
            "scene_tags": ["fashion", "aesthetic"],
            "pace": "fast",
        }
        tags = infer_style_profile(fp)["style_tags"]
        assert len(tags) == len(set(tags))


class TestScoreProfile:
    """_score_profile() assigns higher scores to the correct profile."""

    def test_music_video_scores_highest_for_beat_aligned(self):
        fp = {
            **_BASE_FP,
            "cut_to_beat_alignment": 0.8,
            "beat_points": [0.3, 0.6, 0.9, 1.2, 1.5, 1.8],
            "tempo_bpm": 140.0,
        }
        scores = _score_profile(fp)
        assert scores["music_video"] == max(scores.values())

    def test_talking_head_scores_highest_for_face_and_speech(self):
        fp = {
            **_BASE_FP,
            "face_density": 0.8,
            "speech_density": 0.7,
            "avg_shot_duration": 3.0,
            "motion_style": {"primary": "static"},
        }
        scores = _score_profile(fp)
        assert scores["talking_head"] == max(scores.values())

    def test_vlog_scores_highest_for_shake_motion(self):
        fp = {
            **_BASE_FP,
            "motion_style": {"primary": "shake"},
            "face_density": 0.5,
            "speech_density": 0.3,
        }
        scores = _score_profile(fp)
        assert scores["vlog"] == max(scores.values())

    def test_product_showcase_scores_highest_for_product_tags(self):
        fp = {
            **_BASE_FP,
            "scene_tags": ["product", "unboxing", "closeup"],
            "face_density": 0.02,
            "pace": "slow",
        }
        scores = _score_profile(fp)
        assert scores["product_showcase"] == max(scores.values())

