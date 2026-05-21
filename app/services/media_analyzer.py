"""Media analysis service — probe, transcribe, silence detection.

Uses FFprobe for media metadata and OpenAI Whisper for transcription.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# ═══════════════════════════════════════════════════════════════════════════
#  FFPROBE — media metadata
# ═══════════════════════════════════════════════════════════════════════════

def probe_media(file_path: str) -> Dict[str, Any]:
    """Return FFprobe JSON for a media file."""
    cmd = [
        settings.ffprobe_binary,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        file_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    return json.loads(result.stdout)


def get_media_info(file_path: str) -> Dict[str, Any]:
    """Extract key metadata: duration, dimensions, codec, fps."""
    data = probe_media(file_path)
    fmt = data.get("format", {})
    duration = float(fmt.get("duration", 0))
    width = height = fps = 0
    has_audio = False
    has_video = False

    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and not has_video:
            has_video = True
            width = int(stream.get("width", 0))
            height = int(stream.get("height", 0))
            # parse fps from r_frame_rate "30/1"
            rfr = stream.get("r_frame_rate", "0/1")
            parts = rfr.split("/")
            if len(parts) == 2 and int(parts[1]) > 0:
                fps = round(int(parts[0]) / int(parts[1]), 2)
        elif stream.get("codec_type") == "audio":
            has_audio = True

    return {
        "duration_sec": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "has_video": has_video,
        "has_audio": has_audio,
        "file_size_bytes": int(fmt.get("size", 0)),
        "format_name": fmt.get("format_name", ""),
        "bit_rate": int(fmt.get("bit_rate", 0)),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  WHISPER TRANSCRIPTION
# ═══════════════════════════════════════════════════════════════════════════

_whisper_model = None


def _load_whisper():
    global _whisper_model
    if _whisper_model is None:
        import whisper
        logger.info("Loading Whisper model '%s' on %s", settings.whisper_model, settings.whisper_device)
        _whisper_model = whisper.load_model(settings.whisper_model, device=settings.whisper_device)
    return _whisper_model


def extract_audio(video_path: str, output_path: str | None = None) -> str:
    """Extract audio from video to a WAV file."""
    if output_path is None:
        output_path = tempfile.mktemp(suffix=".wav")
    cmd = [
        settings.ffmpeg_binary,
        "-y", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le",
        "-ar", "16000", "-ac", "1",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, timeout=120, check=True)
    return output_path


def transcribe(
    file_path: str,
    language: str | None = None,
    word_timestamps: bool = True,
) -> Dict[str, Any]:
    """Transcribe audio/video and return word-level segments.

    Returns:
        {
            "text": "full transcript",
            "segments": [
                {"start": 0.0, "end": 1.2, "text": "Hello world", "words": [...]},
                ...
            ],
            "language": "en"
        }
    """
    model = _load_whisper()

    # If video, extract audio first
    if any(file_path.lower().endswith(ext) for ext in (".mp4", ".mov", ".avi", ".mkv", ".webm")):
        audio_path = extract_audio(file_path)
    else:
        audio_path = file_path

    opts = {"verbose": False, "word_timestamps": word_timestamps}
    if language:
        opts["language"] = language

    result = model.transcribe(audio_path, **opts)

    segments = []
    for seg in result.get("segments", []):
        segment_data = {
            "start": round(seg["start"], 3),
            "end": round(seg["end"], 3),
            "text": seg["text"].strip(),
        }
        if word_timestamps and "words" in seg:
            segment_data["words"] = [
                {
                    "word": w["word"].strip(),
                    "start": round(w["start"], 3),
                    "end": round(w["end"], 3),
                    "probability": round(w.get("probability", 0), 3),
                }
                for w in seg["words"]
            ]
        segments.append(segment_data)

    return {
        "text": result.get("text", "").strip(),
        "segments": segments,
        "language": result.get("language", "en"),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  SILENCE DETECTION
# ═══════════════════════════════════════════════════════════════════════════

def detect_silence(
    file_path: str,
    noise_threshold_db: float = -35.0,
    min_duration: float = 0.3,
) -> List[Dict[str, float]]:
    """Detect silent ranges using FFmpeg's silencedetect filter.

    Returns list of {"start": float, "end": float, "duration": float}.
    """
    cmd = [
        settings.ffmpeg_binary,
        "-i", file_path,
        "-af", f"silencedetect=noise={noise_threshold_db}dB:d={min_duration}",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    stderr = result.stderr

    silences: List[Dict[str, float]] = []
    current_start: Optional[float] = None

    for line in stderr.split("\n"):
        if "silence_start:" in line:
            try:
                current_start = float(line.split("silence_start:")[1].strip().split()[0])
            except (ValueError, IndexError):
                current_start = None
        elif "silence_end:" in line and current_start is not None:
            try:
                parts = line.split("silence_end:")[1].strip().split()
                end = float(parts[0])
                duration = end - current_start
                silences.append({
                    "start": round(current_start, 3),
                    "end": round(end, 3),
                    "duration": round(duration, 3),
                })
            except (ValueError, IndexError):
                pass
            current_start = None

    return silences


# ═══════════════════════════════════════════════════════════════════════════
#  SENTENCE BOUNDARY DETECTION (from transcript)
# ═══════════════════════════════════════════════════════════════════════════

def find_sentence_boundaries(
    segments: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Group word-level segments into sentence-level chunks.

    Uses punctuation-based splitting from the transcript.
    """
    sentences: List[Dict[str, Any]] = []
    current_words: List[str] = []
    current_start: Optional[float] = None

    for seg in segments:
        words = seg.get("words", [])
        if not words:
            # Fallback: treat whole segment as one sentence
            sentences.append({
                "start": seg["start"],
                "end": seg["end"],
                "text": seg["text"],
            })
            continue

        for w in words:
            if current_start is None:
                current_start = w["start"]
            current_words.append(w["word"])

            # Check for sentence-ending punctuation
            if w["word"].rstrip().endswith((".", "!", "?", "…")):
                sentences.append({
                    "start": current_start,
                    "end": w["end"],
                    "text": " ".join(current_words).strip(),
                })
                current_words = []
                current_start = None

    # Flush remaining
    if current_words and current_start is not None:
        sentences.append({
            "start": current_start,
            "end": segments[-1]["end"] if segments else current_start,
            "text": " ".join(current_words).strip(),
        })

    return sentences


