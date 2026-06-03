"""StyleRenderer — converts a reference fingerprint into concrete render instructions.

Given the reference fingerprint produced by ReferenceAnalyzer (including the
inferred ``ranking_profile``), StyleRenderer builds a ``RenderStyle`` dict that
EditTimeline stores as ``render_style`` and RenderEngine consumes to apply
visual effects consistently across all clips.

Output schema (``RenderStyle``)
────────────────────────────────
{
    "color_grade": {
        "brightness":   float,   # eq filter param (-1..1)
        "contrast":     float,   # eq filter param (0..2)
        "saturation":   float,   # eq filter param (0..2)
        "gamma":        float,   # eq filter param (0.1..10)
        "extra_filters": str,    # additional FFmpeg vf chain fragment (may be "")
    },
    "grain": {
        "enabled":  bool,
        "strength": float,       # 0..50  — fed to geq/noise filter
    },
    "vignette": {
        "enabled": bool,
        "angle":   float,        # radians (PI/4 ≈ 0.785 is subtle; PI/3 ≈ 1.05 is strong)
    },
    "zoom_style": {
        "type":     str,         # slow_push | punch_zoom | static
        "strength": float,       # 0..0.15
    },
    "caption_style": {
        "font_size":    int,     # pixels
        "bold":         bool,
        "stroke_width": float,   # ASS Outline value
        "position":     str,     # top | center | bottom
        "animation":    str,     # pop | fade | slide_up | none
        "all_caps":     bool,
    },
    "transition_style": {
        "type":     str,         # hard_cut | flash_cut | dissolve | whip_pan_left
        "duration": float,       # seconds (0 = instantaneous cut)
    },
}

Public API
──────────
  StyleRenderer.from_fingerprint(fingerprint) → RenderStyle dict
  StyleRenderer.build_ffmpeg_color_filters(render_style) → str   (vf fragment)
  StyleRenderer.build_ffmpeg_grain_filter(render_style) → str    (vf fragment or "")
  StyleRenderer.build_ffmpeg_vignette_filter(render_style) → str (vf fragment or "")
"""
from __future__ import annotations

from typing import Any

# ── Per-profile render presets ─────────────────────────────────────────────

