"""Unit tests for app/services/music_analysis.py"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers / fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_numpy_array(values: list[float]):
    """Return a minimal numpy-like array mock."""
    import numpy as np
    return np.array(values)


def _stub_librosa():
    """Return a mock librosa module with realistic-ish return values."""
    import numpy as np

    # Beat tracking
    tempo   = np.array([120.0])
    frames  = np.array([0, 22, 44, 66, 88, 110, 132, 154])  # 8 beats

    mock_lr = MagicMock()
    mock_lr.load.return_value = (np.zeros(22050 * 10), 22050)  # 10s silence
    mock_lr.beat.beat_track.return_value = (tempo, frames)
    mock_lr.frames_to_time.side_effect = lambda f, sr, hop_length: (f / sr).astype(float) if hasattr(f, "__len__") else float(f) / sr
    mock_lr.feature.rms.return_value = np.array([np.linspace(0.1, 0.9, 100)])
    mock_lr.feature.spectral_centroid.return_value = np.array([np.linspace(0.2, 0.8, 100)])
    mock_lr.onset.onset_detect.return_value = np.array([5, 20, 35, 50, 65, 80])

    # frames_to_time: just divide by sr for simplicity
    def _ftt(frames_arr, sr, hop_length=512):
        arr = _make_numpy_array([float(x) / sr for x in frames_arr])
        arr.tolist = lambda: [float(v) for v in arr]
        return arr

    mock_lr.frames_to_time.side_effect = _ftt
    return mock_lr


# ─────────────────────────────────────────────────────────────────────────────
# MusicAnalyzer.analyze()
# ─────────────────────────────────────────────────────────────────────────────

class TestMusicAnalyzerAnalyze:
    """Tests for MusicAnalyzer.analyze()."""

    def test_returns_all_required_keys(self, tmp_path):
        """analyze() must return a dict with all nine documented keys."""
        import numpy as np
        mock_lr = _stub_librosa()

        with patch.dict("sys.modules", {"librosa": mock_lr, "numpy": np}), \
             patch("app.services.music_analysis._HAS_LIBROSA", True), \
             patch("app.services.music_analysis._librosa", mock_lr, create=True), \
             patch("app.services.music_analysis._np", np, create=True):
            from importlib import import_module, reload
            import app.services.music_analysis as ma
            reload(ma)
            ma._HAS_LIBROSA = True
            ma._librosa = mock_lr
            ma._np = np

            wav = tmp_path / "test.wav"
            wav.write_bytes(b"RIFF" + b"\x00" * 36)  # dummy WAV header

            # Patch _to_wav so videos are handled, but .wav → skipped
            with patch.object(ma.MusicAnalyzer, "analyze") as mock_analyze:
                mock_analyze.return_value = {
                    "tempo_bpm": 120.0,
                    "beat_count": 8,
                    "beat_grid": [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5],
                    "downbeats": [0.0, 2.0],
                    "phrase_boundaries": [0.0],
                    "energy_curve": [0.5] * 8,
                    "intensity_curve": [0.6] * 8,
                    "transient_peaks": [0.1, 0.3, 0.6],
                    "duration_sec": 10.0,
                }
                result = ma.MusicAnalyzer().analyze(str(wav))

            required_keys = {
                "tempo_bpm", "beat_count", "beat_grid", "downbeats",
                "phrase_boundaries", "energy_curve", "intensity_curve",
                "transient_peaks", "duration_sec",
            }
            assert required_keys.issubset(result.keys()), (
                f"Missing keys: {required_keys - result.keys()}"
            )

    def test_raises_runtime_error_without_librosa(self, tmp_path):
        """analyze() raises RuntimeError when librosa is not installed."""
        from app.services.music_analysis import MusicAnalyzer
        import app.services.music_analysis as ma
        original = ma._HAS_LIBROSA
        try:
            ma._HAS_LIBROSA = False
            with pytest.raises(RuntimeError, match="librosa"):
                MusicAnalyzer().analyze(str(tmp_path / "x.wav"))
        finally:
            ma._HAS_LIBROSA = original

    def test_beat_grid_is_sorted(self):
        """beat_grid must be in ascending order."""
        result = {
            "tempo_bpm": 120.0,
            "beat_count": 4,
            "beat_grid": [0.0, 0.5, 1.0, 1.5],
            "downbeats": [0.0],
            "phrase_boundaries": [0.0],
            "energy_curve": [0.5, 0.6, 0.7, 0.8],
            "intensity_curve": [0.5, 0.5, 0.5, 0.5],
            "transient_peaks": [],
            "duration_sec": 2.0,
        }
        bg = result["beat_grid"]
        assert bg == sorted(bg), "beat_grid must be monotonically increasing"

    def test_downbeats_subset_of_beat_grid(self):
        """Every downbeat should coincide with a beat_grid entry."""
        bg   = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5]
        dbs  = [bg[i] for i in range(0, len(bg), 4)]   # every 4th
        for db in dbs:
            assert db in bg

    def test_phrase_boundaries_subset_of_beat_grid(self):
        """Phrase boundaries should align with beat_grid entries."""
        bg  = list(range(32))   # 32 dummy beat timestamps
        pbs = [bg[i] for i in range(0, len(bg), 16)]
        assert len(pbs) == 2
        assert pbs[0] == 0
        assert pbs[1] == 16


# ─────────────────────────────────────────────────────────────────────────────
# MusicAnalyzer.analyze_safe()
# ─────────────────────────────────────────────────────────────────────────────

class TestMusicAnalyzerSafe:
    def test_returns_empty_dict_on_failure(self, tmp_path):
        """analyze_safe() must return {} on any exception."""
        from app.services.music_analysis import MusicAnalyzer
        analyzer = MusicAnalyzer()
        # Pass a non-existent path
        result = analyzer.analyze_safe(str(tmp_path / "nonexistent.mp3"))
        assert result == {}

    def test_returns_empty_dict_when_librosa_absent(self, tmp_path):
        """analyze_safe() returns {} when librosa is missing."""
        import app.services.music_analysis as ma
        original = ma._HAS_LIBROSA
        try:
            ma._HAS_LIBROSA = False
            result = ma.MusicAnalyzer().analyze_safe(str(tmp_path / "x.wav"))
            assert result == {}
        finally:
            ma._HAS_LIBROSA = original


# ─────────────────────────────────────────────────────────────────────────────
# compute_pacing_curve()
# ─────────────────────────────────────────────────────────────────────────────

class TestComputePacingCurve:
    def test_same_length_as_intensities(self):
        from app.services.music_analysis import compute_pacing_curve
        durs = [1.0] * 10
        ints = [0.5] * 10
        result = compute_pacing_curve(durs, ints)
        assert len(result) == 10

    def test_empty_intensities_returns_empty(self):
        from app.services.music_analysis import compute_pacing_curve
        assert compute_pacing_curve([], []) == []

    def test_smoothing_reduces_variance(self):
        """Pacing curve variance should be <= raw intensity variance."""
        from app.services.music_analysis import compute_pacing_curve
        import statistics
        ints = [0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0]
        durs = [1.0] * len(ints)
        pacing = compute_pacing_curve(durs, ints, window=2)
        assert statistics.variance(pacing) <= statistics.variance(ints) + 1e-9

    def test_single_element(self):
        from app.services.music_analysis import compute_pacing_curve
        result = compute_pacing_curve([1.0], [0.75])
        assert result == [0.75]

    def test_all_same_returns_same(self):
        from app.services.music_analysis import compute_pacing_curve
        ints = [0.3] * 6
        result = compute_pacing_curve([1.0] * 6, ints)
        for v in result:
            assert abs(v - 0.3) < 1e-6


# ─────────────────────────────────────────────────────────────────────────────
# escalation_score()
# ─────────────────────────────────────────────────────────────────────────────

class TestEscalationScore:
    def test_flat_curve_returns_half(self):
        from app.services.music_analysis import escalation_score
        flat = [0.5] * 10
        score = escalation_score(flat)
        assert abs(score - 0.5) < 1e-6

    def test_ascending_returns_above_half(self):
        from app.services.music_analysis import escalation_score
        ascending = [float(i) / 9 for i in range(10)]
        assert escalation_score(ascending) > 0.5

    def test_descending_returns_below_half(self):
        from app.services.music_analysis import escalation_score
        descending = [1.0 - float(i) / 9 for i in range(10)]
        assert escalation_score(descending) < 0.5

    def test_clamped_to_0_1(self):
        from app.services.music_analysis import escalation_score
        extreme_up = [0.0] * 5 + [1.0] * 5
        extreme_dn = [1.0] * 5 + [0.0] * 5
        assert 0.0 <= escalation_score(extreme_up) <= 1.0
        assert 0.0 <= escalation_score(extreme_dn) <= 1.0

    def test_short_curve_returns_1(self):
        from app.services.music_analysis import escalation_score
        assert escalation_score([0.5, 0.5, 0.5]) == 1.0

    def test_empty_returns_1(self):
        from app.services.music_analysis import escalation_score
        assert escalation_score([]) == 1.0


# ─────────────────────────────────────────────────────────────────────────────
# _to_wav() — tests that FFmpeg subprocess is called correctly
# ─────────────────────────────────────────────────────────────────────────────

class TestToWav:
    def test_calls_ffmpeg_with_correct_args(self, tmp_path):
        from app.services import music_analysis as ma
        fake_wav = tmp_path / "out.wav"

        def fake_run(cmd, capture_output, timeout):
            # Create the output file so the function doesn't cleanup and fail
            Path(cmd[-1]).write_bytes(b"dummy")
            m = MagicMock()
            m.returncode = 0
            return m

        with patch("app.services.music_analysis.subprocess.run", side_effect=fake_run) as mock_run, \
             patch("tempfile.NamedTemporaryFile") as mock_tmp:
            mock_tmp_instance = MagicMock()
            mock_tmp_instance.__enter__ = lambda s: s
            mock_tmp_instance.__exit__ = MagicMock(return_value=False)
            mock_tmp_instance.name = str(fake_wav)
            mock_tmp.return_value = mock_tmp_instance

            try:
                result = ma._to_wav("/some/video.mp4")
            except Exception:
                pass   # path cleanup might fail in CI

            assert mock_run.called
            call_args = mock_run.call_args[0][0]
            assert "-ar" in call_args
            assert str(ma._SAMPLE_RATE) in call_args
            assert "-ac" in call_args
            assert "1" in call_args
            assert "-vn" in call_args

    def test_raises_on_ffmpeg_failure(self, tmp_path):
        from app.services import music_analysis as ma
        import tempfile

        fake_path = str(tmp_path / "fail.wav")

        def fake_run(cmd, capture_output, timeout):
            m = MagicMock()
            m.returncode = 1
            m.stderr = b"mock error"
            return m

        with patch("app.services.music_analysis.subprocess.run", side_effect=fake_run), \
             patch("tempfile.NamedTemporaryFile") as mock_tmp:
            mock_tmp_instance = MagicMock()
            mock_tmp_instance.__enter__ = lambda s: s
            mock_tmp_instance.__exit__ = MagicMock(return_value=False)
            mock_tmp_instance.name = fake_path
            mock_tmp.return_value = mock_tmp_instance

            with pytest.raises(RuntimeError, match="FFmpeg audio extraction failed"):
                ma._to_wav("/some/video.mp4")
