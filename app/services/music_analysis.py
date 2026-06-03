"""Music analysis module — BPM detection, beat grid, downbeats, phrase boundaries,
energy/intensity envelopes and transient peaks.

Works with any audio or video file (audio is extracted to WAV via FFmpeg when the
input is a video).  Gracefully degrades to an empty result when librosa is not
installed.

Public API
----------
    >>> analyzer = MusicAnalyzer()
    >>> result   = analyzer.analyze("/path/to/song.mp3")
    # or the convenience wrapper
    >>> result   = analyze_audio_file("/path/to/song.mp3")

Return shape
------------
    {
        "tempo_bpm":          float,        # beats per minute
        "beat_count":         int,
        "beat_grid":          list[float],  # timestamp of every beat (s)
        "downbeats":          list[float],  # every 4th beat
        "phrase_boundaries":  list[float],  # every 16th beat (4 bars at 4/4)
        "energy_curve":       list[float],  # RMS per beat interval (0–1)
        "intensity_curve":    list[float],  # spectral centroid per beat (0–1)
        "transient_peaks":    list[float],  # onset timestamps (s)
        "duration_sec":       float,
    }
"""
from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Optional heavy deps — graceful degradation when absent
try:
    import librosa as _librosa
    import numpy as _np
    _HAS_LIBROSA = True
except ImportError:  # pragma: no cover
    _HAS_LIBROSA = False

# FFmpeg binary — prefer Homebrew full build on macOS
_FFMPEG = (
    "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
    if Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg").exists()
    else "ffmpeg"
)

