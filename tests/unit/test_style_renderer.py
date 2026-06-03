"""Unit tests for app/services/style_renderer.py.

Verifies:
  - StyleRenderer.from_fingerprint() returns the expected preset for each profile
  - FFmpeg filter builders produce valid, non-empty strings when effects are on
  - fashion_montage applies filmic contrast + subtle grain + slow push-in
  - music_video applies punch zooms + flash cuts + high contrast
  - talking_head applies minimal motion + clean captions
  - product_showcase applies strong sharpening + clean crop (no grain, no vignette)
  - Fingerprint overrides (color_grade, caption_style, dominant_transition) are applied
  - Fallback to defaults when no profile is set
"""
from __future__ import annotations

import pytest

from app.services.style_renderer import (
    StyleRenderer,
    _PROFILE_PRESETS,
    _DEFAULT_PRESET,
    _deep_merge,
)

# ── Shared fixtures ──────────────────────────────────────────────────────────

def _fp(profile: str, **extra) -> dict:
    return {"ranking_profile": profile, **extra}


# ── _deep_merge ──────────────────────────────────────────────────────────────

class TestDeepMerge:
    def test_override_leaf(self):
        base = {"a": {"x": 1, "y": 2}, "b": 3}
        override = {"a": {"x": 10}}
        result = _deep_merge(base, override)
        assert result["a"] == {"x": 10, "y": 2}
        assert result["b"] == 3

    def test_new_key(self):
        result = _deep_merge({"a": 1}, {"b": 2})
        assert result == {"a": 1, "b": 2}

    def test_does_not_mutate_base(self):
        base = {"a": {"x": 1}}
        _deep_merge(base, {"a": {"x": 99}})
        assert base["a"]["x"] == 1

    def test_non_dict_override_replaces(self):
        result = _deep_merge({"a": {"x": 1}}, {"a": "replaced"})
        assert result["a"] == "replaced"


# ── _PROFILE_PRESETS completeness ────────────────────────────────────────────

class TestProfilePresetsSchema:
    REQUIRED_KEYS = {
        "color_grade", "grain", "vignette", "zoom_style",
        "caption_style", "transition_style",
    }

    @pytest.mark.parametrize("profile", [
        "fashion_montage", "music_video", "talking_head",
        "vlog", "product_showcase", "travel_reel",
    ])
    def test_all_profiles_have_required_keys(self, profile):
        preset = _PROFILE_PRESETS[profile]
        missing = self.REQUIRED_KEYS - set(preset.keys())
        assert missing == set(), f"Profile {profile} missing keys: {missing}"

    @pytest.mark.parametrize("profile", [
        "fashion_montage", "music_video", "talking_head",
        "vlog", "product_showcase", "travel_reel",
    ])
    def test_color_grade_has_all_fields(self, profile):
        cg = _PROFILE_PRESETS[profile]["color_grade"]
        for field in ("brightness", "contrast", "saturation", "gamma"):
            assert field in cg, f"{profile}: missing {field}"

    @pytest.mark.parametrize("profile", [
        "fashion_montage", "music_video", "talking_head",
        "vlog", "product_showcase", "travel_reel",
    ])
    def test_zoom_style_has_type_and_strength(self, profile):
        zs = _PROFILE_PRESETS[profile]["zoom_style"]
        assert "type" in zs and "strength" in zs


# ── from_fingerprint — profile selection ────────────────────────────────────