_PROFILE_PRESETS: dict[str, dict[str, Any]] = {
    "fashion_montage": {
        # Filmic: punchy contrast, warm saturation, subtle push-in
        "color_grade": {
            "brightness":    0.02,
            "contrast":      1.15,
            "saturation":    1.15,
            "gamma":         0.95,
            "extra_filters": "unsharp=5:5:0.6:5:5:0",   # gentle sharpening
        },
        "grain":      {"enabled": True,  "strength": 12.0},
        "vignette":   {"enabled": True,  "angle": 0.9},
        "zoom_style": {"type": "slow_push", "strength": 0.04},
        "caption_style": {
            "font_size":    72,
            "bold":         True,
            "stroke_width": 3.5,
            "position":     "center",
            "animation":    "pop",
            "all_caps":     True,
        },
        "transition_style": {"type": "hard_cut", "duration": 0.0},
        "motion_preset": {
            "zoom_keyframes": [{"t": 0.0, "scale": 1.0}, {"t": 1.0, "scale": 1.04}],
            "crop_anchor":   "center",
            "easing":        "ease_out",
            "speed_ramp":    None,
        },
        "caption_preset": {
            "safe_zone_frac":     0.08,
            # typography
            "font_family":        "Arial Black",
            "font_weight":        "black",
            "text_case":          "uppercase",
            "stroke_width":       0.0,    # editorial — clean, no stroke
            "shadow":             False,
            "tracking":           2.5,    # wide spaced, editorial feel
            "animation":          "fade",
            "animation_duration": 300,
        },
    },

    "music_video": {
        # High contrast, punchy colours, fast zoom punches, flash cuts
        "color_grade": {
            "brightness":    0.03,
            "contrast":      1.25,
            "saturation":    1.30,
            "gamma":         0.90,
            "extra_filters": "unsharp=3:3:1.0:3:3:0",
        },
        "grain":      {"enabled": False, "strength": 0.0},
        "vignette":   {"enabled": True,  "angle": 1.0},
        "zoom_style": {"type": "punch_zoom", "strength": 0.10},
        "caption_style": {
            "font_size":    80,
            "bold":         True,
            "stroke_width": 4.0,
            "position":     "center",
            "animation":    "pop",
            "all_caps":     True,
        },
        "transition_style": {"type": "flash_cut", "duration": 0.0},
        "motion_preset": {
            "zoom_keyframes": [{"t": 0.0, "scale": 1.0}, {"t": 1.0, "scale": 1.10}],
            "crop_anchor":   "center",
            "easing":        "ease_in",
            "speed_ramp":    None,
        },
        "caption_preset": {
            "safe_zone_frac":     0.12,
            # typography
            "font_family":        "Arial Black",
            "font_weight":        "black",
            "text_case":          "uppercase",
            "stroke_width":       4.0,    # heavy outline for energy
            "shadow":             False,
            "tracking":           0.0,    # tight, punchy
            "animation":          "pop",
            "animation_duration": 100,    # snappy
        },
    },

    "talking_head": {
        # Clean, minimal motion, legible captions, neutral grade
        "color_grade": {
            "brightness":    0.01,
            "contrast":      1.05,
            "saturation":    1.05,
            "gamma":         1.00,
            "extra_filters": "",
        },
        "grain":      {"enabled": False, "strength": 0.0},
        "vignette":   {"enabled": False, "angle": 0.0},
        "zoom_style": {"type": "static", "strength": 0.0},
        "caption_style": {
            "font_size":    68,
            "bold":         True,
            "stroke_width": 2.5,
            "position":     "bottom",
            "animation":    "fade",
            "all_caps":     False,
        },
        "transition_style": {"type": "hard_cut", "duration": 0.0},
        "motion_preset": {
            "zoom_keyframes": [{"t": 0.0, "scale": 1.0}, {"t": 1.0, "scale": 1.0}],
            "crop_anchor":   "center",
            "easing":        "linear",
            "speed_ramp":    None,
        },
        "caption_preset": {
            "safe_zone_frac":     0.06,
            # typography
            "font_family":        "Arial",
            "font_weight":        "bold",
            "text_case":          "titlecase",
            "stroke_width":       2.0,
            "shadow":             True,   # drop shadow aids legibility on busy bg
            "tracking":           0.5,
            "animation":          "fade",
            "animation_duration": 250,
        },
    },

    "vlog": {
        # Casual warm look, slight push, subtitles near bottom
        "color_grade": {
            "brightness":    0.03,
            "contrast":      1.08,
            "saturation":    1.10,
            "gamma":         0.98,
            "extra_filters": "",
        },
        "grain":      {"enabled": True,  "strength": 8.0},
        "vignette":   {"enabled": False, "angle": 0.0},
        "zoom_style": {"type": "slow_push", "strength": 0.03},
        "caption_style": {
            "font_size":    64,
            "bold":         True,
            "stroke_width": 3.0,
            "position":     "center",
            "animation":    "slide_up",
            "all_caps":     True,
        },
        "transition_style": {"type": "hard_cut", "duration": 0.0},
        "motion_preset": {
            "zoom_keyframes": [{"t": 0.0, "scale": 1.0}, {"t": 1.0, "scale": 1.02}],
            "crop_anchor":   "center",
            "easing":        "ease_in_out",
            "speed_ramp":    None,
        },
        "caption_preset": {
            "safe_zone_frac":     0.08,
            # typography
            "font_family":        "Arial",
            "font_weight":        "bold",
            "text_case":          "titlecase",
            "stroke_width":       2.5,
            "shadow":             False,
            "tracking":           0.5,
            "animation":          "slide_up",
            "animation_duration": 200,
        },
    },

    "product_showcase": {
        # Maximum sharpness, clean neutral grade, static or very slow push
        "color_grade": {
            "brightness":    0.00,
            "contrast":      1.10,
            "saturation":    1.05,
            "gamma":         1.00,
            "extra_filters": "unsharp=5:5:1.2:5:5:0",   # strong sharpening
        },
        "grain":      {"enabled": False, "strength": 0.0},
        "vignette":   {"enabled": False, "angle": 0.0},
        "zoom_style": {"type": "slow_push", "strength": 0.02},
        "caption_style": {
            "font_size":    64,
            "bold":         True,
            "stroke_width": 2.0,
            "position":     "bottom",
            "animation":    "fade",
            "all_caps":     False,
        },
        "transition_style": {"type": "dissolve", "duration": 0.3},
        "motion_preset": {
            "zoom_keyframes": [{"t": 0.0, "scale": 1.0}, {"t": 1.0, "scale": 1.02}],
            "crop_anchor":   "center",
            "easing":        "linear",
            "speed_ramp":    None,
        },
        "caption_preset": {
            "safe_zone_frac":     0.06,
            # typography
            "font_family":        "Arial",
            "font_weight":        "normal",
            "text_case":          "titlecase",
            "stroke_width":       0.0,    # clean, no stroke — product-label look
            "shadow":             True,
            "tracking":           1.5,
            "animation":          "fade",
            "animation_duration": 350,    # slow, clean
        },
    },

    "travel_reel": {
        # Vibrant colours, cinematic push-ins, dramatic captions
        "color_grade": {
            "brightness":    0.02,
            "contrast":      1.12,
            "saturation":    1.20,
            "gamma":         0.95,
            "extra_filters": "",
        },
        "grain":      {"enabled": True,  "strength": 6.0},
        "vignette":   {"enabled": True,  "angle": 0.85},
        "zoom_style": {"type": "slow_push", "strength": 0.05},
        "caption_style": {
            "font_size":    72,
            "bold":         True,
            "stroke_width": 3.5,
            "position":     "center",
            "animation":    "slide_up",
            "all_caps":     True,
        },
        "transition_style": {"type": "whip_pan_left", "duration": 0.12},
        "motion_preset": {
            "zoom_keyframes": [{"t": 0.0, "scale": 1.0}, {"t": 1.0, "scale": 1.05}],
            "crop_anchor":   "center",
            "easing":        "ease_in_out",
            "speed_ramp":    None,
        },
        "caption_preset": {
            "safe_zone_frac":     0.10,
            # typography
            "font_family":        "Arial Black",
            "font_weight":        "black",
            "text_case":          "uppercase",
            "stroke_width":       3.5,
            "shadow":             False,
            "tracking":           3.0,    # wide-spaced location-stamp look
            "animation":          "slide_up",
            "animation_duration": 200,
        },
    },
}

