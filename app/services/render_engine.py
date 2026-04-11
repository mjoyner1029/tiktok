"""FFmpeg Render Engine — transforms a RenderContract JSON into a finished MP4.

Pipeline:
  1. Normalize source clips (fps, resolution, codec)
  2. Build filter graph from edit spec tracks
  3. Burn captions via ASS subtitles
  4. Mix audio tracks with ducking
  5. Render final 1080×1920 MP4
  6. Generate thumbnail

All filter construction follows FFmpeg's documented filter syntax.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# ═══════════════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _run(cmd: List[str], timeout: int = 600) -> subprocess.CompletedProcess:
    logger.debug("CMD: %s", " ".join(shlex.quote(c) for c in cmd))
    # Validate that the binary is ffmpeg/ffprobe — block arbitrary commands
    binary = cmd[0] if cmd else ""
    allowed = {settings.ffmpeg_binary, settings.ffprobe_binary}
    if binary not in allowed:
        raise ValueError(f"Refusing to execute unknown binary: {binary}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        logger.error("FFmpeg stderr:\n%s", result.stderr[-3000:])
        raise RuntimeError(f"FFmpeg failed (rc={result.returncode}): {result.stderr[-500:]}")
    return result


def _ensure_dir(path: str) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return path


# ═══════════════════════════════════════════════════════════════════════════
#  CLIP PREPROCESSING
# ═══════════════════════════════════════════════════════════════════════════

def normalize_clip(
    input_path: str,
    output_path: str,
    target_width: int = 1080,
    target_height: int = 1920,
    target_fps: int = 30,
) -> str:
    """Normalize a clip: re-encode to consistent fps, resolution, codec.

    Smart-center crops to 9:16 if aspect ratio differs.
    """
    _ensure_dir(output_path)

    # Scale + crop to target aspect ratio (center crop)
    # 1. Scale so smallest dimension fills target
    # 2. Crop to exact target
    vf = (
        f"scale=w='if(gt(a,{target_width}/{target_height}),{target_height}*a,{target_width})':"
        f"h='if(gt(a,{target_width}/{target_height}),{target_height},{target_width}/a)',"
        f"crop={target_width}:{target_height},"
        f"fps={target_fps},"
        f"format=yuv420p"
    )

    cmd = [
        settings.ffmpeg_binary, "-y",
        "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        output_path,
    ]
    _run(cmd)
    return output_path


# ═══════════════════════════════════════════════════════════════════════════
#  TRIM
# ═══════════════════════════════════════════════════════════════════════════

def trim_clip(
    input_path: str,
    output_path: str,
    start: float,
    end: float,
    speed: float = 1.0,
) -> str:
    """Trim a clip from source_in to source_out, optionally change speed."""
    _ensure_dir(output_path)
    duration = end - start

    cmd = [
        settings.ffmpeg_binary, "-y",
        "-ss", f"{start:.3f}",
        "-i", input_path,
        "-t", f"{duration:.3f}",
    ]

    if speed != 1.0:
        # Video speed + audio speed
        vf = f"setpts={1.0/speed}*PTS"
        af = f"atempo={speed}" if 0.5 <= speed <= 2.0 else f"atempo={min(speed, 2.0)}"
        cmd += ["-vf", vf, "-af", af]

    cmd += [
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ]
    _run(cmd)
    return output_path


# ═══════════════════════════════════════════════════════════════════════════
#  ZOOM / MOTION
# ═══════════════════════════════════════════════════════════════════════════

def apply_motion(
    input_path: str,
    output_path: str,
    motion_type: str = "static",
    strength: float = 0.05,
    target_width: int = 1080,
    target_height: int = 1920,
) -> str:
    """Apply zoom/pan motion to a clip using FFmpeg's zoompan filter.

    Supported types: zoom_in, zoom_out, slow_push, slow_pull, shake, static.
    """
    _ensure_dir(output_path)

    if motion_type == "static":
        # No motion — just copy
        cmd = [settings.ffmpeg_binary, "-y", "-i", input_path, "-c", "copy", output_path]
        _run(cmd)
        return output_path

    # For motion, we upscale slightly and animate the crop window
    pad = int(max(target_width, target_height) * strength)
    sw = target_width + pad * 2
    sh = target_height + pad * 2

    if motion_type in ("zoom_in", "slow_push"):
        # Zoom in: start at full view, end zoomed
        vf = (
            f"scale={sw}:{sh},"
            f"zoompan=z='1+{strength}*on/duration':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':"
            f"d=1:s={target_width}x{target_height}:fps={settings.export_fps}"
        )
    elif motion_type in ("zoom_out", "slow_pull"):
        # Zoom out: start zoomed, end at full view
        vf = (
            f"scale={sw}:{sh},"
            f"zoompan=z='{1+strength}-{strength}*on/duration':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':"
            f"d=1:s={target_width}x{target_height}:fps={settings.export_fps}"
        )
    elif motion_type == "shake":
        # Slight random offset for energy
        offset_x = pad // 2
        offset_y = pad // 2
        vf = (
            f"scale={sw}:{sh},"
            f"crop={target_width}:{target_height}:"
            f"'({pad}+{offset_x}*sin(t*15))':'{pad}+{offset_y}*cos(t*12))'"
        )
    else:
        vf = f"scale={target_width}:{target_height}"

    cmd = [
        settings.ffmpeg_binary, "-y",
        "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    _run(cmd)
    return output_path


# ═══════════════════════════════════════════════════════════════════════════
#  CAPTION / SUBTITLE GENERATION
# ═══════════════════════════════════════════════════════════════════════════

def _seconds_to_ass_time(seconds: float) -> str:
    """Convert seconds to ASS timestamp format: H:MM:SS.cc"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def generate_ass_subtitles(
    text_tracks: List[Dict[str, Any]],
    output_path: str,
    video_width: int = 1080,
    video_height: int = 1920,
) -> str:
    """Generate an ASS subtitle file from text track clips."""
    _ensure_dir(output_path)

    # ASS header
    header = f"""[Script Info]
Title: TikTok Captions
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: {video_height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial Black,64,&H00FFFFFF,&H000000FF,&H00000000,&H88000000,-1,0,0,0,100,100,0,0,3,3,0,2,40,40,180,1
Style: LowerThird,Arial Black,64,&H00FFFFFF,&H000000FF,&H00000000,&H88000000,-1,0,0,0,100,100,0,0,3,3,0,2,40,40,180,1
Style: UpperThird,Arial Black,64,&H00FFFFFF,&H000000FF,&H00000000,&H88000000,-1,0,0,0,100,100,0,0,3,3,0,8,40,40,180,1
Style: Center,Arial Black,72,&H00FFFFFF,&H000000FF,&H00000000,&H88000000,-1,0,0,0,100,100,0,0,3,4,0,5,40,40,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = []
    for clip in text_tracks:
        start = _seconds_to_ass_time(clip["start"])
        end = _seconds_to_ass_time(clip["end"])
        text = clip.get("text", "")
        position = clip.get("position", "lower_third")

        # Map position to ASS style
        style_map = {
            "lower_third": "LowerThird",
            "upper_third": "UpperThird",
            "center": "Center",
            "top": "UpperThird",
            "bottom": "LowerThird",
        }
        style_name = style_map.get(position, "Default")

        # Animation: pop effect via transform
        animation = clip.get("animation", "pop")
        if animation == "pop":
            text = r"{\fscx120\fscy120\t(0,80,\fscx100\fscy100)}" + text
        elif animation == "typewriter":
            # Approximate typewriter with fade
            text = r"{\fad(0,0)\alpha&HFF\t(0,100,\alpha&H00)}" + text
        elif animation == "slide_up":
            text = r"{\move(540,1200,540,1100,0,150)}" + text
        elif animation == "fade":
            text = r"{\fad(150,150)}" + text

        events.append(f"Dialogue: 0,{start},{end},{style_name},,0,0,0,,{text}")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(header)
        f.write("\n".join(events))
        f.write("\n")

    return output_path


# ═══════════════════════════════════════════════════════════════════════════
#  CONCATENATION
# ═══════════════════════════════════════════════════════════════════════════

def concat_clips(
    clip_paths: List[str],
    output_path: str,
) -> str:
    """Concatenate clips using FFmpeg's concat demuxer."""
    _ensure_dir(output_path)

    # Write concat list
    list_path = tempfile.mktemp(suffix=".txt")
    with open(list_path, "w") as f:
        for p in clip_paths:
            f.write(f"file '{p}'\n")

    cmd = [
        settings.ffmpeg_binary, "-y",
        "-f", "concat", "-safe", "0",
        "-i", list_path,
        "-c", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    _run(cmd)
    os.unlink(list_path)
    return output_path


# ═══════════════════════════════════════════════════════════════════════════
#  AUDIO MIXING
# ═══════════════════════════════════════════════════════════════════════════

def mix_audio(
    video_path: str,
    audio_path: str,
    output_path: str,
    music_gain_db: float = -18.0,
    duck_under_speech: bool = True,
) -> str:
    """Mix background music under the video's speech audio."""
    _ensure_dir(output_path)

    if duck_under_speech:
        # Use sidechaincompress: duck music when speech is present
        af = (
            f"[1:a]volume={music_gain_db}dB[music];"
            f"[music][0:a]sidechaincompress=threshold=0.02:ratio=6:attack=10:release=300[ducked];"
            f"[0:a][ducked]amix=inputs=2:duration=first:dropout_transition=2"
        )
    else:
        af = (
            f"[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=2,"
            f"volume={music_gain_db}dB"
        )

    cmd = [
        settings.ffmpeg_binary, "-y",
        "-i", video_path,
        "-i", audio_path,
        "-filter_complex", af,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ]
    _run(cmd)
    return output_path


# ═══════════════════════════════════════════════════════════════════════════
#  SUBTITLE BURN-IN
# ═══════════════════════════════════════════════════════════════════════════

def burn_subtitles(
    video_path: str,
    ass_path: str,
    output_path: str,
) -> str:
    """Burn ASS subtitles into the video."""
    _ensure_dir(output_path)
    # FFmpeg subtitles filter requires escaping special chars in path
    safe_ass = ass_path.replace("\\", "/").replace(":", r"\:")

    cmd = [
        settings.ffmpeg_binary, "-y",
        "-i", video_path,
        "-vf", f"ass='{safe_ass}'",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    _run(cmd)
    return output_path


# ═══════════════════════════════════════════════════════════════════════════
#  THUMBNAIL
# ═══════════════════════════════════════════════════════════════════════════

def generate_thumbnail(
    video_path: str,
    output_path: str,
    timestamp: float = 0.5,
) -> str:
    """Extract a single frame as a JPEG thumbnail."""
    _ensure_dir(output_path)
    cmd = [
        settings.ffmpeg_binary, "-y",
        "-ss", f"{timestamp:.3f}",
        "-i", video_path,
        "-vframes", "1",
        "-q:v", "2",
        output_path,
    ]
    _run(cmd)
    return output_path


# ═══════════════════════════════════════════════════════════════════════════
#  SILENCE REMOVAL
# ═══════════════════════════════════════════════════════════════════════════

def remove_silence(
    input_path: str,
    output_path: str,
    silences: List[Dict[str, float]],
    min_gap: float = 0.15,
) -> str:
    """Remove detected silence ranges, keeping a small gap for naturalness."""
    _ensure_dir(output_path)

    if not silences:
        cmd = [settings.ffmpeg_binary, "-y", "-i", input_path, "-c", "copy", output_path]
        _run(cmd)
        return output_path

    from app.services.media_analyzer import get_media_info
    info = get_media_info(input_path)
    total_duration = info["duration_sec"]

    # Build keep-ranges (inverse of silence ranges, with gap padding)
    keep_ranges: List[Tuple[float, float]] = []
    cursor = 0.0
    for s in sorted(silences, key=lambda x: x["start"]):
        if s["start"] > cursor:
            keep_ranges.append((cursor, s["start"] + min_gap))
        cursor = max(cursor, s["end"] - min_gap)
    if cursor < total_duration:
        keep_ranges.append((cursor, total_duration))

    # Trim each range and concatenate
    tmp_dir = tempfile.mkdtemp()
    segment_paths = []
    for i, (start, end) in enumerate(keep_ranges):
        seg_path = os.path.join(tmp_dir, f"seg_{i:04d}.mp4")
        trim_clip(input_path, seg_path, start, end)
        segment_paths.append(seg_path)

    if len(segment_paths) == 1:
        cmd = [settings.ffmpeg_binary, "-y", "-i", segment_paths[0], "-c", "copy", output_path]
        _run(cmd)
    else:
        concat_clips(segment_paths, output_path)

    # Cleanup temp segments
    for p in segment_paths:
        os.unlink(p)
    os.rmdir(tmp_dir)

    return output_path


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN RENDER PIPELINE
# ═══════════════════════════════════════════════════════════════════════════

class RenderEngine:
    """Consumes a RenderContract–style JSON and produces finalised video."""

    def __init__(self, asset_resolver=None):
        """
        Args:
            asset_resolver: callable(asset_id) -> local file path
        """
        self.asset_resolver = asset_resolver or (lambda x: x)
        self.work_dir = Path(tempfile.mkdtemp(prefix="tiktok_render_"))
        self.output_dir = Path(settings.render_output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def cleanup(self):
        """Remove temporary work directory and all intermediate files."""
        import shutil
        if self.work_dir.exists():
            shutil.rmtree(self.work_dir, ignore_errors=True)

    def render(self, spec: Dict[str, Any]) -> Dict[str, str]:
        """Execute the full render pipeline from an edit spec.

        Returns:
            {
                "output_path": "/path/to/final.mp4",
                "thumbnail_path": "/path/to/thumb.jpg",
                "subtitle_path": "/path/to/captions.ass",
            }
        """
        try:
            return self._render_impl(spec)
        finally:
            self.cleanup()

    def _render_impl(self, spec: Dict[str, Any]) -> Dict[str, str]:
        project_id = spec.get("project_id", uuid.uuid4().hex[:8])
        output_cfg = spec.get("output", {})
        width = output_cfg.get("width", settings.export_width)
        height = output_cfg.get("height", settings.export_height)
        fps = output_cfg.get("fps", settings.export_fps)
        tracks = spec.get("tracks", {})

        video_clips = tracks.get("video", [])
        text_clips = tracks.get("text", [])
        audio_clips = tracks.get("audio", [])

        logger.info(
            "Rendering project %s: %d video clips, %d captions, %d audio tracks",
            project_id, len(video_clips), len(text_clips), len(audio_clips),
        )

        # ── Step 1: Process video clips ──────────────────────────────────
        processed_clips: List[str] = []
        for i, vc in enumerate(video_clips):
            src = self.asset_resolver(vc["asset_id"])
            logger.info("  Processing clip %d: %s [%.2f–%.2f]", i, vc["asset_id"], vc.get("source_in", 0), vc.get("source_out", 0))

            # Trim
            trimmed = str(self.work_dir / f"trim_{i:03d}.mp4")
            trim_clip(
                src, trimmed,
                start=vc.get("source_in", 0.0),
                end=vc.get("source_out", vc.get("source_in", 0) + (vc["end"] - vc["start"])),
                speed=vc.get("speed", 1.0),
            )

            # Normalize to target resolution
            normalised = str(self.work_dir / f"norm_{i:03d}.mp4")
            normalize_clip(trimmed, normalised, width, height, fps)

            # Apply motion
            motion = vc.get("motion", {})
            motion_type = motion.get("type", "static") if isinstance(motion, dict) else "static"
            motion_strength = motion.get("strength", 0.05) if isinstance(motion, dict) else 0.05

            if motion_type != "static":
                motion_out = str(self.work_dir / f"motion_{i:03d}.mp4")
                apply_motion(normalised, motion_out, motion_type, motion_strength, width, height)
                processed_clips.append(motion_out)
            else:
                processed_clips.append(normalised)

        # ── Step 2: Concatenate video clips ──────────────────────────────
        if len(processed_clips) == 0:
            raise ValueError("No video clips in edit spec")
        elif len(processed_clips) == 1:
            concat_path = processed_clips[0]
        else:
            concat_path = str(self.work_dir / "concat.mp4")
            concat_clips(processed_clips, concat_path)

        # ── Step 3: Generate & burn captions ─────────────────────────────
        if text_clips:
            ass_path = str(self.work_dir / "captions.ass")
            generate_ass_subtitles(text_clips, ass_path, width, height)

            captioned_path = str(self.work_dir / "captioned.mp4")
            burn_subtitles(concat_path, ass_path, captioned_path)
            current = captioned_path
        else:
            ass_path = None
            current = concat_path

        # ── Step 4: Mix audio ────────────────────────────────────────────
        for ac in audio_clips:
            audio_src = self.asset_resolver(ac["asset_id"])
            if audio_src and os.path.exists(audio_src):
                mixed_path = str(self.work_dir / f"mixed_{uuid.uuid4().hex[:6]}.mp4")
                mix_audio(
                    current, audio_src, mixed_path,
                    music_gain_db=ac.get("gain_db", -18),
                    duck_under_speech=ac.get("duck_under_speech", True),
                )
                current = mixed_path

        # ── Step 5: Final export ─────────────────────────────────────────
        final_name = f"{project_id}_{uuid.uuid4().hex[:8]}.mp4"
        final_path = str(self.output_dir / final_name)

        cmd = [
            settings.ffmpeg_binary, "-y",
            "-i", current,
            "-c:v", "libx264", "-preset", "medium",
            "-b:v", output_cfg.get("video_bitrate", settings.export_video_bitrate),
            "-c:a", "aac",
            "-b:a", output_cfg.get("audio_bitrate", settings.export_audio_bitrate),
            "-movflags", "+faststart",
            final_path,
        ]
        _run(cmd)

        # ── Step 6: Thumbnail ────────────────────────────────────────────
        thumb_path = str(self.output_dir / f"{project_id}_thumb.jpg")
        generate_thumbnail(final_path, thumb_path, timestamp=0.5)

        logger.info("Render complete: %s", final_path)

        result = {
            "output_path": final_path,
            "thumbnail_path": thumb_path,
        }
        if ass_path:
            # Copy ASS to output dir
            final_ass = str(self.output_dir / f"{project_id}_captions.ass")
            import shutil
            shutil.copy2(ass_path, final_ass)
            result["subtitle_path"] = final_ass

        return result
