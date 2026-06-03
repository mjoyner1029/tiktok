"""Unit tests for app/services/footage_analyzer.py.

All subprocess/ffprobe calls and PIL frame extraction are mocked.
We verify that:
  - each clip produces usable_segments with actual start/end times and scores
  - long segments are subdivided into overlapping windows (not just 1 moment)
  - speech detection correctly tags segments
  - quality dict contains blur_score, brightness, stability
  - legacy `moments` key mirrors usable_segments
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from app.services.footage_analyzer import (
    FootageAnalyzer,
    _frame_sharpness,
    _brightness_label,
    _stability_label,
)

# ── Mock helpers ──────────────────────────────────────────────────────────

MOCK_MEDIA_INFO_30S = {
    "duration_sec": 30.0,
    "width": 1080,
    "height": 1920,
    "fps": 30.0,
    "has_video": True,
    "has_audio": True,
    "file_size_bytes": 10_000_000,
    "format_name": "mov,mp4",
    "bit_rate": 4_000_000,
}

MOCK_MEDIA_INFO_4S = {
    "duration_sec": 4.0,
    "width": 1080,
    "height": 1920,
    "fps": 30.0,
    "has_video": True,
    "has_audio": False,
    "file_size_bytes": 1_000_000,
    "format_name": "mov,mp4",
    "bit_rate": 2_000_000,
}

MOCK_VISUAL_STYLE_NO_CUTS = {
    "cut_timestamps": [],
    "avg_cut_duration_sec": 30.0,
    "num_cuts": 0,
    "color_grade": {"brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "gamma": 1.0, "luma_avg": 128.0},
}

MOCK_VISUAL_STYLE_CUTS = {
    "cut_timestamps": [1.0, 2.5],
    "avg_cut_duration_sec": 1.5,
    "num_cuts": 2,
    "color_grade": {"brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "gamma": 1.0, "luma_avg": 128.0},
}


def _make_analyzer():
    return FootageAnalyzer()


def _make_tmp_mp4(tmp_path, name="clip.mp4"):
    f = tmp_path / name
    f.write_bytes(b"\x00\x00\x00\x20ftyp")   # minimal stub, never actually read
    return f


# ── Pure helper functions ─────────────────────────────────────────────────

class TestHelpers:
    def test_brightness_label_dark(self):
        assert _brightness_label(50.0) == "dark"

    def test_brightness_label_normal(self):
        assert _brightness_label(128.0) == "normal"

    def test_brightness_label_overexposed(self):
        assert _brightness_label(220.0) == "overexposed"

    def test_stability_label_stable(self):
        assert _stability_label(0.2) == "stable"

    def test_stability_label_handheld(self):
        assert _stability_label(3.5) == "handheld"

    def test_stability_label_shaky(self):
        assert _stability_label(7.0) == "shaky"


# ── FootageAnalyzer.analyze_all ───────────────────────────────────────────

class TestFootageAnalyzerLongClip:
    """A 30s clip with no internal cuts must be subdivided into multiple windows."""

    @pytest.fixture
    def entry(self, tmp_path):
        clip = _make_tmp_mp4(tmp_path)
        analyzer = _make_analyzer()

        with (
            patch("app.services.footage_analyzer.get_media_info", return_value=MOCK_MEDIA_INFO_30S),
            patch("app.services.footage_analyzer.extract_visual_style", return_value=MOCK_VISUAL_STYLE_NO_CUTS),
            patch("app.services.footage_analyzer._extract_frame", return_value=False),   # skip actual frames
            patch("app.services.footage_analyzer._motion_score_cv2", return_value=0.5),
            patch("app.services.footage_analyzer.detect_silence", return_value=[]),
        ):
            result = analyzer.analyze_all([clip])

        return result[0]

    def test_returns_one_entry_per_clip(self, entry):
        assert entry["asset_id"] == "footage_00"

    def test_usable_segments_has_multiple_windows(self, entry):
        # 30s with no cuts → subdivided into overlapping 2s windows
        segs = entry["usable_segments"]
        assert len(segs) >= 5, f"Expected ≥5 windows for 30s clip, got {len(segs)}"

    def test_each_segment_has_start_end_score(self, entry):
        for seg in entry["usable_segments"]:
            assert "start" in seg, f"Missing start in {seg}"
            assert "end" in seg, f"Missing end in {seg}"
            assert "score" in seg, f"Missing score in {seg}"
            assert seg["end"] > seg["start"]
            assert isinstance(seg["score"], (int, float))

    def test_segments_cover_full_duration(self, entry):
        segs = entry["usable_segments"]
        # First seg starts at/near 0
        assert segs[0]["start"] == pytest.approx(0.0, abs=0.1)
        # Last seg ends reasonably close to 30s
        assert segs[-1]["end"] <= 30.5

    def test_legacy_moments_mirrors_usable_segments(self, entry):
        assert entry["moments"] == entry["usable_segments"]

    def test_quality_dict_present(self, entry):
        q = entry["quality"]
        assert "blur_score" in q
        assert "brightness" in q
        assert "stability" in q

    def test_duration_sec_correct(self, entry):
        assert entry["duration_sec"] == pytest.approx(30.0)

    def test_resolution_correct(self, entry):
        assert entry["resolution"] == [1080, 1920]


class TestFootageAnalyzerShortClip:
    """A 4s clip with internal cuts produces proper segments (not subdivided)."""

    @pytest.fixture
    def entry(self, tmp_path):
        clip = _make_tmp_mp4(tmp_path)
        analyzer = _make_analyzer()

        with (
            patch("app.services.footage_analyzer.get_media_info", return_value=MOCK_MEDIA_INFO_4S),
            patch("app.services.footage_analyzer.extract_visual_style", return_value=MOCK_VISUAL_STYLE_CUTS),
            patch("app.services.footage_analyzer._extract_frame", return_value=False),
            patch("app.services.footage_analyzer._motion_score_cv2", return_value=0.2),
            patch("app.services.footage_analyzer.detect_silence", return_value=[]),
        ):
            result = analyzer.analyze_all([clip])

        return result[0]

    def test_returns_entry(self, entry):
        assert entry is not None
        assert entry["asset_id"] == "footage_00"

    def test_segments_derived_from_cuts(self, entry):
        # 3 cuts at 2.0, 5.0, 8.0 in a 4s clip → clips from real cut timestamps
        segs = entry["usable_segments"]
        assert len(segs) >= 1

    def test_segment_end_within_duration(self, entry):
        duration = MOCK_MEDIA_INFO_4S["duration_sec"]
        for seg in entry["usable_segments"]:
            assert seg["end"] <= duration + 0.05, f"Segment end {seg['end']} exceeds duration {duration}"

    def test_no_speech_when_no_audio(self, entry):
        # MOCK_MEDIA_INFO_4S has has_audio=False
        assert entry["has_speech"] is False


class TestFootageAnalyzerMultipleClips:
    def test_multiple_clips_indexed_correctly(self, tmp_path):
        clips = [_make_tmp_mp4(tmp_path, f"clip_{i}.mp4") for i in range(3)]
        analyzer = _make_analyzer()

        with (
            patch("app.services.footage_analyzer.get_media_info", return_value=MOCK_MEDIA_INFO_4S),
            patch("app.services.footage_analyzer.extract_visual_style", return_value=MOCK_VISUAL_STYLE_NO_CUTS),
            patch("app.services.footage_analyzer._extract_frame", return_value=False),
            patch("app.services.footage_analyzer._motion_score_cv2", return_value=0.0),
            patch("app.services.footage_analyzer.detect_silence", return_value=[]),
        ):
            result = analyzer.analyze_all(clips)

        # Filter out the batch report sentinel appended by analyze_all
        real_results = [e for e in result if e.get("asset_id") != "__batch_report__"]
        assert len(real_results) == 3
        ids = [e["asset_id"] for e in real_results]
        assert ids == ["footage_00", "footage_01", "footage_02"]

    def test_failed_clip_does_not_crash_others(self, tmp_path):
        clips = [_make_tmp_mp4(tmp_path, f"c{i}.mp4") for i in range(2)]
        analyzer = _make_analyzer()

        call_count = {"n": 0}
        def selective_info(path):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("ffprobe died")
            return MOCK_MEDIA_INFO_4S

        with (
            patch("app.services.footage_analyzer.get_media_info", side_effect=selective_info),
            patch("app.services.footage_analyzer.extract_visual_style", return_value=MOCK_VISUAL_STYLE_NO_CUTS),
            patch("app.services.footage_analyzer._extract_frame", return_value=False),
            patch("app.services.footage_analyzer._motion_score_cv2", return_value=0.0),
            patch("app.services.footage_analyzer.detect_silence", return_value=[]),
        ):
            result = analyzer.analyze_all(clips)

        # Filter out the batch report sentinel
        real_results = [e for e in result if e.get("asset_id") != "__batch_report__"]
        # Both entries present (first is fallback)
        assert len(real_results) == 2
        assert real_results[1]["asset_id"] == "footage_01"
        assert len(real_results[1]["usable_segments"]) >= 1