# ── Fallback when profile is unknown ──────────────────────────────────────

_DEFAULT_PRESET: dict[str, Any] = {
    "color_grade": {
        "brightness":    0.0,
        "contrast":      1.0,
        "saturation":    1.0,
        "gamma":         1.0,
        "extra_filters": "",
    },
    "grain":      {"enabled": False, "strength": 0.0},
    "vignette":   {"enabled": False, "angle": 0.0},
    "zoom_style": {"type": "slow_push", "strength": 0.04},
    "caption_style": {
        "font_size":    72,
        "bold":         True,
        "stroke_width": 3.0,
        "position":     "center",
        "animation":    "pop",
        "all_caps":     True,
    },
    "transition_style": {"type": "hard_cut", "duration": 0.0},
    "motion_preset": {
        "zoom_keyframes": [{"t": 0.0, "scale": 1.0}, {"t": 1.0, "scale": 1.04}],
        "crop_anchor":   "center",
        "easing":        "linear",
        "speed_ramp":    None,
    },
    "caption_preset": {
        "safe_zone_frac":     0.08,
        "font_family":        "Arial Black",
        "font_weight":        "bold",
        "text_case":          "uppercase",
        "stroke_width":       3.0,
        "shadow":             False,
        "tracking":           0.5,
        "animation":          "pop",
        "animation_duration": 200,
    },
}