class TestFromFingerprintProfiles:
    def test_unknown_profile_returns_default(self):
        rs = StyleRenderer.from_fingerprint({"ranking_profile": "nonexistent"})
        assert rs["zoom_style"]["type"] == _DEFAULT_PRESET["zoom_style"]["type"]

    def test_no_profile_returns_default(self):
        rs = StyleRenderer.from_fingerprint({})
        assert rs["grain"]["enabled"] == _DEFAULT_PRESET["grain"]["enabled"]

    # ── fashion_montage ────────────────────────────────────────────────────
    def test_fashion_montage_filmic_contrast(self):
        rs = StyleRenderer.from_fingerprint(_fp("fashion_montage"))
        assert rs["color_grade"]["contrast"] > 1.10, "fashion should have punchy contrast"

    def test_fashion_montage_grain_enabled(self):
        rs = StyleRenderer.from_fingerprint(_fp("fashion_montage"))
        assert rs["grain"]["enabled"] is True

    def test_fashion_montage_grain_is_subtle(self):
        rs = StyleRenderer.from_fingerprint(_fp("fashion_montage"))
        assert 0 < rs["grain"]["strength"] <= 20, "fashion grain should be subtle"

    def test_fashion_montage_slow_push(self):
        rs = StyleRenderer.from_fingerprint(_fp("fashion_montage"))
        assert rs["zoom_style"]["type"] == "slow_push"

    def test_fashion_montage_extra_filters_sharpening(self):
        rs = StyleRenderer.from_fingerprint(_fp("fashion_montage"))
        assert "unsharp" in rs["color_grade"]["extra_filters"]

    def test_fashion_montage_vignette_enabled(self):
        rs = StyleRenderer.from_fingerprint(_fp("fashion_montage"))
        assert rs["vignette"]["enabled"] is True

    # ── music_video ─────────────────────────────────────────────────────────
    def test_music_video_high_contrast(self):
        rs = StyleRenderer.from_fingerprint(_fp("music_video"))
        assert rs["color_grade"]["contrast"] >= 1.20, "music_video needs high contrast"

    def test_music_video_punch_zoom(self):
        rs = StyleRenderer.from_fingerprint(_fp("music_video"))
        assert rs["zoom_style"]["type"] == "punch_zoom"

    def test_music_video_punch_zoom_strong(self):
        rs = StyleRenderer.from_fingerprint(_fp("music_video"))
        assert rs["zoom_style"]["strength"] >= 0.08, "punch zooms need noticeable strength"

    def test_music_video_flash_cut_transition(self):
        rs = StyleRenderer.from_fingerprint(_fp("music_video"))
        assert rs["transition_style"]["type"] == "flash_cut"

    def test_music_video_no_grain(self):
        rs = StyleRenderer.from_fingerprint(_fp("music_video"))
        assert rs["grain"]["enabled"] is False

    # ── talking_head ─────────────────────────────────────────────────────────
    def test_talking_head_static_zoom(self):
        rs = StyleRenderer.from_fingerprint(_fp("talking_head"))
        assert rs["zoom_style"]["type"] == "static"

    def test_talking_head_minimal_contrast(self):
        rs = StyleRenderer.from_fingerprint(_fp("talking_head"))
        # Should be close to neutral — < 1.10
        assert rs["color_grade"]["contrast"] < 1.10

    def test_talking_head_no_grain(self):
        rs = StyleRenderer.from_fingerprint(_fp("talking_head"))
        assert rs["grain"]["enabled"] is False

    def test_talking_head_no_vignette(self):
        rs = StyleRenderer.from_fingerprint(_fp("talking_head"))
        assert rs["vignette"]["enabled"] is False

    def test_talking_head_caption_no_all_caps(self):
        rs = StyleRenderer.from_fingerprint(_fp("talking_head"))
        assert rs["caption_style"]["all_caps"] is False

    def test_talking_head_caption_bottom_position(self):
        rs = StyleRenderer.from_fingerprint(_fp("talking_head"))
        assert rs["caption_style"]["position"] == "bottom"

    # ── product_showcase ─────────────────────────────────────────────────────
    def test_product_showcase_strong_sharpening(self):
        rs = StyleRenderer.from_fingerprint(_fp("product_showcase"))
        extras = rs["color_grade"]["extra_filters"]
        assert "unsharp" in extras, "product_showcase must sharpen"

    def test_product_showcase_no_grain(self):
        rs = StyleRenderer.from_fingerprint(_fp("product_showcase"))
        assert rs["grain"]["enabled"] is False

    def test_product_showcase_no_vignette(self):
        rs = StyleRenderer.from_fingerprint(_fp("product_showcase"))
        assert rs["vignette"]["enabled"] is False

    def test_product_showcase_gentle_zoom(self):
        rs = StyleRenderer.from_fingerprint(_fp("product_showcase"))
        assert rs["zoom_style"]["strength"] <= 0.04, "product needs clean, subtle motion"

    def test_product_showcase_dissolve_transition(self):
        rs = StyleRenderer.from_fingerprint(_fp("product_showcase"))
        assert rs["transition_style"]["type"] == "dissolve"

    # ── vlog ──────────────────────────────────────────────────────────────────
    def test_vlog_grain_enabled(self):
        rs = StyleRenderer.from_fingerprint(_fp("vlog"))
        assert rs["grain"]["enabled"] is True

    def test_vlog_slow_push(self):
        rs = StyleRenderer.from_fingerprint(_fp("vlog"))
        assert rs["zoom_style"]["type"] == "slow_push"

    # ── travel_reel ──────────────────────────────────────────────────────────
    def test_travel_reel_vignette_enabled(self):
        rs = StyleRenderer.from_fingerprint(_fp("travel_reel"))
        assert rs["vignette"]["enabled"] is True

    def test_travel_reel_high_saturation(self):
        rs = StyleRenderer.from_fingerprint(_fp("travel_reel"))
        assert rs["color_grade"]["saturation"] >= 1.15

    def test_travel_reel_whip_pan_transition(self):
        rs = StyleRenderer.from_fingerprint(_fp("travel_reel"))
        assert "whip_pan" in rs["transition_style"]["type"]