# ── Analysis constants ────────────────────────────────────────────────────────
_SAMPLE_RATE  = 22050   # Hz; matches librosa default
_HOP_LENGTH   = 512     # STFT hop in frames
_PHRASE_BEATS = 16      # beats per phrase  (4 bars × 4/4 time)
_VIDEO_EXTS   = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".flv"}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _to_wav(file_path: str) -> str:
    """Extract audio from *file_path* to a temporary mono 22 050 Hz WAV.

    Returns the path to the temporary file; the caller is responsible for
    deleting it after use.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    wav_path = tmp.name
    tmp.close()
    result = subprocess.run(
        [_FFMPEG, "-y", "-i", file_path,
         "-ar", str(_SAMPLE_RATE), "-ac", "1", "-vn", wav_path],
        capture_output=True,
        timeout=120,
    )
    if result.returncode != 0:
        Path(wav_path).unlink(missing_ok=True)
        raise RuntimeError(
            f"FFmpeg audio extraction failed: {result.stderr.decode()[-400:]}"
        )
    return wav_path


def _sample_envelope(
    envelope: list[float],
    beat_frames: Any,
    total_frames: int,
) -> list[float]:
    """Sample *envelope* at each frame index listed in *beat_frames*."""
    values: list[float] = []
    for bf in beat_frames:
        idx = int(bf)
        if idx < total_frames:
            values.append(float(envelope[idx]))
        elif envelope:
            values.append(float(envelope[-1]))
        else:
            values.append(0.0)
    return values


# ── MusicAnalyzer ─────────────────────────────────────────────────────────────

class MusicAnalyzer:
    """Analyse an audio or video file and return structured music metadata.

    ``librosa`` **must** be installed for :meth:`analyze` to succeed.  Use
    :meth:`analyze_safe` in production paths where the dependency may be absent
    or the file may not contain an audio stream.
    """

    def analyze(self, file_path: str) -> dict[str, Any]:
        """Run full music analysis and return structured metadata.

        Args:
            file_path: Path to an audio file (mp3, wav, flac, aac, m4a, ogg …)
                       or a video file — the audio stream is extracted
                       automatically via FFmpeg.

        Returns:
            Dict with keys: tempo_bpm, beat_count, beat_grid, downbeats,
            phrase_boundaries, energy_curve, intensity_curve, transient_peaks,
            duration_sec.

        Raises:
            RuntimeError: When librosa is not installed.
        """
        if not _HAS_LIBROSA:
            raise RuntimeError(
                "librosa is required for music analysis.  "
                "Install it with:  pip install librosa soundfile"
            )

        # ── Load audio ────────────────────────────────────────────────────
        suffix = Path(file_path).suffix.lower()
        is_video = suffix in _VIDEO_EXTS

        wav_path: str | None = _to_wav(file_path) if is_video else None
        src = wav_path or file_path
        try:
            y, sr = _librosa.load(src, sr=_SAMPLE_RATE, mono=True)
        finally:
            if wav_path:
                Path(wav_path).unlink(missing_ok=True)

        duration = float(len(y)) / sr

        # ── BPM + beat grid ───────────────────────────────────────────────
        tempo_raw, beat_frames = _librosa.beat.beat_track(
            y=y, sr=sr, hop_length=_HOP_LENGTH
        )
        tempo_bpm = float(tempo_raw[0]) if hasattr(tempo_raw, "__len__") else float(tempo_raw)
        beat_times: list[float] = (
            _librosa.frames_to_time(beat_frames, sr=sr, hop_length=_HOP_LENGTH).tolist()
        )

        # ── Downbeats: every 4th beat (approximation without Madmom) ─────
        downbeats = [beat_times[i] for i in range(0, len(beat_times), 4)]

        # ── Phrase boundaries: every 16 beats (= 4 bars at 4/4) ──────────
        phrase_boundaries = [
            beat_times[i] for i in range(0, len(beat_times), _PHRASE_BEATS)
        ]

        # ── Energy curve — RMS per beat interval, normalised 0–1 ─────────
        rms_arr = _librosa.feature.rms(y=y, hop_length=_HOP_LENGTH)[0]
        rms_max = float(_np.max(rms_arr)) or 1.0
        rms_norm: list[float] = (rms_arr / rms_max).tolist()
        energy_curve = _sample_envelope(rms_norm, beat_frames, len(rms_arr))

        # ── Intensity curve — spectral centroid, normalised 0–1 ──────────
        cent_arr = _librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=_HOP_LENGTH)[0]
        cent_max = float(_np.max(cent_arr)) or 1.0
        cent_norm: list[float] = (cent_arr / cent_max).tolist()
        intensity_curve = _sample_envelope(cent_norm, beat_frames, len(cent_arr))

        # ── Transient peaks (onset detection) ────────────────────────────
        onset_frames = _librosa.onset.onset_detect(y=y, sr=sr, hop_length=_HOP_LENGTH)
        transient_peaks: list[float] = (
            _librosa.frames_to_time(onset_frames, sr=sr, hop_length=_HOP_LENGTH).tolist()
        )

        return {
            "tempo_bpm":         round(tempo_bpm, 1),
            "beat_count":        len(beat_times),
            "beat_grid":         [round(t, 3) for t in beat_times],
            "downbeats":         [round(t, 3) for t in downbeats],
            "phrase_boundaries": [round(t, 3) for t in phrase_boundaries],
            "energy_curve":      [round(v, 4) for v in energy_curve],
            "intensity_curve":   [round(v, 4) for v in intensity_curve],
            "transient_peaks":   [round(t, 3) for t in transient_peaks],
            "duration_sec":      round(duration, 3),
        }

    def analyze_safe(self, file_path: str) -> dict[str, Any]:
        """Like :meth:`analyze` but never raises.

        Returns an empty ``{}`` on any failure (missing librosa, corrupt file,
        audio-free video, etc.).
        """
        try:
            return self.analyze(file_path)
        except Exception as exc:
            logger.debug("MusicAnalyzer.analyze_safe failed for %r: %s", file_path, exc)
            return {}


# ── Module-level convenience functions ───────────────────────────────────────

def analyze_audio_file(file_path: str) -> dict[str, Any]:
    """Convenience wrapper around ``MusicAnalyzer().analyze(file_path)``."""
    return MusicAnalyzer().analyze(file_path)


def compute_pacing_curve(
    clip_durations: list[float],
    clip_intensities: list[float],
    window: int = 3,
) -> list[float]:
    """Smooth a per-clip intensity list with a sliding-window average.

    Args:
        clip_durations:   Clip durations in seconds (kept for API symmetry;
                          not used in the current implementation).
        clip_intensities: Per-clip intensity scores (0–1).
        window:           Half-width of the averaging window (clips).

    Returns:
        Smoothed intensity curve, same length as *clip_intensities*.
    """
    n = len(clip_intensities)
    if n == 0:
        return []
    result: list[float] = []
    for i in range(n):
        lo = max(0, i - window)
        hi = min(n, i + window + 1)
        chunk = clip_intensities[lo:hi]
        result.append(round(sum(chunk) / len(chunk), 4))
    return result


def escalation_score(pacing: list[float]) -> float:
    """Return a 0–1 escalation score.

    1.0 means the second half of the timeline is maximally more intense than
    the first half.  0.5 means the two halves are equal.  Values below 0.5
    indicate the video loses energy toward the end.
    """
    if len(pacing) < 4:
        return 1.0
    mid        = len(pacing) // 2
    first_avg  = sum(pacing[:mid]) / mid
    second_avg = sum(pacing[mid:]) / (len(pacing) - mid)
    delta      = second_avg - first_avg
    return float(max(0.0, min(1.0, 0.5 + delta)))