# ═══════════════════════════════════════════════════════════════════════════
#  VISUAL STYLE EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════

def extract_visual_style(video_path: str) -> Dict[str, Any]:
    """Extract visual style dimensions from a video using FFmpeg.

    Detects:
    - Cut/scene change timestamps and average shot duration
    - Color grade profile (brightness, contrast, saturation, gamma)

    Returns:
        {
            "cut_timestamps": [float, ...],
            "avg_cut_duration_sec": float,
            "num_cuts": int,
            "color_grade": {
                "brightness": float,  # -1.0 to 1.0
                "contrast": float,    # 0.0 to 2.0
                "saturation": float,  # 0.0 to 2.0
                "gamma": float,       # 0.1 to 10.0
                "luma_avg": float,    # raw 0-255 for reference
            }
        }
    """
    # ── Scene / cut detection ─────────────────────────────────────────────
    # Uses FFmpeg's scene filter to detect scene changes above threshold 0.3
    scene_cmd = [
        settings.ffmpeg_binary,
        "-i", video_path,
        "-vf", r"select=gt(scene\,0.3),showinfo",
        "-vsync", "vfr",
        "-f", "null", "-",
    ]
    scene_result = subprocess.run(scene_cmd, capture_output=True, text=True, timeout=120)
    stderr = scene_result.stderr

    cut_timestamps: List[float] = []
    for line in stderr.split("\n"):
        if "showinfo" in line and "pts_time:" in line:
            try:
                pts_part = line.split("pts_time:")[1].strip().split()[0]
                cut_timestamps.append(round(float(pts_part), 3))
            except (ValueError, IndexError):
                pass

    # Compute average cut duration from detected cuts + total duration
    media_info = get_media_info(video_path)
    total_duration = media_info["duration_sec"]

    if len(cut_timestamps) > 1:
        gaps = [
            cut_timestamps[i + 1] - cut_timestamps[i]
            for i in range(len(cut_timestamps) - 1)
        ]
        avg_cut_duration = round(sum(gaps) / len(gaps), 2)
    elif total_duration > 0:
        avg_cut_duration = round(total_duration, 2)
    else:
        avg_cut_duration = 2.0

    # ── Color stats via signalstats filter ────────────────────────────────
    # Samples 30 evenly-spaced frames for stable averages
    color_cmd = [
        settings.ffmpeg_binary,
        "-i", video_path,
        "-vf", "select='not(mod(n,10))',signalstats",
        "-f", "null", "-",
    ]
    color_result = subprocess.run(color_cmd, capture_output=True, text=True, timeout=120)
    color_stderr = color_result.stderr

    yavg_values: List[float] = []
    yhigh_values: List[float] = []
    ylow_values: List[float] = []
    uavg_values: List[float] = []
    vavg_values: List[float] = []

    for line in color_stderr.split("\n"):
        if "YAVG" in line:
            try:
                for token in line.split():
                    if token.startswith("YAVG:"):
                        yavg_values.append(float(token.split(":")[1]))
                    elif token.startswith("YHIGH:"):
                        yhigh_values.append(float(token.split(":")[1]))
                    elif token.startswith("YLOW:"):
                        ylow_values.append(float(token.split(":")[1]))
                    elif token.startswith("UAVG:"):
                        uavg_values.append(float(token.split(":")[1]))
                    elif token.startswith("VAVG:"):
                        vavg_values.append(float(token.split(":")[1]))
            except (ValueError, IndexError):
                pass

    # Derive eq-filter-compatible parameters from raw stats
    if yavg_values:
        luma_avg = sum(yavg_values) / len(yavg_values)
        # brightness: scale 0-255 → -0.3 to +0.3 offset from neutral (128)
        brightness = round((luma_avg - 128.0) / 255.0 * 0.6, 3)

        if yhigh_values and ylow_values:
            dynamic_range = (sum(yhigh_values) / len(yhigh_values)) - (sum(ylow_values) / len(ylow_values))
            # contrast: 80 = flat, 180 = punchy. Map 80-200 → 0.7-1.4
            contrast = round(max(0.7, min(1.4, dynamic_range / 140.0)), 3)
        else:
            contrast = 1.0

        if uavg_values and vavg_values:
            chroma_u = abs(sum(uavg_values) / len(uavg_values) - 128.0)
            chroma_v = abs(sum(vavg_values) / len(vavg_values) - 128.0)
            chroma_strength = (chroma_u + chroma_v) / 2.0
            # saturation: 0 chroma = 1.0, 40+ chroma = 1.4
            saturation = round(max(0.8, min(1.5, 1.0 + chroma_strength / 100.0)), 3)
        else:
            saturation = 1.0

        # gamma: lifted shadows = < 1.0, crushed = > 1.0
        # If average brightness is high and lows are elevated → lifted look (gamma < 1)
        gamma = 1.0
        if ylow_values:
            low_avg = sum(ylow_values) / len(ylow_values)
            if low_avg > 20:  # lifted blacks
                gamma = round(max(0.7, 1.0 - (low_avg - 20) / 200.0), 3)
    else:
        luma_avg = 128.0
        brightness = 0.0
        contrast = 1.0
        saturation = 1.0
        gamma = 1.0

    return {
        "cut_timestamps": cut_timestamps,
        "avg_cut_duration_sec": avg_cut_duration,
        "num_cuts": len(cut_timestamps),
        "color_grade": {
            "brightness": brightness,
            "contrast": contrast,
            "saturation": saturation,
            "gamma": gamma,
            "luma_avg": round(luma_avg, 1),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
#  FULL ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════

def analyze_asset(file_path: str) -> Dict[str, Any]:
    """Run full analysis: probe + transcribe + silence detect.

    Returns combined result dict.
    """
    info = get_media_info(file_path)
    transcript = transcribe(file_path)
    silences = detect_silence(file_path) if info["has_audio"] else []
    sentences = find_sentence_boundaries(transcript.get("segments", []))

    return {
        "media_info": info,
        "transcript": transcript,
        "silences": silences,
        "sentences": sentences,
    }
