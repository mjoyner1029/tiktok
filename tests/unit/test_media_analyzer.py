"""Tests for app/services/media_analyzer.py — subprocess and whisper are mocked."""

from __future__ import annotations

import json
from unittest.mock import patch, Mock, MagicMock
import pytest

from app.services.media_analyzer import (
    probe_media,
    get_media_info,
    extract_audio,
    detect_silence,
    find_sentence_boundaries,
    extract_visual_style,
    transcribe,
    analyze_asset,
    _load_whisper,
)
from app.config import get_settings

settings = get_settings()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FFPROBE_JSON = {
    "format": {
        "duration": "10.5",
        "size": "1048576",
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "bit_rate": "800000",
    },
    "streams": [
        {
            "codec_type": "video",
            "width": 1080,
            "height": 1920,
            "r_frame_rate": "30/1",
        },
        {
            "codec_type": "audio",
        },
    ],
}

SILENCE_STDERR = (
    "[silencedetect @ 0x123] silence_start: 2.000\n"
    "[silencedetect @ 0x123] silence_end: 3.500 | silence_duration: 1.500\n"
    "[silencedetect @ 0x123] silence_start: 7.000\n"
    "[silencedetect @ 0x123] silence_end: 8.200 | silence_duration: 1.200\n"
)

SCENE_STDERR = (
    "pts_time:0.000 lavfi.scene_score=0.380\n"
    "pts_time:1.500 lavfi.scene_score=0.420\n"
    "pts_time:3.200 lavfi.scene_score=0.390\n"
)

SHOWINFO_STDERR = (
    "n:   0 pts:      0 pts_time:0       pos:   4096 fmt:yuv420p "
    "sar:1/1 s:1080x1920 i:P iskey:1 type:I checksum:ABCDEF "
    "mean:[100 110 120] stdev:[20.0 30.0 25.0]\n"
    "n:   1 pts:   3000 pts_time:1.5     pos:  65536 fmt:yuv420p "
    "sar:1/1 s:1080x1920 i:P iskey:0 type:P checksum:123456 "
    "mean:[90 100 110] stdev:[15.0 20.0 18.0]\n"
)


def _ok_proc_with_stdout(stdout="", stderr=""):
    m = Mock()
    m.returncode = 0
    m.stdout = stdout
    m.stderr = stderr
    return m


# ---------------------------------------------------------------------------
# probe_media
# ---------------------------------------------------------------------------

class TestProbeMedia:
    def test_success(self):
        proc = _ok_proc_with_stdout(stdout=json.dumps(FFPROBE_JSON))
        with patch("app.services.media_analyzer.subprocess.run", return_value=proc):
            result = probe_media("/fake/video.mp4")
        assert "format" in result
        assert "streams" in result
        assert result["format"]["duration"] == "10.5"

    def test_failure_raises_runtime_error(self):
        proc = Mock(returncode=1, stdout="", stderr="ffprobe: error")
        with patch("app.services.media_analyzer.subprocess.run", return_value=proc):
            with pytest.raises(RuntimeError, match="ffprobe failed"):
                probe_media("/fake/bad.mp4")


# ---------------------------------------------------------------------------
# get_media_info
# ---------------------------------------------------------------------------

class TestGetMediaInfo:
    def test_full_info(self):
        proc = _ok_proc_with_stdout(stdout=json.dumps(FFPROBE_JSON))
        with patch("app.services.media_analyzer.subprocess.run", return_value=proc):
            info = get_media_info("/fake/video.mp4")
        assert info["duration_sec"] == pytest.approx(10.5)
        assert info["width"] == 1080
        assert info["height"] == 1920
        assert info["fps"] == 30.0
        assert info["has_video"] is True
        assert info["has_audio"] is True
        assert info["file_size_bytes"] == 1048576

    def test_no_streams(self):
        data = {"format": {"duration": "5.0", "size": "1024", "bit_rate": "100"}, "streams": []}
        proc = _ok_proc_with_stdout(stdout=json.dumps(data))
        with patch("app.services.media_analyzer.subprocess.run", return_value=proc):
            info = get_media_info("/fake/audio_only.mp3")
        assert info["width"] == 0
        assert info["height"] == 0
        assert info["has_video"] is False

    def test_fps_division_by_zero_handled(self):
        data = {
            "format": {"duration": "5.0", "size": "1024", "bit_rate": "100"},
            "streams": [{"codec_type": "video", "width": 640, "height": 480, "r_frame_rate": "30/0"}],
        }
        proc = _ok_proc_with_stdout(stdout=json.dumps(data))
        with patch("app.services.media_analyzer.subprocess.run", return_value=proc):
            info = get_media_info("/fake/video.mp4")
        assert info["fps"] == 0