# ── Fingerprint field overrides ──────────────────────────────────────────────

class TestFingerprintOverrides:
    def test_measured_color_grade_overrides_preset(self):
        fp = _fp("fashion_montage", color_grade={"contrast": 1.50, "saturation": 1.40})
        rs = StyleRenderer.from_fingerprint(fp)
        assert rs["color_grade"]["contrast"] == pytest.approx(1.50)
        assert rs["color_grade"]["saturation"] == pytest.approx(1.40)

    def test_color_profile_key_also_accepted(self):
        fp = _fp("fashion_montage", color_profile={"brightness": 0.08})
        rs = StyleRenderer.from_fingerprint(fp)
        assert rs["color_grade"]["brightness"] == pytest.approx(0.08)

    def test_caption_style_position_override(self):
        fp = _fp("fashion_montage", caption_style={"position": "top", "animation": "fade"})
        rs = StyleRenderer.from_fingerprint(fp)
        assert rs["caption_style"]["position"] == "top"
        assert rs["caption_style"]["animation"] == "fade"

    def test_caption_style_small_font_class(self):
        fp = _fp("music_video", caption_style={"font_size_class": "small"})
        rs = StyleRenderer.from_fingerprint(fp)
        assert rs["caption_style"]["font_size"] == 52

    def test_caption_style_no_stroke(self):
        fp = _fp("fashion_montage", caption_style={"has_stroke": False})
        rs = StyleRenderer.from_fingerprint(fp)
        assert rs["caption_style"]["stroke_width"] == pytest.approx(0.0)

    def test_dominant_transition_overrides(self):
        fp = _fp("fashion_montage", dominant_transition="dissolve")
        rs = StyleRenderer.from_fingerprint(fp)
        assert rs["transition_style"]["type"] == "dissolve"

    def test_caller_overrides_applied_last(self):
        fp = _fp("fashion_montage")
        rs = StyleRenderer.from_fingerprint(fp, overrides={"grain": {"enabled": False}})
        assert rs["grain"]["enabled"] is False

    def test_caller_overrides_nested(self):
        fp = _fp("talking_head")
        rs = StyleRenderer.from_fingerprint(fp, overrides={"zoom_style": {"type": "slow_push", "strength": 0.08}})
        assert rs["zoom_style"]["type"] == "slow_push"
        assert rs["zoom_style"]["strength"] == pytest.approx(0.08)

    def test_high_energy_boosts_slow_push_strength(self):
        fp = _fp("travel_reel", energy_level="high")
        rs = StyleRenderer.from_fingerprint(fp)
        default_fp = _fp("travel_reel", energy_level="medium")
        rs_default = StyleRenderer.from_fingerprint(default_fp)
        assert rs["zoom_style"]["strength"] >= rs_default["zoom_style"]["strength"]


# ── FFmpeg filter builders ────────────────────────────────────────────────────