# ── Merge helper ───────────────────────────────────────────────────────────

def _deep_merge(base: dict, override: dict) -> dict:
    """Return a new dict that is `base` with leaf values replaced by `override`."""
    result = {**base}
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


# ═══════════════════════════════════════════════════════════════════════════
#  StyleRenderer
# ═══════════════════════════════════════════════════════════════════════════

class StyleRenderer:
    """Converts a reference fingerprint into a concrete ``RenderStyle`` dict.

    Usage::

        render_style = StyleRenderer.from_fingerprint(fingerprint)
        # render_style is stored in EditTimeline.render_style
        # RenderEngine reads it during _render_impl

    The class is stateless — all methods are classmethods/staticmethods.
    """

    # ── Primary entry point ───────────────────────────────────────────────

    @classmethod
    def from_fingerprint(
        cls,
        fingerprint: dict[str, Any],
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a RenderStyle dict from a reference fingerprint.

        The profile preset is selected via ``fingerprint["ranking_profile"]``
        (set by ``infer_style_profile()`` in reference_analyzer.py).  Then the
        fingerprint's own measured values (color_grade, caption_style, dominant
        transition) are layered on top so the rendered output matches the
        reference as closely as possible.  Any caller-supplied ``overrides``
        are applied last.

        Args:
            fingerprint:  Reference fingerprint dict (output of ReferenceAnalyzer).
            overrides:    Optional nested dict of per-section overrides to apply
                          on top of the profile preset.

        Returns:
            RenderStyle dict (see module docstring for schema).
        """
        profile = fingerprint.get("ranking_profile")
        preset  = _deep_merge(_DEFAULT_PRESET, _PROFILE_PRESETS.get(profile, {}))

        # ── Layer 1: fingerprint color_grade overrides preset ─────────────
        fp_cg = fingerprint.get("color_grade") or fingerprint.get("color_profile") or {}
        measured_cg: dict[str, Any] = {}
        for key in ("brightness", "contrast", "saturation", "gamma"):
            if key in fp_cg:
                val = fp_cg[key]
                # For multiplicative params, 0.0 means the LLM returned no data.
                # Skip zero values so the profile preset defaults are preserved.
                if key != "brightness" and not val:
                    continue
                measured_cg[key] = val
        if measured_cg:
            preset = _deep_merge(preset, {"color_grade": measured_cg})

        # ── Layer 2: fingerprint caption_style overrides preset ───────────
        fp_cap = fingerprint.get("caption_style") or {}
        if fp_cap:
            cap_override: dict[str, Any] = {}
            if "position" in fp_cap:
                cap_override["position"] = fp_cap["position"]
            if "animation" in fp_cap:
                cap_override["animation"] = fp_cap["animation"]
            if fp_cap.get("font_size_class") == "small":
                cap_override["font_size"] = 52
            elif fp_cap.get("font_size_class") == "medium":
                cap_override["font_size"] = 64
            if "all_caps" in fp_cap:
                cap_override["all_caps"] = fp_cap["all_caps"]
            if "has_stroke" in fp_cap:
                cap_override["stroke_width"] = (
                    preset["caption_style"]["stroke_width"] if fp_cap["has_stroke"]
                    else 0.0
                )
            if cap_override:
                preset = _deep_merge(preset, {"caption_style": cap_override})

        # ── Layer 3: dominant_transition overrides preset ─────────────────
        dom_trans = fingerprint.get("dominant_transition")
        if dom_trans:
            preset = _deep_merge(preset, {
                "transition_style": {"type": dom_trans, "duration": 0.0},
            })

        # ── Layer 4: energy/pace fine-tuning ─────────────────────────────
        energy = fingerprint.get("energy_level", "medium")
        if energy == "high" and preset["zoom_style"]["type"] == "slow_push":
            preset = _deep_merge(preset, {"zoom_style": {"strength": 0.07}})
        elif energy == "low":
            preset = _deep_merge(preset, {
                "zoom_style": {"strength": max(0.0, preset["zoom_style"]["strength"] - 0.02)},
            })

        # ── Layer 5: motion_pattern=static disables zoom ─────────────────
        if fingerprint.get("motion_pattern", "") == "static":
            preset = _deep_merge(preset, {"zoom_style": {"type": "static", "strength": 0.0}})

        # ── Layer 6: caller overrides (highest priority) ──────────────────
        if overrides:
            preset = _deep_merge(preset, overrides)

        return preset

    # ── FFmpeg filter helpers ─────────────────────────────────────────────

    @staticmethod
    def build_ffmpeg_color_filters(render_style: dict[str, Any]) -> str:
        """Return the ``eq`` + any extra video filter fragment for a clip.

        Returns an empty string when no grade is needed (all at defaults).

        Example return value::

            "eq=brightness=0.02:contrast=1.15:saturation=1.15:gamma=0.95,unsharp=5:5:0.6:5:5:0"
        """
        cg = render_style.get("color_grade", {})
        brightness = float(cg.get("brightness", 0.0))
        contrast   = float(cg.get("contrast",   1.0))
        saturation = float(cg.get("saturation", 1.0))
        gamma      = float(cg.get("gamma",      1.0))
        extras     = str(cg.get("extra_filters", "")).strip()

        # Clamp to safe FFmpeg eq ranges — gamma=0 is invalid (min 0.1)
        brightness = max(-1.0, min(1.0, brightness))
        contrast   = max(0.0,  min(2.0, contrast))
        saturation = max(0.0,  min(2.0, saturation))
        gamma      = max(0.1,  min(10.0, gamma))

        # Skip eq if everything is at default
        needs_eq = (
            abs(brightness) > 1e-4
            or abs(contrast  - 1.0) > 1e-4
            or abs(saturation - 1.0) > 1e-4
            or abs(gamma     - 1.0) > 1e-4
        )

        parts: list[str] = []
        if needs_eq:
            parts.append(
                f"eq=brightness={brightness:.3f}"
                f":contrast={contrast:.3f}"
                f":saturation={saturation:.3f}"
                f":gamma={gamma:.3f}"
            )
        if extras:
            parts.append(extras)

        return ",".join(parts)

    @staticmethod
    def build_ffmpeg_grain_filter(render_style: dict[str, Any]) -> str:
        """Return the noise/grain vf fragment, or empty string if disabled."""
        grain = render_style.get("grain", {})
        if not grain.get("enabled", False):
            return ""
        strength = max(0.0, min(50.0, float(grain.get("strength", 10.0))))
        # geq-based luma noise that's consistent across frames
        s = round(strength, 1)
        return (
            f"noise=alls={s}:allf=t+u"   # temporal + uniform grain
        )

    @staticmethod
    def build_ffmpeg_vignette_filter(render_style: dict[str, Any]) -> str:
        """Return the vignette vf fragment, or empty string if disabled."""
        vig = render_style.get("vignette", {})
        if not vig.get("enabled", False):
            return ""
        angle = max(0.0, min(3.14, float(vig.get("angle", 0.9))))
        return f"vignette=angle={angle:.3f}"

    @staticmethod
    def build_full_clip_vf(render_style: dict[str, Any]) -> str:
        """Return the complete comma-joined vf filter string for a single clip.

        Combines color grade + grain + vignette.  Returns empty string when no
        filters are needed (avoids an unnecessary re-encode).
        """
        parts = [
            StyleRenderer.build_ffmpeg_color_filters(render_style),
            StyleRenderer.build_ffmpeg_grain_filter(render_style),
            StyleRenderer.build_ffmpeg_vignette_filter(render_style),
        ]
        return ",".join(p for p in parts if p)

    @staticmethod
    def build_ffmpeg_motion_vf(
        motion_preset: dict[str, Any],
        clip_duration: float,
        width: int,
        height: int,
        fps: int,
    ) -> str:
        """Return the zoompan filter string that enacts a motion_preset on one clip.

        Uses the first and last zoom_keyframe as start/end scale, then applies
        the easing curve and crop_anchor to the zoompan filter expression.
        Returns empty string when the zoom delta is negligible (< 0.005).

        Args:
            motion_preset:  A ``motion_preset`` dict (from ``RenderStyle``).
            clip_duration:  Clip duration in seconds (used for frame count).
            width, height:  Output frame dimensions.
            fps:            Output frame rate.
        """
        kfs = motion_preset.get("zoom_keyframes", [])
        if len(kfs) < 2:
            return ""

        start_scale = float(kfs[0].get("scale", 1.0))
        end_scale   = float(kfs[-1].get("scale", 1.0))
        strength    = abs(end_scale - start_scale)
        if strength < 0.005:
            return ""

        easing = motion_preset.get("easing", "linear")
        anchor = motion_preset.get("crop_anchor", "center")

        total_frames = max(1, round(clip_duration * fps)) if clip_duration > 0 else fps * 3
        denom = max(total_frames - 1, 1)   # on/denom → 0..1 over the clip

        # Slight upscale to give zoompan room
        pad = int(max(width, height) * max(start_scale, end_scale) * 0.06) + 2
        sw  = width  + pad * 2
        sh  = height + pad * 2

        # Easing expression using zoompan's 'on' variable (output frame number).
        # 'on' is natively supported; generic 'n'/'t' are not available in zoompan.
        if easing == "ease_out":
            e = f"(1-pow(1-on/{denom},2))"
        elif easing == "ease_in":
            e = f"pow(on/{denom},2)"
        elif easing == "ease_in_out":
            e = f"if(lt(on/{denom},0.5),2*pow(on/{denom},2),1-2*pow(1-on/{denom},2))"
        else:
            e = f"on/{denom}"

        if end_scale >= start_scale:
            z_expr = f"{start_scale:.4f}+{strength:.4f}*{e}"
        else:
            z_expr = f"{start_scale:.4f}-{strength:.4f}*{e}"

        # Crop anchor x/y (zoompan-native zoom-relative expressions)
        _ax = {"left": "0", "right": "iw-iw/zoom",
               "center": "(iw-iw/zoom)/2", "top": "(iw-iw/zoom)/2", "bottom": "(iw-iw/zoom)/2"}
        _ay = {"top": "0", "bottom": "ih-ih/zoom",
               "center": "(ih-ih/zoom)/2", "left": "(ih-ih/zoom)/2", "right": "(ih-ih/zoom)/2"}
        x_expr = _ax.get(anchor, "(iw-iw/zoom)/2")
        y_expr = _ay.get(anchor, "(ih-ih/zoom)/2")

        return (
            f"scale={sw}:{sh},"
            f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':"
            f"d=1:s={width}x{height}:fps={fps}"
        )

    @staticmethod
    def get_caption_safe_zone(
        render_style: dict[str, Any],
        frame_height: int = 1920,
    ) -> int:
        """Return caption safe-zone margin in pixels for edge-anchored captions.

        Reads ``render_style["caption_preset"]["safe_zone_frac"]``.
        Returns 0 when not set so callers can fall back to their own default.
        """
        frac = float(
            (render_style.get("caption_preset") or {}).get("safe_zone_frac", 0.0)
        )
        return int(frac * frame_height)