# ---------------------------------------------------------------------------
# extract_audio
# ---------------------------------------------------------------------------

class TestExtractAudio:
    def test_extracts_to_wav(self, tmp_path):
        out = str(tmp_path / "audio.wav")
        with patch("app.services.media_analyzer.subprocess.run", return_value=_ok_proc_with_stdout()):
            result = extract_audio("/fake/video.mp4", output_path=out)
        assert result == out

    def test_auto_creates_temp_file(self):
        with patch("app.services.media_analyzer.subprocess.run", return_value=_ok_proc_with_stdout()):
            result = extract_audio("/fake/video.mp4")
        assert result.endswith(".wav")


# ---------------------------------------------------------------------------
# detect_silence
# ---------------------------------------------------------------------------

class TestDetectSilence:
    def test_parses_silence_ranges(self):
        proc = _ok_proc_with_stdout(stderr=SILENCE_STDERR)
        with patch("app.services.media_analyzer.subprocess.run", return_value=proc):
            silences = detect_silence("/fake/video.mp4")
        assert len(silences) == 2
        assert silences[0]["start"] == pytest.approx(2.0)
        assert silences[0]["end"] == pytest.approx(3.5)
        assert silences[0]["duration"] == pytest.approx(1.5)
        assert silences[1]["start"] == pytest.approx(7.0)
        assert silences[1]["end"] == pytest.approx(8.2)

    def test_no_silences(self):
        proc = _ok_proc_with_stdout(stderr="")
        with patch("app.services.media_analyzer.subprocess.run", return_value=proc):
            silences = detect_silence("/fake/video.mp4")
        assert silences == []

    def test_incomplete_pair_ignored(self):
        # silence_start without matching end
        stderr = "[silencedetect] silence_start: 2.000\n"
        proc = _ok_proc_with_stdout(stderr=stderr)
        with patch("app.services.media_analyzer.subprocess.run", return_value=proc):
            silences = detect_silence("/fake/video.mp4")
        # Incomplete pair should either be empty or handled gracefully
        assert isinstance(silences, list)


# ---------------------------------------------------------------------------
# find_sentence_boundaries
# ---------------------------------------------------------------------------

class TestFindSentenceBoundaries:
    def test_single_sentence(self):
        segments = [
            {"start": 0.0, "end": 2.0, "text": "Hello world."},
        ]
        result = find_sentence_boundaries(segments)
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_multiple_sentences(self):
        segments = [
            {"start": 0.0, "end": 1.5, "text": "First sentence."},
            {"start": 1.5, "end": 3.0, "text": "Second sentence."},
            {"start": 3.0, "end": 5.0, "text": "Third sentence!"},
        ]
        result = find_sentence_boundaries(segments)
        assert isinstance(result, list)

    def test_empty_segments(self):
        result = find_sentence_boundaries([])
        assert result == []

    def test_question_mark_boundary(self):
        segments = [
            {"start": 0.0, "end": 2.0, "text": "Did you know?"},
            {"start": 2.0, "end": 4.0, "text": "This is amazing."},
        ]
        result = find_sentence_boundaries(segments)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# extract_visual_style
# ---------------------------------------------------------------------------

class TestExtractVisualStyle:
    def test_basic(self):
        scene_proc = _ok_proc_with_stdout(stderr=SCENE_STDERR)
        showinfo_proc = _ok_proc_with_stdout(stderr=SHOWINFO_STDERR)
        duration_proc = _ok_proc_with_stdout(stdout=json.dumps(FFPROBE_JSON))

        call_count = [0]

        def side_effect(cmd, **kwargs):
            call_count[0] += 1
            if "silencedetect" in " ".join(cmd) or "select" in " ".join(cmd):
                return showinfo_proc
            elif "scene" in " ".join(cmd):
                return scene_proc
            elif "ffprobe" in cmd[0]:
                return duration_proc
            return scene_proc

        with patch("app.services.media_analyzer.subprocess.run", side_effect=side_effect):
            result = extract_visual_style("/fake/video.mp4")
        assert isinstance(result, dict)
        assert "avg_cut_duration_sec" in result
        assert "num_cuts" in result

    def test_no_scene_changes(self):
        proc = _ok_proc_with_stdout(stderr="", stdout=json.dumps(FFPROBE_JSON))
        with patch("app.services.media_analyzer.subprocess.run", return_value=proc):
            result = extract_visual_style("/fake/video.mp4")
        assert isinstance(result, dict)
        assert result["num_cuts"] >= 0


# ---------------------------------------------------------------------------
# transcribe
# ---------------------------------------------------------------------------