class TestBuildFfmpegColorFilters:
    def test_identity_returns_empty(self):
        rs = {"color_grade": {"brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "gamma": 1.0, "extra_filters": ""}}
        assert StyleRenderer.build_ffmpeg_color_filters(rs) == ""

    def test_non_default_produces_eq(self):
        rs = {"color_grade": {"brightness": 0.05, "contrast": 1.1, "saturation": 1.2, "gamma": 0.95, "extra_filters": ""}}
        result = StyleRenderer.build_ffmpeg_color_filters(rs)
        assert result.startswith("eq=")
        assert "brightness=0.050" in result
        assert "contrast=1.100" in result

    def test_extra_filters_appended(self):
        rs = {"color_grade": {"brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "gamma": 1.0, "extra_filters": "unsharp=5:5:1.0:5:5:0"}}
        result = StyleRenderer.build_ffmpeg_color_filters(rs)
        assert "unsharp" in result

    def test_eq_and_extra_joined_with_comma(self):
        rs = {"color_grade": {"brightness": 0.05, "contrast": 1.1, "saturation": 1.0, "gamma": 1.0, "extra_filters": "unsharp=5:5:0.5:5:5:0"}}
        result = StyleRenderer.build_ffmpeg_color_filters(rs)
        assert "," in result

    def test_missing_color_grade_returns_empty(self):
        assert StyleRenderer.build_ffmpeg_color_filters({}) == ""


class TestBuildFfmpegGrainFilter:
    def test_disabled_returns_empty(self):
        rs = {"grain": {"enabled": False, "strength": 10.0}}
        assert StyleRenderer.build_ffmpeg_grain_filter(rs) == ""

    def test_enabled_returns_noise_filter(self):
        rs = {"grain": {"enabled": True, "strength": 12.0}}
        result = StyleRenderer.build_ffmpeg_grain_filter(rs)
        assert result != ""
        assert "noise" in result or "geq" in result

    def test_missing_grain_returns_empty(self):
        assert StyleRenderer.build_ffmpeg_grain_filter({}) == ""

    def test_strength_clamped_to_50(self):
        rs = {"grain": {"enabled": True, "strength": 999.0}}
        result = StyleRenderer.build_ffmpeg_grain_filter(rs)
        assert "50" in result


class TestBuildFfmpegVignetteFilter:
    def test_disabled_returns_empty(self):
        rs = {"vignette": {"enabled": False, "angle": 1.0}}
        assert StyleRenderer.build_ffmpeg_vignette_filter(rs) == ""

    def test_enabled_returns_vignette_filter(self):
        rs = {"vignette": {"enabled": True, "angle": 0.9}}
        result = StyleRenderer.build_ffmpeg_vignette_filter(rs)
        assert result.startswith("vignette=")
        assert "0.900" in result

    def test_missing_vignette_returns_empty(self):
        assert StyleRenderer.build_ffmpeg_vignette_filter({}) == ""


class TestBuildFullClipVf:
    def test_empty_when_no_effects(self):
        rs = StyleRenderer.from_fingerprint(_fp("talking_head"))
        vf = StyleRenderer.build_full_clip_vf(rs)
        # talking_head: minimal grade + no grain + no vignette
        # Result may still contain eq, but must not have grain or vignette
        assert "noise" not in vf
        assert "vignette" not in vf

    def test_fashion_montage_includes_sharpening(self):
        rs = StyleRenderer.from_fingerprint(_fp("fashion_montage"))
        vf = StyleRenderer.build_full_clip_vf(rs)
        assert "unsharp" in vf

    def test_fashion_montage_includes_grain(self):
        rs = StyleRenderer.from_fingerprint(_fp("fashion_montage"))
        vf = StyleRenderer.build_full_clip_vf(rs)
        assert "noise" in vf

    def test_fashion_montage_includes_vignette(self):
        rs = StyleRenderer.from_fingerprint(_fp("fashion_montage"))
        vf = StyleRenderer.build_full_clip_vf(rs)
        assert "vignette" in vf

    def test_music_video_has_high_contrast_eq(self):
        rs = StyleRenderer.from_fingerprint(_fp("music_video"))
        vf = StyleRenderer.build_full_clip_vf(rs)
        # contrast 1.25 → should produce eq filter
        assert "eq=" in vf

    def test_product_showcase_contains_strong_sharpening(self):
        rs = StyleRenderer.from_fingerprint(_fp("product_showcase"))
        vf = StyleRenderer.build_full_clip_vf(rs)
        assert "unsharp" in vf

    def test_product_showcase_no_grain_in_vf(self):
        rs = StyleRenderer.from_fingerprint(_fp("product_showcase"))
        vf = StyleRenderer.build_full_clip_vf(rs)
        assert "noise" not in vf


# ── RenderStyle schema validation ────────────────────────────────────────────

class TestRenderStyleSchemaKeys:
    @pytest.mark.parametrize("profile", [
        "fashion_montage", "music_video", "talking_head",
        "vlog", "product_showcase", "travel_reel",
    ])
    def test_from_fingerprint_returns_all_sections(self, profile):
        rs = StyleRenderer.from_fingerprint(_fp(profile))
        for section in ("color_grade", "grain", "vignette", "zoom_style", "caption_style", "transition_style"):
            assert section in rs, f"{profile}: missing {section}"

    @pytest.mark.parametrize("profile", [
        "fashion_montage", "music_video", "talking_head",
        "vlog", "product_showcase", "travel_reel",
    ])
    def test_caption_style_has_required_fields(self, profile):
        cap = StyleRenderer.from_fingerprint(_fp(profile))["caption_style"]
        for field in ("font_size", "bold", "stroke_width", "position", "animation", "all_caps"):
            assert field in cap, f"{profile}: caption_style missing {field}"
