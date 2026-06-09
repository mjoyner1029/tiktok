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

def _run(cmd: List[str], timeout: int = 3600) -> subprocess.CompletedProcess:
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
    # Clamp start to non-negative; ensure minimum duration of 1 frame @ 30 fps
    start = max(0.0, start)
    duration = end - start
    if duration < (1.0 / 30):
        raise ValueError(
            f"trim_clip: degenerate duration {duration:.4f}s "
            f"(start={start}, end={end}) — skipping clip"
        )

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
    crop_anchor: str = "center",
    easing: str = "linear",
    clip_duration: float = 0.0,
) -> str:
    """Apply zoom/pan motion to a clip using FFmpeg's zoompan filter.

    Supported types: zoom_in, zoom_out, slow_push, slow_pull, shake, static.

    Args:
        crop_anchor:    Where to anchor the crop window.
                        One of ``center`` (default), ``top``, ``bottom``,
                        ``left``, ``right``.
        easing:         Animation curve — ``linear`` (default), ``ease_in``,
                        ``ease_out``, ``ease_in_out``.
        clip_duration:  Duration of the trimmed clip in seconds.  Used to
                        build a frame-accurate normalized time expression.
    """
    _ensure_dir(output_path)

    if motion_type == "static":
        # No motion — just copy
        cmd = [settings.ffmpeg_binary, "-y", "-i", input_path, "-c", "copy", output_path]
        _run(cmd)
        return output_path

    # For motion, we upscale slightly and animate the crop window
    pad = int(max(target_width, target_height) * strength)
    sw = target_width  + pad * 2
    sh = target_height + pad * 2

    fps_val      = settings.export_fps
    total_frames = max(1, round(clip_duration * fps_val)) if clip_duration > 0 else fps_val * 3
    denom        = max(total_frames - 1, 1)   # on/denom → 0..1 over the clip

    # Easing expression using zoompan's 'on' variable (output frame number).
    # 'on' is natively supported by this FFmpeg build; 'n'/'t' are not.
    if easing == "ease_out":
        e_expr = f"(1-pow(1-on/{denom},2))"
    elif easing == "ease_in":
        e_expr = f"pow(on/{denom},2)"
    elif easing == "ease_in_out":
        e_expr = f"if(lt(on/{denom},0.5),2*pow(on/{denom},2),1-2*pow(1-on/{denom},2))"
    else:  # linear
        e_expr = f"on/{denom}"

    # Crop anchor x/y (zoompan-native zoom-relative expressions)
    _AX = {
        "center": "(iw-iw/zoom)/2", "top": "(iw-iw/zoom)/2", "bottom": "(iw-iw/zoom)/2",
        "left":   "0",              "right": "iw-iw/zoom",
    }
    _AY = {
        "center": "(ih-ih/zoom)/2", "left": "(ih-ih/zoom)/2", "right": "(ih-ih/zoom)/2",
        "top":    "0",              "bottom": "ih-ih/zoom",
    }
    x_expr = _AX.get(crop_anchor, "(iw-iw/zoom)/2")
    y_expr = _AY.get(crop_anchor, "(ih-ih/zoom)/2")

    if motion_type in ("zoom_in", "slow_push"):
        vf = (
            f"scale={sw}:{sh},"
            f"zoompan=z='1+{strength:.4f}*{e_expr}':x='{x_expr}':y='{y_expr}':"
            f"d=1:s={target_width}x{target_height}:fps={fps_val}"
        )
    elif motion_type in ("zoom_out", "slow_pull"):
        vf = (
            f"scale={sw}:{sh},"
            f"zoompan=z='{1+strength:.4f}-{strength:.4f}*{e_expr}':x='{x_expr}':y='{y_expr}':"
            f"d=1:s={target_width}x{target_height}:fps={fps_val}"
        )
    elif motion_type == "shake":
        # Slight random offset for handheld feel (crop x/y use t which is supported)
        offset_x = pad // 2
        offset_y = pad // 2
        vf = (
            f"scale={sw}:{sh},"
            f"crop={target_width}:{target_height}:"
            f"({pad}+{offset_x}*sin(t*15)):({pad}+{offset_y}*cos(t*12))"
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


def _ass_colour(hex_rgb: str = "FFFFFF", alpha: int = 0) -> str:
    """Convert #RRGGBB hex to ASS &HAABBGGRR colour string."""
    h = hex_rgb.lstrip("#")
    if len(h) == 6:
        r, g, b = h[0:2], h[2:4], h[4:6]
    else:
        r, g, b = "FF", "FF", "FF"
    aa = f"{alpha:02X}"
    return f"&H{aa}{b}{g}{r}"


def _ass_bold(font_weight: str) -> int:
    """Return ASS Bold flag.  ``black`` weight uses the font name; others use flag."""
    return -1 if font_weight in ("bold", "black") else 0


def _eff_font_family(font_family: str, font_weight: str) -> str:
    """For 'black' weight, append ' Black' to font name if not already present."""
    if font_weight == "black" and "Black" not in font_family:
        return font_family.rstrip() + " Black"
    return font_family


def generate_ass_subtitles(
    text_tracks: List[Dict[str, Any]],
    output_path: str,
    video_width: int = 1080,
    video_height: int = 1920,
    render_style: Optional[Dict[str, Any]] = None,
) -> str:
    """Generate an ASS subtitle file from text track clips.

    Per-clip typography fields (font_family, font_size, font_weight, text_case,
    stroke_width, shadow, tracking, animation, animation_duration, emphasis_words)
    are read from each text-track dict (populated by CaptionEvent.to_render_spec).
    When present they override the profile defaults from
    ``render_style["caption_preset"]``.

    ASS animation tags produced per ``animation``:
    - ``pop``       — scale from 120 % → 100 % over *animation_duration* ms
    - ``fade``      — fade in/out over *animation_duration* ms each edge
    - ``slide_up``  — move from 100 px below final position over *animation_duration* ms
    - ``typewriter``— character-by-character fade approximation via \fad
    - ``none``      — no tag

    Args:
        render_style: Optional RenderStyle dict.  When provided,
                      ``caption_preset`` supplies default typography for every
                      clip, and ``safe_zone_frac`` drives the edge margin.
    """
    _ensure_dir(output_path)

    # ── Derive profile-level caption defaults from render_style ──────────
    cap_preset: dict[str, Any] = {}
    if render_style is not None:
        cap_preset = dict(render_style.get("caption_preset") or {})

    # Safe-zone margin — defaults to 180 px (clears TikTok nav bar)
    margin_v_edge: int = 180
    if render_style is not None:
        from app.services.style_renderer import StyleRenderer
        computed = StyleRenderer.get_caption_safe_zone(render_style, video_height)
        if computed > 0:
            margin_v_edge = computed

    # ── Resolve profile-level style values ───────────────────────────────
    p_font_family  = str(cap_preset.get("font_family", "Arial Black"))
    p_font_weight  = str(cap_preset.get("font_weight", "bold"))
    p_font_size    = int(cap_preset.get("font_size", 64))
    p_stroke_width = float(cap_preset.get("stroke_width", 3.0))
    p_shadow       = bool(cap_preset.get("shadow", False))
    p_tracking     = float(cap_preset.get("tracking", 0.5))

    eff_family = _eff_font_family(p_font_family, p_font_weight)
    eff_bold   = _ass_bold(p_font_weight)
    eff_stroke = round(p_stroke_width, 1)
    eff_shadow_v = 1 if p_shadow else 0
    eff_spacing  = round(p_tracking, 1)

    # Outline colour is semi-transparent black; BackColour is box-shadow when
    # BorderStyle=3, but we use BorderStyle=1 (outline only) everywhere.
    outline_colour = _ass_colour("000000")
    back_colour    = _ass_colour("000000", alpha=0x88)

    # ── ASS header + style block ─────────────────────────────────────────
    #   Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour,
    #           OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut,
    #           ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow,
    #           Alignment, MarginL, MarginR, MarginV, Encoding
    def _style_line(name: str, size: int, align: int, margin_v: int,
                    stroke: float = eff_stroke, shadow: int = eff_shadow_v,
                    spacing: float = eff_spacing) -> str:
        return (
            f"Style: {name},{eff_family},{size},"
            f"&H00FFFFFF,&H000000FF,{outline_colour},{back_colour},"
            f"{eff_bold},0,0,0,100,100,{spacing:.1f},0,"
            f"1,{stroke:.1f},{shadow},{align},40,40,{margin_v},1"
        )

    header = f"""[Script Info]
Title: TikTok Captions
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: {video_height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{_style_line("Default",    p_font_size,      2, margin_v_edge)}
{_style_line("LowerThird", p_font_size,      2, margin_v_edge)}
{_style_line("UpperThird", p_font_size,      8, margin_v_edge)}
{_style_line("Center",     p_font_size + 8,  5, 40)}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = []
    for clip in text_tracks:
        start = _seconds_to_ass_time(clip["start"])
        end   = _seconds_to_ass_time(clip["end"])
        text  = clip.get("text", "")

        position = clip.get("position", "lower_third")
        style_map = {
            "lower_third": "LowerThird",
            "upper_third": "UpperThird",
            "center":      "Center",
            "top":         "UpperThird",
            "bottom":      "LowerThird",
        }
        style_name = style_map.get(position, "Default")

        # ── Per-clip typography (may override profile defaults) ───────────
        c_font_family  = clip.get("font_family",  p_font_family)
        c_font_weight  = clip.get("font_weight",  p_font_weight)
        c_font_size    = int(clip.get("font_size",    p_font_size))
        c_stroke_width = float(clip.get("stroke_width", p_stroke_width))
        c_shadow       = bool(clip.get("shadow",       p_shadow))
        c_tracking     = float(clip.get("tracking",     p_tracking))
        animation      = clip.get("animation",     cap_preset.get("animation", "pop"))
        anim_dur       = int(clip.get("animation_duration",
                                      cap_preset.get("animation_duration", 200)))
        emphasis_words = clip.get("emphasis_words") or []

        # ── Reference-matched visual style fields ─────────────────────────
        c_text_color      = clip.get("text_color", "white")
        c_background_box  = bool(clip.get("background_box", False))
        c_bg_color        = clip.get("background_color", "black")
        c_bg_opacity      = float(clip.get("background_opacity", 0.6))
        c_y_pct           = clip.get("y_position_percent")

        # text_case is already applied by to_render_spec; re-apply only for
        # clips that arrive without pre-processing (e.g. legacy callers).
        tc = clip.get("text_case", "asis")
        if tc == "uppercase":
            text = text.upper()
        elif tc == "lowercase":
            text = text.lower()
        elif tc == "titlecase":
            text = text.title()

        # ── Per-clip style override tags (only when clip differs from profile) ─
        override_tags = ""
        clip_eff_family = _eff_font_family(c_font_family, c_font_weight)
        clip_eff_bold   = _ass_bold(c_font_weight)
        if clip_eff_family != eff_family or clip_eff_bold != eff_bold:
            override_tags += f"\\fn{clip_eff_family}\\b{1 if clip_eff_bold else 0}"
        if c_font_size != p_font_size:
            override_tags += f"\\fs{c_font_size}"
        if abs(c_stroke_width - p_stroke_width) > 0.05:
            override_tags += f"\\bord{c_stroke_width:.1f}"
        if abs(c_tracking - p_tracking) > 0.05:
            override_tags += f"\\fsp{c_tracking:.1f}"
        if c_shadow != p_shadow:
            override_tags += f"\\shad{1 if c_shadow else 0}"

        # ── Text color override ───────────────────────────────────────────
        _TEXT_COLOR_MAP = {
            "white": "FFFFFF", "yellow": "00FFFF", "black": "000000",
            "red": "0000FF", "blue": "FF0000", "green": "00FF00",
        }
        hex_color = _TEXT_COLOR_MAP.get(c_text_color.lower(), "FFFFFF")
        if hex_color != "FFFFFF":  # only emit tag when non-default
            # ASS color is &H00BBGGRR (note: BGR order)
            r = hex_color[0:2]; g = hex_color[2:4]; b = hex_color[4:6]
            ass_primary = f"&H00{b}{g}{r}"
            override_tags += f"\\1c{ass_primary}"

        # ── Background box ────────────────────────────────────────────────
        # BorderStyle 3 = opaque box, 4 = shadow box.
        # We switch per-event by writing an \shad override and emitting a
        # {\bord0\shad0\3c<color>\4a<alpha>} prefix that simulates a box.
        if c_background_box:
            alpha_hex = format(max(0, min(255, int(255 * (1 - c_bg_opacity)))), "02X")
            bg_hex = _TEXT_COLOR_MAP.get(c_bg_color.lower(), "000000")
            r2 = bg_hex[0:2]; g2 = bg_hex[2:4]; b2 = bg_hex[4:6]
            override_tags += f"\\3c&H{b2}{g2}{r2}&\\4c&H{b2}{g2}{r2}&\\3a&H{alpha_hex}&\\4a&H{alpha_hex}&"

        # ── Precise vertical positioning via y_position_percent ──────────
        pos_tag = ""
        if c_y_pct is not None:
            cx = video_width // 2
            cy = int(video_height * int(c_y_pct) / 100)
            pos_tag = r"{\an5\pos(" + f"{cx},{cy}" + r")}"
            style_name = "Center"   # use centered alignment for \pos override

        # ── Emphasis: bold-highlight listed words ─────────────────────────
        if emphasis_words:
            for word in emphasis_words:
                # Case-insensitive replacement with bold tag wrapping
                import re as _re
                text = _re.sub(
                    r"(?i)(\b" + _re.escape(word) + r"\b)",
                    r"{\\b1}\1{\\b0}",
                    text,
                )

        # ── Animation tags ────────────────────────────────────────────────
        anim_tag = ""
        if animation == "pop":
            anim_tag = (
                r"{\fscx120\fscy120"
                + f"\\t(0,{anim_dur},\\fscx100\\fscy100)"
                + r"}"
            )
        elif animation == "fade":
            anim_tag = r"{\fad(" + f"{anim_dur},{anim_dur}" + r")}"
        elif animation == "slide_up":
            # Move from 100 px below final Y to final Y over anim_dur ms
            cx = video_width // 2
            if c_y_pct is not None:
                y_final = int(video_height * int(c_y_pct) / 100)
            elif position in ("lower_third", "bottom"):
                y_final = video_height - margin_v_edge
            elif position in ("upper_third", "top"):
                y_final = margin_v_edge
            else:
                y_final = video_height // 2
            y_start = y_final + 100
            anim_tag = r"{\move(" + f"{cx},{y_start},{cx},{y_final},0,{anim_dur}" + r")}"
        elif animation == "typewriter":
            anim_tag = r"{\fad(0,0)\alpha&HFF\t(0," + str(anim_dur) + r",\alpha&H00)}"

        # ── Assemble final text ───────────────────────────────────────────
        if override_tags:
            prefix = "{" + override_tags + "}"
        else:
            prefix = ""
        full_text = pos_tag + anim_tag + prefix + text

        events.append(f"Dialogue: 0,{start},{end},{style_name},,0,0,0,,{full_text}")

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
#  TRANSITIONS
# ═══════════════════════════════════════════════════════════════════════════

# transition name → (xfade_preset, duration_seconds)
# "__flash__" = pre-insert white frame sentinel (hard cut with visual pop)
_TRANSITION_CFG: Dict[str, Tuple[Optional[str], float]] = {
    "cut":              (None,           0.0),
    "hard_cut":         (None,           0.0),
    "hard cut":         (None,           0.0),
    "flash_cut":        ("__flash__",    0.0),
    "flash cut":        ("__flash__",    0.0),
    "flash":            ("__flash__",    0.0),
    "fade":             ("fade",         0.45),
    "dissolve":         ("dissolve",     0.45),
    "whip_pan_left":    ("wipeleft",     0.12),
    "whip pan left":    ("wipeleft",     0.12),
    "whip_pan_right":   ("wiperight",    0.12),
    "whip pan right":   ("wiperight",    0.12),
    "whip_pan":         ("wipeleft",     0.12),
    "whip pan":         ("wipeleft",     0.12),
    "swipe_left":       ("slideleft",    0.22),
    "swipe left":       ("slideleft",    0.22),
    "swipe_right":      ("slideright",   0.22),
    "swipe right":      ("slideright",   0.22),
    "swipe_up":         ("slideup",      0.22),
    "swipe up":         ("slideup",      0.22),
    "swipe_down":       ("slidedown",    0.22),
    "swipe down":       ("slidedown",    0.22),
    "wipe_left":        ("wipeleft",     0.15),
    "wipe left":        ("wipeleft",     0.15),
    "wipe_right":       ("wiperight",    0.15),
    "wipe right":       ("wiperight",    0.15),
    "zoom_transition":  ("zoomin",       0.25),
    "zoom transition":  ("zoomin",       0.25),
    "circle":           ("circleopen",   0.30),
    "iris":             ("circleopen",   0.30),
}


def _make_flash_clip(work_dir: Path, idx: int, width: int = 1080, height: int = 1920, fps: int = 30) -> str:
    """Create a 2-frame white clip (≈0.067s) for flash cut transitions."""
    out = str(work_dir / f"flash_{idx:03d}.mp4")
    dur = round(2 / max(fps, 1), 4)
    cmd = [
        settings.ffmpeg_binary, "-y",
        "-f", "lavfi", "-i", f"color=white:size={width}x{height}:rate={fps}:duration={dur}",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-t", str(dur),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
        "-c:a", "aac", "-b:a", "64k",
        "-shortest", out,
    ]
    _run(cmd)
    return out


def _probe_clip_duration(path: str) -> float:
    """Return VIDEO stream duration in seconds via ffprobe.

    Uses the video stream duration specifically (not the container/format
    duration) because the audio stream in iPhone footage often runs slightly
    longer due to PTS offset accumulation, which would cause xfade offsets to
    exceed the actual video length and silently discard clips.
    """
    try:
        r = subprocess.run(
            [settings.ffprobe_binary, "-v", "error",
             "-select_streams", "v:0",
             "-show_entries", "stream=duration",
             "-of", "json", path],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(r.stdout)
        streams = data.get("streams", [])
        if streams:
            dur = streams[0].get("duration")
            if dur:
                return float(dur)
        # Fallback: container duration
        r2 = subprocess.run(
            [settings.ffprobe_binary, "-v", "error", "-show_entries",
             "format=duration", "-of", "json", path],
            capture_output=True, text=True, timeout=30,
        )
        return float(json.loads(r2.stdout)["format"]["duration"])
    except Exception:
        return 1.0


def _apply_2clip_xfade(
    a: str,
    b: str,
    preset: str,
    dur: float,
    fps: int,
    output: str,
) -> str:
    """Apply a 2-clip xfade (video+audio) and return output path.

    This is the safe, low-overhead alternative to building a 60-input filter
    graph.  Each xfade is handled independently so FFmpeg only needs two open
    streams at a time.
    """
    _ensure_dir(output)
    da = _probe_clip_duration(a)
    db = _probe_clip_duration(b)
    max_safe = min(da, db) * 0.45
    safe_dur = min(dur, max_safe) if dur > 0 else 0.001
    offset = max(0.0, da - safe_dur)

    fc = (
        # Normalize both inputs to the same fps before xfade —
        # iPhone footage uses fractional timebases (29.97, 240tbr, etc.)
        # which make xfade refuse with "frame rate mismatch".
        f"[0:v]fps={fps}[v0];[1:v]fps={fps}[v1];"
        f"[v0][v1]xfade=transition={preset}:"
        f"duration={safe_dur:.3f}:offset={offset:.3f}[vout];"
        f"[0:a][1:a]acrossfade=d={safe_dur:.3f}[aout]"
    )
    _run([
        settings.ffmpeg_binary, "-y",
        "-i", a, "-i", b,
        "-filter_complex", fc,
        "-map", "[vout]", "-map", "[aout]",
        "-r", str(fps),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output,
    ], timeout=3600)
    return output


def concat_with_transitions(
    clip_paths: List[str],
    video_clips_spec: List[Dict[str, Any]],
    output_path: str,
    work_dir: Path,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
) -> str:
    """Concatenate clips with per-gap xfade transitions.

    Each gap reads its transition type from video_clips_spec[i].transition_out.
    Flash cuts are handled by pre-inserting a white frame before the hard cut.
    Falls back to plain concat if xfade command fails.
    """
    _ensure_dir(output_path)

    if not clip_paths:
        raise ValueError("No clips to concatenate")
    if len(clip_paths) == 1:
        import shutil as _shutil
        _shutil.copy2(clip_paths[0], output_path)
        return output_path

    # ── Extract transition type per gap ────────────────────────────────────
    raw_transitions: List[str] = []
    for i in range(len(clip_paths) - 1):
        spec_entry = video_clips_spec[i] if i < len(video_clips_spec) else {}
        trans = spec_entry.get("transition_out", {})
        if isinstance(trans, dict):
            t_type = trans.get("type", "cut")
        elif isinstance(trans, str):
            t_type = trans
        else:
            t_type = "cut"
        raw_transitions.append((t_type or "cut").lower().strip())

    # ── Pre-insert white flash frames where needed ─────────────────────────
    enriched: List[str] = [clip_paths[0]]
    enriched_trans: List[str] = []
    flash_count = 0
    for i, t_type in enumerate(raw_transitions):
        if t_type in ("flash_cut", "flash cut", "flash"):
            flash = _make_flash_clip(work_dir, flash_count, width, height, fps)
            flash_count += 1
            enriched.append(flash)
            enriched_trans.append("cut")  # gap before flash frame
            enriched.append(clip_paths[i + 1])
            enriched_trans.append("cut")  # gap after flash frame
        else:
            enriched.append(clip_paths[i + 1])
            enriched_trans.append(t_type)

    n = len(enriched)

    # ── Build per-gap (preset, duration) ──────────────────────────────────
    gap_presets: List[Optional[str]] = []
    gap_durs: List[float] = []
    clip_durations = [_probe_clip_duration(p) for p in enriched]
    for i, t_type in enumerate(enriched_trans):
        preset, dur = _TRANSITION_CFG.get(t_type, (None, 0.0))
        if preset == "__flash__":
            preset, dur = None, 0.0
        max_safe = min(clip_durations[i], clip_durations[i + 1]) * 0.45
        dur = min(dur, max_safe)
        gap_presets.append(preset)
        gap_durs.append(dur)

    # ── If no xfade needed, use plain concat (faster) ─────────────────────
    if not any(p for p in gap_presets):
        return concat_clips(enriched, output_path)

    # ── Chunked xfade: group clips at hard-cut boundaries ─────────────────
    # Each run of consecutive hard-cut clips is joined with the concat
    # demuxer.  Real xfade transitions are applied pairwise (2 clips at a
    # time) so FFmpeg never needs more than 2 open streams per xfade.
    groups: List[Tuple[List[str], Optional[str], float]] = []
    current_group: List[str] = [enriched[0]]
    for idx, clip in enumerate(enriched[1:]):          # idx = gap index
        preset_at_gap = gap_presets[idx]
        if preset_at_gap is not None:
            # End the current group here; record the xfade transition
            groups.append((current_group, preset_at_gap, gap_durs[idx]))
            current_group = [clip]
        else:
            current_group.append(clip)
    groups.append((current_group, None, 0.0))   # last group, no trailing xfade

    # Concat each intra-group run with hard cuts
    group_paths: List[str] = []
    for gi, (grp, _, _) in enumerate(groups):
        gpath = str(work_dir / f"group_{gi:03d}.mp4")
        if len(grp) == 1:
            import shutil as _sh
            _sh.copy2(grp[0], gpath)
        else:
            concat_clips(grp, gpath)
        group_paths.append(gpath)

    if len(group_paths) == 1:
        import shutil as _sh
        _sh.copy2(group_paths[0], output_path)
        return output_path

    # Pairwise-join groups with their trailing xfade (or hard cut on failure)
    current = group_paths[0]
    for i, next_gpath in enumerate(group_paths[1:]):
        _, trans_preset, trans_dur = groups[i]   # transition after groups[i]
        joined = str(work_dir / f"joined_{i:03d}.mp4")
        if trans_preset:
            try:
                current = _apply_2clip_xfade(
                    current, next_gpath, trans_preset, trans_dur, fps, joined
                )
            except RuntimeError as exc:
                logger.warning("2-clip xfade failed (%s → hard cut): %s", trans_preset, exc)
                concat_clips([current, next_gpath], joined)
                current = joined
        else:
            concat_clips([current, next_gpath], joined)
            current = joined

    import shutil as _sh
    _sh.copy2(current, output_path)
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
    original_audio_volume: float = 1.0,
) -> str:
    """Mix background music under the video's speech audio."""
    _ensure_dir(output_path)

    vol_filter = f",volume={original_audio_volume:.3f}" if original_audio_volume != 1.0 else ""

    if duck_under_speech:
        # Use sidechaincompress: duck music when speech is present
        af = (
            f"[1:a]volume={music_gain_db}dB[music];"
            f"[music][0:a]sidechaincompress=threshold=0.02:ratio=6:attack=10:release=300[ducked];"
            f"[0:a]{vol_filter.lstrip(',') + ',' if vol_filter else ''}[speech];"
            f"[speech][ducked]amix=inputs=2:duration=first:dropout_transition=2"
        ) if vol_filter else (
            f"[1:a]volume={music_gain_db}dB[music];"
            f"[music][0:a]sidechaincompress=threshold=0.02:ratio=6:attack=10:release=300[ducked];"
            f"[0:a][ducked]amix=inputs=2:duration=first:dropout_transition=2"
        )
    else:
        af = (
            f"[0:a]volume=1.0{vol_filter}[speech];[1:a]volume={music_gain_db}dB[music];"
            f"[speech][music]amix=inputs=2:duration=first:dropout_transition=2"
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

def _ass_ts_to_secs(ts: str) -> float:
    """Convert ASS timestamp H:MM:SS.cs to float seconds."""
    h, m, rest = ts.split(":", 2)
    s, cs = rest.split(".")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100.0


# Font name → macOS file paths (used by _ass_to_drawtext_filter)
_FONT_FILES: Dict[str, str] = {
    "Impact":         "/System/Library/Fonts/Supplemental/Impact.ttf",
    "Arial Black":    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "Arial":          "/System/Library/Fonts/Supplemental/Arial.ttf",
    "Helvetica":      "/System/Library/Fonts/Helvetica.ttc",
    "Georgia":        "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "Futura":         "/System/Library/Fonts/Supplemental/Futura.ttc",
    "Marker Felt":    "/System/Library/Fonts/Supplemental/Marker Felt.ttc",
    "Verdana":        "/System/Library/Fonts/Supplemental/Verdana.ttf",
    "Trebuchet MS":   "/System/Library/Fonts/Supplemental/Trebuchet MS.ttf",
    "Copperplate":    "/System/Library/Fonts/Supplemental/Copperplate.ttc",
}
_FALLBACK_FONT_FILE = "/System/Library/Fonts/Supplemental/Impact.ttf"


def _resolve_font_file(name: str) -> str:
    """Return fontfile='...' prefix for drawtext, or '' if not found."""
    path = _FONT_FILES.get(name)
    if not path:
        # case-insensitive fallback
        lower = name.lower()
        for k, v in _FONT_FILES.items():
            if k.lower() == lower:
                path = v
                break
    if path and os.path.exists(path):
        return f"fontfile='{path}':"
    if os.path.exists(_FALLBACK_FONT_FILE):
        return f"fontfile='{_FALLBACK_FONT_FILE}':"
    return ""


def _ass_to_drawtext_filter(ass_path: str) -> str:
    """Parse ASS subtitles → FFmpeg drawtext filter chain.

    Uses drawtext instead of ass= because libass rasterizes per-frame on
    macOS (~0.2 fps), while drawtext runs at ~30-100 fps.

    Reads per-style font/size/alignment from [V4+ Styles] and honours
    per-event inline overrides: \\fn (font), \\fs (size), \\pos (position),
    \\an (alignment), \\1c (color).
    """
    import re as _re

    with open(ass_path, encoding="utf-8") as f:
        content = f.read()

    # ── Parse Style definitions ───────────────────────────────────────────
    # Format: Style: Name,Fontname,Fontsize,...,Alignment,MarginL,MarginR,MarginV,Encoding
    styles: Dict[str, Dict[str, Any]] = {}
    for line in content.splitlines():
        if not line.startswith("Style:"):
            continue
        parts = line[len("Style:"):].strip().split(",")
        if len(parts) < 22:
            continue
        try:
            styles[parts[0].strip()] = {
                "fontname":  parts[1].strip(),
                "fontsize":  int(float(parts[2].strip())),
                "alignment": int(parts[18].strip()),
                "margin_v":  int(parts[21].strip()),
            }
        except (ValueError, IndexError):
            pass

    _DEFAULT_STYLE: Dict[str, Any] = {
        "fontname": "Impact", "fontsize": 88, "alignment": 2, "margin_v": 180,
    }
    default_style = styles.get("Default") or (
        next(iter(styles.values())) if styles else _DEFAULT_STYLE
    )

    # ── Parse Dialogue events ─────────────────────────────────────────────
    dialogue_re = _re.compile(
        r"^Dialogue:\s*\d+,"
        r"(\d+:\d+:\d+\.\d+),"   # start
        r"(\d+:\d+:\d+\.\d+),"   # end
        r"([^,]*),[^,]*,\d+,\d+,\d+,[^,]*,"  # style name
        r"(.+)$",
        _re.MULTILINE,
    )
    filters: List[str] = []
    for m in dialogue_re.finditer(content):
        start_ts, end_ts, style_name, raw = m.groups()
        start = _ass_ts_to_secs(start_ts)
        end   = _ass_ts_to_secs(end_ts)

        style = styles.get(style_name.strip(), default_style)
        s_fontname  = style["fontname"]
        s_fontsize  = style["fontsize"]
        s_alignment = style["alignment"]
        s_margin_v  = style["margin_v"]

        # ── Extract inline tag overrides ──────────────────────────────────
        # \fn<name>  — font name (ends at next \ or })
        fn_m = _re.search(r"\\fn([^\\}]+?)(?=[\\}])", raw)
        ev_fontname = fn_m.group(1).strip() if fn_m else s_fontname

        # \fs<digits>  — font size
        fs_m = _re.search(r"\\fs(\d+)", raw)
        ev_fontsize = int(fs_m.group(1)) if fs_m else s_fontsize

        # \pos(x,y)  — explicit screen position
        pos_m = _re.search(r"\\pos\((\d+(?:\.\d+)?),(\d+(?:\.\d+)?)\)", raw)

        # \an<n>  — alignment override
        an_m = _re.search(r"\\an(\d)", raw)
        ev_alignment = int(an_m.group(1)) if an_m else s_alignment

        # \1c&H<BBGGRR>&  — primary color (ASS stores BGR)
        color_m = _re.search(r"\\1c&H([0-9A-Fa-f]{6})&", raw)

        # ── Strip all inline tags to get plain text ───────────────────────
        text = _re.sub(r"\{[^}]*\}", "", raw).strip()
        if not text:
            continue

        # Escape characters special to drawtext
        esc = (text
               .replace("\\", "\\\\")
               .replace(":", r"\:")
               .replace("%", r"\%")
               .replace("'", r"\'"))

        # ── Font file ─────────────────────────────────────────────────────
        font_part = _resolve_font_file(ev_fontname)

        # ── Text color ────────────────────────────────────────────────────
        if color_m:
            # ASS BBGGRR → RGB for drawtext 0xRRGGBB
            hex6 = color_m.group(1)
            b, g, r = hex6[0:2], hex6[2:4], hex6[4:6]
            fontcolor = f"0x{r}{g}{b}@1.0"
        else:
            fontcolor = "white@1.0"

        # ── Position ──────────────────────────────────────────────────────
        if pos_m:
            # \pos centers on that pixel when \an5; adjust so text center = pos
            px = int(float(pos_m.group(1)))
            py = int(float(pos_m.group(2)))
            x_expr = f"{px}-text_w/2"
            y_expr = f"{py}-text_h/2"
        else:
            x_expr = "(w-text_w)/2"
            align = ev_alignment
            if align in (1, 2, 3):       # bottom row
                y_expr = f"h-text_h-{s_margin_v}"
            elif align in (7, 8, 9):     # top row
                y_expr = str(s_margin_v)
            else:                        # middle row (4, 5, 6)
                y_expr = "(h-text_h)/2"

        filters.append(
            f"drawtext={font_part}"
            f"text='{esc}':"
            f"fontsize={ev_fontsize}:"
            f"fontcolor={fontcolor}:"
            f"x={x_expr}:"
            f"y={y_expr}:"
            f"shadowcolor=black@0.6:"
            f"shadowx=3:shadowy=3:"
            f"enable='between(t,{start:.3f},{end:.3f})'"
        )
    return ",".join(filters)


def burn_subtitles(
    video_path: str,
    ass_path: str,
    output_path: str,
) -> str:
    """Burn ASS subtitles into the video."""
    _ensure_dir(output_path)

    # Build drawtext filter chain (fast; avoids libass ~0.2 fps on macOS)
    vf = ""
    if ass_path and os.path.exists(ass_path):
        try:
            vf = _ass_to_drawtext_filter(ass_path)
        except Exception as exc:
            logger.warning("drawtext build failed (%s); falling back to ass= filter", exc)

    if not vf:
        # Fallback: ass= filter (slow but correct)
        safe_ass = ass_path.replace("\\", "/").replace(":", r"\:")
        vf = f"ass='{safe_ass}'"

    cmd = [
        settings.ffmpeg_binary, "-y",
        "-i", video_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        # apad fills any tiny audio shortage from iPhone PTS drift;
        # -shortest caps output at the shorter (video) stream so apad
        # never generates infinite silence → prevents OOM.
        "-af", "apad",
        "-shortest",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    _run(cmd, timeout=3600)
    return output_path


# ═══════════════════════════════════════════════════════════════════════════
#  COLOR GRADING
# ═══════════════════════════════════════════════════════════════════════════

def apply_color_grade(
    input_path: str,
    output_path: str,
    grade: Dict[str, Any],
) -> str:
    """Apply a color grade profile extracted from the reference video.

    grade dict keys (all optional, sensible defaults assumed):
        brightness  float  -1.0 to 1.0  (default 0.0)
        contrast    float   0.0 to 2.0  (default 1.0)
        saturation  float   0.0 to 2.0  (default 1.0)
        gamma       float   0.1 to 10.0 (default 1.0)
    """
    _ensure_dir(output_path)

    brightness = float(grade.get("brightness", 0.0))
    contrast = float(grade.get("contrast", 1.0))
    saturation = float(grade.get("saturation", 1.0))
    gamma = float(grade.get("gamma", 1.0))

    # Clamp to safe FFmpeg eq ranges
    brightness = max(-1.0, min(1.0, brightness))
    contrast = max(0.0, min(2.0, contrast))
    saturation = max(0.0, min(2.0, saturation))
    gamma = max(0.1, min(10.0, gamma))

    vf = (
        f"eq=brightness={brightness:.3f}"
        f":contrast={contrast:.3f}"
        f":saturation={saturation:.3f}"
        f":gamma={gamma:.3f}"
    )

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
    """Consumes a validated EditTimeline or a RenderContract dict and produces a finished MP4.

    Preferred entry point: ``render_timeline(timeline)`` — type-safe, validates the
    timeline first and derives the render spec from ``EditTimeline.to_render_spec()``.
    The low-level ``render(spec)`` is kept for backward compatibility.
    """

    def __init__(self, asset_resolver=None, work_dir: Optional[Path] = None):
        """
        Args:
            asset_resolver: callable(asset_id) -> local file path
            work_dir: Optional pre-created working directory.  If None, a fresh
                      temp directory is created automatically and deleted after render.
        """
        self.asset_resolver = asset_resolver or (lambda x: x)
        self._work_dir_owned = work_dir is None
        self.work_dir = work_dir if work_dir is not None else Path(tempfile.mkdtemp(prefix="tiktok_render_"))
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir = Path(settings.render_output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def cleanup(self):
        """Remove temporary work directory (only if it was auto-created)."""
        import shutil
        if self._work_dir_owned and self.work_dir.exists():
            shutil.rmtree(self.work_dir, ignore_errors=True)

    def render_timeline(
        self,
        timeline: Any,
        preview: bool = False,
    ) -> Dict[str, str]:
        """Type-safe render from a validated EditTimeline object.

        This is the canonical entry point for the timeline-driven pipeline.
        It validates the timeline, converts it to a render spec, and delegates
        to ``_render_impl``.  Raw dicts or unvalidated Claude output are rejected.

        Args:
            timeline: An ``EditTimeline`` Pydantic object.
            preview:  If True, down-scales output to 480×854 for fast preview.

        Returns:
            Same dict as ``render()``: ``{output_path, thumbnail_path, ...}``

        Raises:
            TypeError:  if ``timeline`` is not an ``EditTimeline`` instance.
            ValueError: if ``timeline.validate_timeline()`` returns errors.
        """
        from app.services.timeline_schema import EditTimeline
        if not isinstance(timeline, EditTimeline):
            raise TypeError(
                f"render_timeline() requires an EditTimeline object, got {type(timeline).__name__}. "
                "Use EditTimeline.model_validate(...) to parse a dict first."
            )

        errors = timeline.validate_timeline()
        if errors:
            raise ValueError(
                f"Timeline validation failed ({len(errors)} error(s)):\n" +
                "\n".join(f"  • {e}" for e in errors)
            )

        spec = timeline.to_render_spec(asset_resolver=self.asset_resolver)

        if preview:
            spec["output"]["width"] = 480
            spec["output"]["height"] = 854

        color_grade = {
            "brightness": timeline.color_grade.brightness,
            "contrast": timeline.color_grade.contrast,
            "saturation": timeline.color_grade.saturation,
            "gamma": timeline.color_grade.gamma,
            "luma_avg": timeline.color_grade.luma_avg,
        }

        render_style = timeline.render_style or None

        try:
            return self._render_impl(spec, color_grade=color_grade, render_style=render_style)
        finally:
            self.cleanup()

    def render(self, spec: Dict[str, Any], color_grade: Optional[Dict[str, Any]] = None, render_style: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        """Execute the full render pipeline from an edit spec.

        Args:
            spec: RenderContract-style edit specification.
            color_grade: Optional color grade profile extracted from the reference
                video. Applied to every processed clip before concatenation.
            render_style: Optional RenderStyle dict produced by StyleRenderer.
                When provided, grain, vignette, and zoom presets are applied in
                addition to the basic color grade.

        Returns:
            {
                "output_path": "/path/to/final.mp4",
                "thumbnail_path": "/path/to/thumb.jpg",
                "subtitle_path": "/path/to/captions.ass",
            }
        """
        try:
            return self._render_impl(spec, color_grade=color_grade, render_style=render_style)
        finally:
            self.cleanup()

    def _render_impl(self, spec: Dict[str, Any], color_grade: Optional[Dict[str, Any]] = None, render_style: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
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

            # Trim — apply speed_ramp if provided (averaged across ramp points)
            clip_speed = vc.get("speed", 1.0)
            speed_ramp_pts = vc.get("speed_ramp") or []
            if speed_ramp_pts and isinstance(speed_ramp_pts, list):
                speeds = [float(pt.get("speed", 1.0)) for pt in speed_ramp_pts if "speed" in pt]
                if speeds:
                    clip_speed = sum(speeds) / len(speeds)
                    logger.debug("speed_ramp applied (avg=%.2f) for clip %s",
                                 clip_speed, vc.get("asset_id"))

            trimmed = str(self.work_dir / f"trim_{i:03d}.mp4")
            _src_in  = max(0.0, float(vc.get("source_in", 0.0)))
            _src_out = float(vc.get("source_out",
                vc.get("source_in", 0) + (vc["end"] - vc["start"])))
            if _src_out - _src_in < (1.0 / 30):
                logger.warning(
                    "Skipping clip %d (%s): degenerate source range [%.4f–%.4f]",
                    i, vc.get("asset_id"), _src_in, _src_out,
                )
                continue
            trim_clip(
                src, trimmed,
                start=_src_in,
                end=_src_out,
                speed=clip_speed,
            )

            # Normalize to target resolution
            normalised = str(self.work_dir / f"norm_{i:03d}.mp4")
            normalize_clip(trimmed, normalised, width, height, fps)

            # Apply motion — motion_preset (from render_style) drives animation.
            # Per-clip zoom_keyframes / crop_anchor / motion_easing override the profile default.
            motion_preset: dict = dict((render_style or {}).get("motion_preset") or {})
            if vc.get("zoom_keyframes"):
                motion_preset["zoom_keyframes"] = vc["zoom_keyframes"]
            if vc.get("crop_anchor") and vc["crop_anchor"] != "center":
                motion_preset["crop_anchor"] = vc["crop_anchor"]
            if vc.get("motion_easing") and vc["motion_easing"] != "linear":
                motion_preset["easing"] = vc["motion_easing"]

            motion = vc.get("motion", {})
            motion_type     = motion.get("type", "static") if isinstance(motion, dict) else "static"
            motion_strength = motion.get("strength", 0.05) if isinstance(motion, dict) else 0.05
            crop_anchor     = motion_preset.get("crop_anchor", "center")
            easing          = motion_preset.get("easing", "linear")
            clip_dur        = vc.get("end", 0.0) - vc.get("start", 0.0)

            # zoom_keyframes take priority; fall back to zoom_style for legacy specs
            kfs = motion_preset.get("zoom_keyframes", [])
            if kfs and len(kfs) >= 2:
                start_s = float(kfs[0].get("scale", 1.0))
                end_s   = float(kfs[-1].get("scale", 1.0))
                if abs(end_s - start_s) >= 0.005:
                    motion_type     = "zoom_in" if end_s > start_s else "zoom_out"
                    motion_strength = abs(end_s - start_s)
                else:
                    motion_type = "static"
            elif render_style:
                zs      = render_style.get("zoom_style", {})
                rs_type = zs.get("type", "")
                if rs_type and rs_type != "static":
                    # Map punch_zoom → zoom_in for apply_motion
                    motion_type     = "zoom_in" if rs_type == "punch_zoom" else rs_type
                    motion_strength = float(zs.get("strength", motion_strength))

            # Apply color grade from reference if provided
            graded = normalised
            if render_style:
                # Use StyleRenderer for the full vf chain (grade + grain + vignette)
                from app.services.style_renderer import StyleRenderer
                vf = StyleRenderer.build_full_clip_vf(render_style)
                if vf:
                    graded = str(self.work_dir / f"grade_{i:03d}.mp4")
                    cmd = [
                        settings.ffmpeg_binary, "-y",
                        "-i", normalised,
                        "-vf", vf,
                        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                        "-c:a", "copy",
                        "-movflags", "+faststart",
                        graded,
                    ]
                    _run(cmd)
            elif color_grade and not (
                color_grade.get("brightness", 0.0) == 0.0
                and color_grade.get("contrast", 1.0) == 1.0
                and color_grade.get("saturation", 1.0) == 1.0
                and color_grade.get("gamma", 1.0) == 1.0
            ):
                graded = str(self.work_dir / f"grade_{i:03d}.mp4")
                apply_color_grade(normalised, graded, color_grade)

            if motion_type != "static":
                motion_out = str(self.work_dir / f"motion_{i:03d}.mp4")
                apply_motion(
                    graded, motion_out, motion_type, motion_strength, width, height,
                    crop_anchor=crop_anchor,
                    easing=easing,
                    clip_duration=clip_dur,
                )
                processed_clips.append(motion_out)
            else:
                processed_clips.append(graded)

        # ── Step 2: Concatenate video clips (with transitions) ───────────
        if len(processed_clips) == 0:
            raise ValueError("No video clips in edit spec")
        elif len(processed_clips) == 1:
            concat_path = processed_clips[0]
        else:
            concat_path = str(self.work_dir / "concat.mp4")
            concat_with_transitions(
                processed_clips,
                video_clips,
                concat_path,
                self.work_dir,
                width=width,
                height=height,
                fps=fps,
            )

        # ── Step 3: Generate & burn captions ─────────────────────────────
        if text_clips:
            ass_path = str(self.work_dir / "captions.ass")
            generate_ass_subtitles(text_clips, ass_path, width, height, render_style=render_style)

            captioned_path = str(self.work_dir / "captioned.mp4")
            burn_subtitles(concat_path, ass_path, captioned_path)
            current = captioned_path
        else:
            ass_path = None
            current = concat_path

        # ── Step 4: Mix audio ────────────────────────────────────────────
        audio_mode = spec.get("audio_mode", "reference_audio")
        music_path = spec.get("music_path")
        audio_mix  = spec.get("audio_mix_settings", {})
        music_gain_db        = float(audio_mix.get("music_volume", -18.0))
        original_audio_vol   = float(audio_mix.get("original_audio_volume", 0.0))
        duck_under_speech    = bool(audio_mix.get("duck_under_speech", True))

        if audio_mode == "silent":
            # Strip all audio from the output
            stripped_path = str(self.work_dir / "stripped.mp4")
            _run([
                settings.ffmpeg_binary, "-y",
                "-i", current,
                "-an",
                "-c:v", "copy",
                stripped_path,
            ])
            current = stripped_path

        elif audio_mode == "original_audio":
            # Keep footage audio; optionally adjust its volume
            if original_audio_vol != 1.0:
                vol_path = str(self.work_dir / "vol_adjusted.mp4")
                _run([
                    settings.ffmpeg_binary, "-y",
                    "-i", current,
                    "-af", f"volume={original_audio_vol:.3f}",
                    "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k",
                    vol_path,
                ])
                current = vol_path

        elif audio_mode in ("reference_audio", "uploaded_audio"):
            # Prefer explicit music_path; fall back to audio_clips
            if music_path and os.path.exists(music_path):
                mixed_path = str(self.work_dir / f"mixed_{uuid.uuid4().hex[:6]}.mp4")
                mix_audio(
                    current, music_path, mixed_path,
                    music_gain_db=music_gain_db,
                    duck_under_speech=duck_under_speech,
                    original_audio_volume=original_audio_vol or 1.0,
                )
                current = mixed_path
            else:
                # Legacy: iterate audio_clips track
                for ac in audio_clips:
                    audio_src = self.asset_resolver(ac["asset_id"])
                    if audio_src and os.path.exists(audio_src):
                        mixed_path = str(self.work_dir / f"mixed_{uuid.uuid4().hex[:6]}.mp4")
                        mix_audio(
                            current, audio_src, mixed_path,
                            music_gain_db=ac.get("gain_db", -18),
                            duck_under_speech=ac.get("duck_under_speech", True),
                            original_audio_volume=original_audio_vol or 1.0,
                        )
                        current = mixed_path

        else:
            # Unknown mode — treat like reference_audio for safety
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