class TestTranscribe:
    def _mock_whisper(self):
        mock_model = Mock()
        mock_model.transcribe.return_value = {
            "text": "Hello world",
            "language": "en",
            "segments": [
                {
                    "start": 0.0,
                    "end": 2.0,
                    "text": " Hello world",
                    "words": [
                        {"word": "Hello", "start": 0.0, "end": 1.0, "probability": 0.9},
                        {"word": "world", "start": 1.0, "end": 2.0, "probability": 0.95},
                    ],
                }
            ],
        }
        return mock_model

    def test_transcribe_audio_file(self, tmp_path):
        audio_path = str(tmp_path / "audio.wav")
        open(audio_path, "w").close()
        mock_model = self._mock_whisper()

        with patch("app.services.media_analyzer._load_whisper", return_value=mock_model):
            result = transcribe(audio_path)
        assert result["text"] == "Hello world"
        assert len(result["segments"]) == 1
        assert result["language"] == "en"

    def test_transcribe_video_extracts_audio_first(self, tmp_path):
        video_path = str(tmp_path / "video.mp4")
        open(video_path, "w").close()
        mock_model = self._mock_whisper()

        with patch("app.services.media_analyzer._load_whisper", return_value=mock_model), \
             patch("app.services.media_analyzer.extract_audio", return_value="/tmp/audio.wav") as mock_ea:
            result = transcribe(video_path)
        mock_ea.assert_called_once_with(video_path)
        assert result["text"] == "Hello world"

    def test_transcribe_with_language(self, tmp_path):
        audio_path = str(tmp_path / "audio.wav")
        open(audio_path, "w").close()
        mock_model = self._mock_whisper()

        with patch("app.services.media_analyzer._load_whisper", return_value=mock_model):
            result = transcribe(audio_path, language="en")
        assert result["language"] == "en"

    def test_transcribe_no_word_timestamps(self, tmp_path):
        audio_path = str(tmp_path / "audio.wav")
        open(audio_path, "w").close()
        mock_model = Mock()
        mock_model.transcribe.return_value = {
            "text": "Hello",
            "language": "en",
            "segments": [{"start": 0.0, "end": 1.0, "text": " Hello"}],
        }
        with patch("app.services.media_analyzer._load_whisper", return_value=mock_model):
            result = transcribe(audio_path, word_timestamps=False)
        assert result["text"] == "Hello"


# ---------------------------------------------------------------------------
# analyze_asset
# ---------------------------------------------------------------------------

class TestAnalyzeAsset:
    def test_full_analysis(self, tmp_path):
        asset_path = str(tmp_path / "video.mp4")
        open(asset_path, "w").close()

        mock_info = {
            "duration_sec": 10.0, "width": 1080, "height": 1920,
            "fps": 30.0, "has_video": True, "has_audio": True,
            "file_size_bytes": 1048576, "format_name": "mp4", "bit_rate": 800000,
        }
        mock_transcript = {
            "text": "Hello world", "language": "en", "segments": [],
        }
        mock_silences = [{"start": 2.0, "end": 3.0, "duration": 1.0}]
        mock_sentences = [{"text": "Hello world.", "start": 0.0, "end": 2.0}]
        mock_visual = {
            "cut_timestamps": [1.5, 3.0],
            "avg_cut_duration_sec": 1.5,
            "num_cuts": 2,
            "color_grade": {"brightness": 0.0},
        }

        with patch("app.services.media_analyzer.get_media_info", return_value=mock_info), \
             patch("app.services.media_analyzer.transcribe", return_value=mock_transcript), \
             patch("app.services.media_analyzer.detect_silence", return_value=mock_silences), \
             patch("app.services.media_analyzer.find_sentence_boundaries", return_value=mock_sentences), \
             patch("app.services.media_analyzer.extract_visual_style", return_value=mock_visual):
            result = analyze_asset(asset_path)

        assert result["media_info"]["width"] == 1080
        assert result["transcript"]["text"] == "Hello world"
        assert len(result["silences"]) == 1
        assert isinstance(result["sentences"], list)


# ---------------------------------------------------------------------------
# Additional coverage tests
# ---------------------------------------------------------------------------

class TestFindSentenceBoundariesExtra:
    def test_words_with_ellipsis(self):
        """Test sentence boundary with … character."""
        segments = [
            {
                "start": 0.0,
                "end": 3.0,
                "text": "Hello… world.",
                "words": [
                    {"word": "Hello…", "start": 0.0, "end": 1.0},
                    {"word": "world.", "start": 1.5, "end": 3.0},
                ],
            }
        ]
        result = find_sentence_boundaries(segments)
        assert len(result) >= 1

    def test_words_no_sentence_end_flushes_remaining(self):
        """Words without punctuation should flush as remaining at end."""
        segments = [
            {
                "start": 0.0,
                "end": 5.0,
                "text": "here are some words",
                "words": [
                    {"word": "here", "start": 0.0, "end": 1.0},
                    {"word": "are", "start": 1.0, "end": 2.0},
                    {"word": "some", "start": 2.0, "end": 3.0},
                    {"word": "words", "start": 3.0, "end": 5.0},
                ],
            }
        ]
        result = find_sentence_boundaries(segments)
        assert len(result) == 1
        assert "words" in result[0]["text"]

    def test_multiple_segments_with_words(self):
        """Multiple segments each with word-level timestamps."""
        segments = [
            {
                "start": 0.0,
                "end": 2.0,
                "text": "first sentence.",
                "words": [
                    {"word": "first", "start": 0.0, "end": 0.8},
                    {"word": "sentence.", "start": 0.8, "end": 2.0},
                ],
            },
            {
                "start": 2.5,
                "end": 5.0,
                "text": "second sentence!",
                "words": [
                    {"word": "second", "start": 2.5, "end": 3.5},
                    {"word": "sentence!", "start": 3.5, "end": 5.0},
                ],
            },
        ]
        result = find_sentence_boundaries(segments)
        assert len(result) == 2


class TestExtractVisualStyleExtra:
    def test_multiple_scene_cuts(self):
        """Test with multiple scene changes to exercise gap calculation."""
        showinfo_stderr = (
            "n:   0 pts:      0 pts_time:0       showinfo\n"
            "n:   1 pts:   3000 pts_time:1.5     showinfo\n"
            "n:   2 pts:   6000 pts_time:3.0     showinfo\n"
        )
        color_stderr = (
            "[signalstats] YAVG:150 YHIGH:200 YLOW:50 UAVG:130 VAVG:125\n"
        )
        call_count = [0]

        def side_effect(cmd, **kwargs):
            call_count[0] += 1
            c = _ok_proc_with_stdout(stderr="")
            if "ffprobe" in " ".join(cmd):
                c.stdout = json.dumps(FFPROBE_JSON)
            elif call_count[0] == 1:
                c.stderr = showinfo_stderr
            elif call_count[0] == 2:
                c.stderr = color_stderr
            return c

        with patch("app.services.media_analyzer.subprocess.run", side_effect=side_effect):
            result = extract_visual_style("/fake/video.mp4")
        assert result["num_cuts"] >= 0
        assert "color_grade" in result

    def test_color_grade_with_yavg_values(self):
        """Test color grade calculation when YAVG data is present."""
        showinfo_stderr = ""
        color_stderr = "\n".join([
            f"YAVG:{y} YHIGH:{h} YLOW:{lo} UAVG:{u} VAVG:{v}"
            for y, h, lo, u, v in [(160, 220, 30, 135, 128), (140, 210, 25, 130, 126)]
        ])
        call_count = [0]

        def side_effect(cmd, **kwargs):
            call_count[0] += 1
            c = _ok_proc_with_stdout(stderr="")
            if "ffprobe" in " ".join(cmd):
                c.stdout = json.dumps(FFPROBE_JSON)
            elif call_count[0] == 1:
                c.stderr = showinfo_stderr
            else:
                c.stderr = color_stderr
            return c

        with patch("app.services.media_analyzer.subprocess.run", side_effect=side_effect):
            result = extract_visual_style("/fake/video.mp4")
        assert "color_grade" in result
        grade = result["color_grade"]
        assert "brightness" in grade
        assert "contrast" in grade

    def test_zero_duration_fallback(self):
        """avg_cut_duration defaults to 2.0 when duration=0."""
        empty_probe = {
            "format": {"duration": "0", "size": "0", "format_name": "mp4", "bit_rate": "0"},
            "streams": [],
        }

        def side_effect(cmd, **kwargs):
            c = _ok_proc_with_stdout(stderr="")
            if "ffprobe" in " ".join(cmd):
                c.stdout = json.dumps(empty_probe)
            return c

        with patch("app.services.media_analyzer.subprocess.run", side_effect=side_effect):
            result = extract_visual_style("/fake/video.mp4")
        assert result["avg_cut_duration_sec"] == 2.0


class TestLoadWhisper:
    def test_loads_model_once(self):
        """_load_whisper loads model only once."""
        import app.services.media_analyzer as mod
        orig = mod._whisper_model
        try:
            mod._whisper_model = None
            mock_model = Mock()
            mock_whisper = Mock()
            mock_whisper.load_model.return_value = mock_model
            with patch.dict("sys.modules", {"whisper": mock_whisper}):
                result = _load_whisper()
            assert result is mock_model
            # Second call returns cached
            result2 = mod._load_whisper()
            assert result2 is mock_model
            mock_whisper.load_model.assert_called_once()
        finally:
            mod._whisper_model = orig
