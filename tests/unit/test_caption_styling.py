"""Unit tests for advanced caption styling and animation.

Covers:
  1. CaptionEvent accepts all new typography fields with correct defaults
  2. CaptionEvent.to_render_spec passes every field through the text track dict
  3. Each profile produces a distinct caption_preset (font, case, stroke, shadow…)
  4. ASS output reflects profile typography (font name, stroke, spacing, shadow)
  5. ASS output for each animation type contains the correct ASS tag
  6. text_case transformations are applied in to_render_spec
  7. emphasis_words render as bold-tagged regions in ASS output
  8. EditPlanner stamps caption_preset typography onto generated CaptionEvents
  9. EditPlanner sets render_style on the returned EditTimeline
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.services.timeline_schema import (
    CaptionEvent,
    ClipEvent,
    EditTimeline,
)
from app.services.style_renderer import StyleRenderer, _PROFILE_PRESETS, _DEFAULT_PRESET
from app.services.render_engine import generate_ass_subtitles

# ── Helpers ──────────────────────────────────────────────────────────────────

CAPTION_PROFILES = [
    "fashion_montage",
    "music_video",
    "talking_head",
    "product_showcase",
    "travel_reel",
    "vlog",
]


def _fp(profile: str, **extra) -> dict:
    return {"ranking_profile": profile, **extra}


def _rs(profile: str) -> dict[str, Any]:
    return StyleRenderer.from_fingerprint(_fp(profile))


def _cap(**kw) -> CaptionEvent:
    defaults = dict(text="HELLO WORLD", start=0.0, end=2.0)
    defaults.update(kw)
    return CaptionEvent(**defaults)


def _track(**kw) -> dict:
    """Build a minimal text-track dict (as produced by to_render_spec)."""
    defaults = dict(
        start=0.0, end=2.0, text="HELLO WORLD",
        position="lower_third", animation="pop",
        safe_zone=0.0,
        font_family="Arial Black", font_size=64, font_weight="bold",
        text_case="asis", stroke_width=3.0, shadow=False,
        tracking=0.5, animation_duration=200, emphasis_words=[],
    )
    defaults.update(kw)
    return defaults


# ═══════════════════════════════════════════════════════════════════════════
#  1. CaptionEvent accepts new typography fields
# ═══════════════════════════════════════════════════════════════════════════

class TestCaptionEventTypographyFields:
    def test_default_font_family(self):
        cap = _cap()
        assert cap.font_family == "Arial Black"

    def test_default_font_weight(self):
        cap = _cap()
        assert cap.font_weight == "bold"

    def test_default_text_case(self):
        cap = _cap()
        assert cap.text_case == "uppercase"

    def test_default_stroke_width(self):
        cap = _cap()
        assert cap.stroke_width == 3.0

    def test_default_shadow_false(self):
        cap = _cap()
        assert cap.shadow is False

    def test_default_animation_duration(self):
        cap = _cap()
        assert cap.animation_duration == 200

    def test_default_emphasis_words_empty(self):
        cap = _cap()
        assert cap.emphasis_words == []

    def test_accepts_custom_font_family(self):
        cap = _cap(font_family="Helvetica Neue")
        assert cap.font_family == "Helvetica Neue"

    @pytest.mark.parametrize("fw", ["normal", "bold", "black"])
    def test_accepts_font_weight(self, fw):
        cap = _cap(font_weight=fw)
        assert cap.font_weight == fw

    @pytest.mark.parametrize("tc", ["uppercase", "titlecase", "lowercase", "asis"])
    def test_accepts_text_case(self, tc):
        cap = _cap(text_case=tc)
        assert cap.text_case == tc

    def test_accepts_stroke_width_float(self):
        cap = _cap(stroke_width=0.0)
        assert cap.stroke_width == 0.0

    def test_accepts_shadow_true(self):
        cap = _cap(shadow=True)
        assert cap.shadow is True

    def test_accepts_animation_duration(self):
        cap = _cap(animation_duration=100)
        assert cap.animation_duration == 100

    def test_accepts_emphasis_words(self):
        cap = _cap(emphasis_words=["WORLD", "HELLO"])
        assert cap.emphasis_words == ["WORLD", "HELLO"]

    def test_legacy_fields_preserved(self):
        """stroke: bool and case: str still work for backward compat."""
        cap = _cap(stroke=False, case="titlecase")
        assert cap.stroke is False
        assert cap.case == "titlecase"


# ═══════════════════════════════════════════════════════════════════════════
#  2. to_render_spec passes every typography field through
# ═══════════════════════════════════════════════════════════════════════════

class TestToRenderSpecCaptionTypography:
    def _make_tl(self, **cap_kw) -> EditTimeline:
        clip = ClipEvent(
            asset_id="c0", source_in=0.0, source_out=3.0,
            timeline_in=0.0, timeline_out=3.0,
        )
        cap = CaptionEvent(
            text="test caption",
            start=0.0, end=2.0,
            **cap_kw,
        )
        return EditTimeline(duration_sec=3.0, clips=[clip], captions=[cap])

    def test_font_family_in_spec(self):
        tl = self._make_tl(font_family="Impact")
        tc = tl.to_render_spec()["tracks"]["text"][0]
        assert tc["font_family"] == "Impact"

    def test_font_weight_in_spec(self):
        tl = self._make_tl(font_weight="black")
        tc = tl.to_render_spec()["tracks"]["text"][0]
        assert tc["font_weight"] == "black"

    def test_font_size_in_spec(self):
        tl = self._make_tl(font_size=88)
        tc = tl.to_render_spec()["tracks"]["text"][0]
        assert tc["font_size"] == 88

    def test_stroke_width_in_spec(self):
        tl = self._make_tl(stroke_width=0.0)
        tc = tl.to_render_spec()["tracks"]["text"][0]
        assert tc["stroke_width"] == 0.0

    def test_shadow_in_spec(self):
        tl = self._make_tl(shadow=True)
        tc = tl.to_render_spec()["tracks"]["text"][0]
        assert tc["shadow"] is True

    def test_tracking_in_spec(self):
        tl = self._make_tl(tracking=3.0)
        tc = tl.to_render_spec()["tracks"]["text"][0]
        assert tc["tracking"] == 3.0

    def test_animation_duration_in_spec(self):
        tl = self._make_tl(animation_duration=100)
        tc = tl.to_render_spec()["tracks"]["text"][0]
        assert tc["animation_duration"] == 100

    def test_emphasis_words_in_spec(self):
        tl = self._make_tl(emphasis_words=["WORLD"])
        tc = tl.to_render_spec()["tracks"]["text"][0]
        assert tc["emphasis_words"] == ["WORLD"]

    # text_case transformations applied in to_render_spec
    def test_uppercase_applied_to_text(self):
        tl = self._make_tl(text_case="uppercase")
        tl.captions[0].text = "mixed case"
        tc = tl.to_render_spec()["tracks"]["text"][0]
        assert tc["text"] == "MIXED CASE"

    def test_lowercase_applied_to_text(self):
        tl = self._make_tl(text_case="lowercase")
        tl.captions[0].text = "Mixed CASE"
        tc = tl.to_render_spec()["tracks"]["text"][0]
        assert tc["text"] == "mixed case"

    def test_titlecase_applied_to_text(self):
        tl = self._make_tl(text_case="titlecase")
        tl.captions[0].text = "hello world"
        tc = tl.to_render_spec()["tracks"]["text"][0]
        assert tc["text"] == "Hello World"

    def test_asis_no_transformation(self):
        tl = self._make_tl(text_case="asis", case="asis")
        tl.captions[0].text = "As-Is Text"
        tc = tl.to_render_spec()["tracks"]["text"][0]
        assert tc["text"] == "As-Is Text"


# ═══════════════════════════════════════════════════════════════════════════
#  3. Profiles produce distinct caption_presets
# ═══════════════════════════════════════════════════════════════════════════

class TestProfileCaptionPresetDistinctness:
    @pytest.mark.parametrize("profile", CAPTION_PROFILES)
    def test_caption_preset_has_font_family(self, profile):
        rs = _rs(profile)
        assert "font_family" in rs["caption_preset"]

    @pytest.mark.parametrize("profile", CAPTION_PROFILES)
    def test_caption_preset_has_font_weight(self, profile):
        rs = _rs(profile)
        assert "font_weight" in rs["caption_preset"]

    @pytest.mark.parametrize("profile", CAPTION_PROFILES)
    def test_caption_preset_has_text_case(self, profile):
        rs = _rs(profile)
        assert "text_case" in rs["caption_preset"]

    @pytest.mark.parametrize("profile", CAPTION_PROFILES)
    def test_caption_preset_has_stroke_width(self, profile):
        rs = _rs(profile)
        cp = rs["caption_preset"]
        assert "stroke_width" in cp
        assert isinstance(cp["stroke_width"], (int, float))

    @pytest.mark.parametrize("profile", CAPTION_PROFILES)
    def test_caption_preset_has_shadow(self, profile):
        rs = _rs(profile)
        assert "shadow" in rs["caption_preset"]

    @pytest.mark.parametrize("profile", CAPTION_PROFILES)
    def test_caption_preset_has_tracking(self, profile):
        rs = _rs(profile)
        assert "tracking" in rs["caption_preset"]

    @pytest.mark.parametrize("profile", CAPTION_PROFILES)
    def test_caption_preset_has_animation(self, profile):
        rs = _rs(profile)
        assert "animation" in rs["caption_preset"]

    @pytest.mark.parametrize("profile", CAPTION_PROFILES)
    def test_caption_preset_has_animation_duration(self, profile):
        rs = _rs(profile)
        assert "animation_duration" in rs["caption_preset"]

    def test_fashion_montage_no_stroke(self):
        rs = _rs("fashion_montage")
        assert rs["caption_preset"]["stroke_width"] == 0.0

    def test_music_video_heavy_stroke(self):
        rs = _rs("music_video")
        assert rs["caption_preset"]["stroke_width"] >= 3.5

    def test_talking_head_has_shadow(self):
        assert _rs("talking_head")["caption_preset"]["shadow"] is True

    def test_product_showcase_has_shadow(self):
        assert _rs("product_showcase")["caption_preset"]["shadow"] is True

    def test_fashion_montage_wide_tracking(self):
        rs = _rs("fashion_montage")
        assert rs["caption_preset"]["tracking"] >= 2.0

    def test_travel_reel_very_wide_tracking(self):
        rs = _rs("travel_reel")
        assert rs["caption_preset"]["tracking"] >= 2.5

    def test_music_video_snappy_animation_duration(self):
        rs = _rs("music_video")
        assert rs["caption_preset"]["animation_duration"] <= 150

    def test_product_showcase_slow_animation_duration(self):
        rs = _rs("product_showcase")
        assert rs["caption_preset"]["animation_duration"] >= 300

    def test_profiles_use_different_text_case(self):
        """At least two profiles must differ in text_case."""
        cases = {p: _rs(p)["caption_preset"]["text_case"] for p in CAPTION_PROFILES}
        assert len(set(cases.values())) > 1

    def test_profiles_use_different_font_weight(self):
        weights = {p: _rs(p)["caption_preset"]["font_weight"] for p in CAPTION_PROFILES}
        assert len(set(weights.values())) > 1

    def test_profiles_use_different_animations(self):
        anims = {p: _rs(p)["caption_preset"]["animation"] for p in CAPTION_PROFILES}
        assert len(set(anims.values())) > 1

    def test_fashion_montage_uses_fade_animation(self):
        assert _rs("fashion_montage")["caption_preset"]["animation"] == "fade"

    def test_music_video_uses_pop_animation(self):
        assert _rs("music_video")["caption_preset"]["animation"] == "pop"

    def test_travel_reel_uses_uppercase(self):
        assert _rs("travel_reel")["caption_preset"]["text_case"] == "uppercase"

    def test_talking_head_uses_titlecase(self):
        assert _rs("talking_head")["caption_preset"]["text_case"] == "titlecase"


# ═══════════════════════════════════════════════════════════════════════════
#  4. ASS output reflects profile typography
# ═══════════════════════════════════════════════════════════════════════════

class TestAssOutputTypography:
    def _gen(self, tmp_path, profile: str, tracks=None) -> str:
        rs = _rs(profile)
        out = str(tmp_path / f"{profile}.ass")
        tracks = tracks or []
        generate_ass_subtitles(tracks, out, render_style=rs)
        return Path(out).read_text()

    def test_font_family_in_style_line(self, tmp_path):
        content = self._gen(tmp_path, "fashion_montage")
        # fashion_montage uses font_weight="black" → "Arial Black Black" or just "Arial Black"
        assert "Arial" in content

    def test_music_video_stroke_in_style(self, tmp_path):
        content = self._gen(tmp_path, "music_video")
        cp = _rs("music_video")["caption_preset"]
        stroke = cp["stroke_width"]
        # ASS Outline appears in style line
        assert f"{stroke:.1f}" in content

    def test_talking_head_shadow_in_style(self, tmp_path):
        content = self._gen(tmp_path, "talking_head")
        # Shadow=1 appears in the style definition
        lines = [l for l in content.splitlines() if l.startswith("Style:")]
        # At least one style line should have shadow=1
        assert any(",1,2," in l or ",1,8," in l or re.search(r",\d+\.\d+,1,\d,", l) for l in lines), \
            f"Expected shadow>0 in style lines, got:\n" + "\n".join(lines)

    def test_fashion_montage_zero_stroke_in_style(self, tmp_path):
        content = self._gen(tmp_path, "fashion_montage")
        # stroke_width=0.0 → Outline=0.0 appears in style lines
        lines = [l for l in content.splitlines() if l.startswith("Style: ")]
        assert any("0.0" in l for l in lines), \
            f"Expected 0.0 stroke in style lines, got:\n" + "\n".join(lines)

    def test_travel_reel_wide_spacing_in_style(self, tmp_path):
        content = self._gen(tmp_path, "travel_reel")
        tracking = _rs("travel_reel")["caption_preset"]["tracking"]
        assert f"{tracking:.1f}" in content

    def test_profiles_produce_different_ass_headers(self, tmp_path):
        contents = {p: self._gen(tmp_path, p) for p in CAPTION_PROFILES}
        # At least one pair of profiles should differ in ASS style lines
        headers = [
            "\n".join(l for l in c.splitlines() if l.startswith("Style:"))
            for c in contents.values()
        ]
        assert len(set(headers)) > 1, "All profiles produce identical ASS styles"

    def test_safe_zone_margin_in_style(self, tmp_path):
        rs = _rs("music_video")
        out = str(tmp_path / "mv.ass")
        generate_ass_subtitles([], out, render_style=rs)
        content = Path(out).read_text()
        expected_margin = str(int(0.12 * 1920))
        assert expected_margin in content

    def test_no_render_style_uses_default_font(self, tmp_path):
        out = str(tmp_path / "default.ass")
        generate_ass_subtitles([], out)
        content = Path(out).read_text()
        assert "Arial" in content


# ═══════════════════════════════════════════════════════════════════════════
#  5. ASS animation tags per animation type
# ═══════════════════════════════════════════════════════════════════════════

class TestAssAnimationTags:
    def _ass_event(self, tmp_path, animation: str, anim_dur: int = 200,
                   text: str = "HELLO", **extra) -> str:
        out = str(tmp_path / f"anim_{animation}.ass")
        track = _track(animation=animation, animation_duration=anim_dur, text=text, **extra)
        generate_ass_subtitles([track], out)
        content = Path(out).read_text()
        # Return only the Dialogue lines
        return "\n".join(l for l in content.splitlines() if l.startswith("Dialogue:"))

    def test_pop_uses_fscx_fscy(self, tmp_path):
        event = self._ass_event(tmp_path, "pop")
        assert "\\fscx" in event and "\\fscy" in event

    def test_pop_t_tag_present(self, tmp_path):
        event = self._ass_event(tmp_path, "pop", anim_dur=150)
        assert "\\t(" in event

    def test_pop_duration_in_tag(self, tmp_path):
        event = self._ass_event(tmp_path, "pop", anim_dur=120)
        assert "120" in event

    def test_fade_uses_fad(self, tmp_path):
        event = self._ass_event(tmp_path, "fade")
        assert "\\fad(" in event

    def test_fade_duration_in_fad(self, tmp_path):
        event = self._ass_event(tmp_path, "fade", anim_dur=300)
        assert "300" in event

    def test_slide_up_uses_move(self, tmp_path):
        event = self._ass_event(tmp_path, "slide_up")
        assert "\\move(" in event

    def test_typewriter_uses_alpha(self, tmp_path):
        event = self._ass_event(tmp_path, "typewriter")
        assert "\\alpha" in event

    def test_none_has_no_animation_tag(self, tmp_path):
        event = self._ass_event(tmp_path, "none")
        # no fad, no fscx, no move in the event line
        assert "\\fad" not in event
        assert "\\fscx" not in event
        assert "\\move" not in event

    @pytest.mark.parametrize("anim", ["pop", "fade", "slide_up", "typewriter", "none"])
    def test_all_animations_produce_valid_dialogue_line(self, tmp_path, anim):
        event = self._ass_event(tmp_path, anim)
        assert event.startswith("Dialogue:")


# ═══════════════════════════════════════════════════════════════════════════
#  6. Per-clip typography override in ASS output
# ═══════════════════════════════════════════════════════════════════════════

class TestAssPerClipOverride:
    def test_different_font_size_adds_fs_tag(self, tmp_path):
        rs = _rs("music_video")  # profile font_size might differ from 88
        track = _track(font_size=88)
        out = str(tmp_path / "fs.ass")
        generate_ass_subtitles([track], out, render_style=rs)
        event_lines = [l for l in Path(out).read_text().splitlines()
                       if l.startswith("Dialogue:")]
        assert event_lines, "Expected at least one Dialogue line"
        # Only check fs tag if clip font size differs from profile size
        cp = rs["caption_preset"]
        if cp.get("font_size", 64) != 88:
            assert "\\fs88" in event_lines[0]

    def test_different_stroke_adds_bord_tag(self, tmp_path):
        rs = _rs("fashion_montage")   # profile stroke=0.0
        track = _track(stroke_width=5.0)  # clip overrides to 5.0
        out = str(tmp_path / "bord.ass")
        generate_ass_subtitles([track], out, render_style=rs)
        event_lines = [l for l in Path(out).read_text().splitlines()
                       if l.startswith("Dialogue:")]
        assert "\\bord5.0" in event_lines[0]

    def test_different_tracking_adds_fsp_tag(self, tmp_path):
        rs = _rs("music_video")   # profile tracking=0.0
        track = _track(tracking=5.0)
        out = str(tmp_path / "fsp.ass")
        generate_ass_subtitles([track], out, render_style=rs)
        event_lines = [l for l in Path(out).read_text().splitlines()
                       if l.startswith("Dialogue:")]
        cp = rs["caption_preset"]
        if abs(cp.get("tracking", 0.0) - 5.0) > 0.1:
            assert "\\fsp5.0" in event_lines[0]

    def test_same_values_no_override_tags(self, tmp_path):
        rs = _rs("vlog")
        cp = rs["caption_preset"]
        track = _track(
            font_family=cp.get("font_family", "Arial Black"),
            font_weight=cp.get("font_weight", "bold"),
            font_size=cp.get("font_size", 64),
            stroke_width=cp.get("stroke_width", 3.0),
            tracking=cp.get("tracking", 0.5),
            shadow=cp.get("shadow", False),
        )
        out = str(tmp_path / "nooverride.ass")
        generate_ass_subtitles([track], out, render_style=rs)
        events = [l for l in Path(out).read_text().splitlines()
                  if l.startswith("Dialogue:")]
        # No per-clip override tags expected (use precise patterns to avoid
        # false positives from animation tags like \fscx / \fscy)
        for tag in ("\\bord", "\\fsp", "\\shad"):
            assert tag not in events[0], f"Unexpected override tag {tag!r} in: {events[0]}"
        # \\fs must not appear as a standalone font-size override (\\fsNN)
        # while \\fscx / \\fscy from pop animation are acceptable
        import re as _re
        assert not _re.search(r'\\fs\d', events[0]), \
            f"Unexpected \\fsNN override tag in: {events[0]}"


# ═══════════════════════════════════════════════════════════════════════════
#  7. emphasis_words bold highlighting
# ═══════════════════════════════════════════════════════════════════════════

class TestEmphasisWords:
    def _gen_event(self, tmp_path, text: str, emphasis: list) -> str:
        track = _track(text=text, emphasis_words=emphasis, animation="none")
        out = str(tmp_path / "emph.ass")
        generate_ass_subtitles([track], out)
        return Path(out).read_text()

    def test_emphasis_word_wrapped_in_bold_tags(self, tmp_path):
        content = self._gen_event(tmp_path, "HELLO WORLD", ["WORLD"])
        assert "\\b1" in content or "b1}" in content

    def test_emphasis_multiple_words(self, tmp_path):
        content = self._gen_event(tmp_path, "FIRST AND LAST", ["FIRST", "LAST"])
        assert content.count("\\b1") >= 2 or content.count("b1}") >= 2

    def test_no_emphasis_no_bold_tags(self, tmp_path):
        content = self._gen_event(tmp_path, "PLAIN TEXT", [])
        events = [l for l in content.splitlines() if l.startswith("Dialogue:")]
        # The only bold could come from the style definition, not inline tags
        for line in events:
            assert "\\b1" not in line, f"Unexpected \\b1 tag in: {line}"

    def test_emphasis_case_insensitive(self, tmp_path):
        content = self._gen_event(tmp_path, "Hello World", ["world"])
        assert "\\b1" in content or "b1}" in content


# ═══════════════════════════════════════════════════════════════════════════
#  8. EditPlanner stamps caption_preset typography onto CaptionEvents
# ═══════════════════════════════════════════════════════════════════════════

class TestEditPlannerCaptionPresetWiring:
    def _mock_llm(self, captions: list[dict]) -> MagicMock:
        """Return an LLM mock that yields structured caption JSON."""
        llm = MagicMock()
        llm.chat.return_value = {
            "content": __import__("json").dumps({
                "shots": captions,
                "hook_index": 0,
            })
        }
        return llm

    def _fingerprint(self, profile: str = "music_video") -> dict:
        return {
            "ranking_profile": profile,
            "num_cuts": 3,
            "avg_shot_duration": 2.0,
            "pace": "fast",
            "shot_durations": [2.0, 2.0, 2.0, 2.0],
            "transitions": ["hard_cut"],
            "caption_style": {"position": "center", "animation": "pop", "all_caps": True},
            "energy_level": "high",
        }

    def _footage(self) -> list[dict]:
        return [
            {
                "asset_id": f"v{i}",
                "file_path": f"/fake/v{i}.mp4",
                "duration": 10.0,
                "usable_segments": [
                    {"start": 0.0, "end": 4.0, "score": 0.9, "asset_id": f"v{i}",
                     "tags": ["motion"], "description": "clip"}
                ],
            }
            for i in range(4)
        ]

    def test_captions_get_font_family_from_preset(self):
        from app.services.edit_planner import EditPlanner
        fp = self._fingerprint("fashion_montage")
        captions_data = [{"caption": "word", "moment_type": "hook"},
                         {"caption": "next", "moment_type": "build"},
                         {"caption": "third", "moment_type": "build"},
                         {"caption": "last", "moment_type": "build"}]
        llm = self._mock_llm(captions_data)
        timeline = EditPlanner(llm).plan(fp, self._footage())
        rs = StyleRenderer.from_fingerprint({"ranking_profile": "fashion_montage"})
        expected_font = rs["caption_preset"]["font_family"]
        for cap in timeline.captions:
            assert cap.font_family == expected_font, \
                f"Expected font {expected_font!r}, got {cap.font_family!r}"

    def test_captions_get_text_case_from_preset(self):
        from app.services.edit_planner import EditPlanner
        fp = self._fingerprint("talking_head")
        captions_data = [{"caption": "hello world", "moment_type": "hook"},
                         {"caption": "second line", "moment_type": "build"},
                         {"caption": "third line", "moment_type": "build"},
                         {"caption": "last line", "moment_type": "build"}]
        timeline = EditPlanner(self._mock_llm(captions_data)).plan(fp, self._footage())
        # talking_head → titlecase
        for cap in timeline.captions:
            assert cap.text_case == "titlecase"

    def test_music_video_captions_use_uppercase(self):
        from app.services.edit_planner import EditPlanner
        fp = self._fingerprint("music_video")
        captions_data = [{"caption": "go hard", "moment_type": "hook"},
                         {"caption": "drop it", "moment_type": "build"},
                         {"caption": "fire beat", "moment_type": "build"},
                         {"caption": "last drop", "moment_type": "build"}]
        timeline = EditPlanner(self._mock_llm(captions_data)).plan(fp, self._footage())
        for cap in timeline.captions:
            assert cap.text == cap.text.upper(), f"Expected uppercase text, got {cap.text!r}"

    def test_talking_head_captions_use_titlecase(self):
        from app.services.edit_planner import EditPlanner
        fp = self._fingerprint("talking_head")
        captions_data = [{"caption": "hello world", "moment_type": "hook"},
                         {"caption": "second line", "moment_type": "build"},
                         {"caption": "third", "moment_type": "build"},
                         {"caption": "last", "moment_type": "build"}]
        timeline = EditPlanner(self._mock_llm(captions_data)).plan(fp, self._footage())
        # With titlecase text_case, text should be title-cased
        for cap in timeline.captions:
            assert cap.text == cap.text.title(), f"Expected title case, got {cap.text!r}"

    def test_captions_get_stroke_width_from_preset(self):
        from app.services.edit_planner import EditPlanner
        fp = self._fingerprint("fashion_montage")
        captions_data = [{"caption": "look", "moment_type": "hook"},
                         {"caption": "next", "moment_type": "build"},
                         {"caption": "after", "moment_type": "build"},
                         {"caption": "last", "moment_type": "build"}]
        timeline = EditPlanner(self._mock_llm(captions_data)).plan(fp, self._footage())
        rs = StyleRenderer.from_fingerprint({"ranking_profile": "fashion_montage"})
        expected_sw = rs["caption_preset"]["stroke_width"]
        for cap in timeline.captions:
            assert abs(cap.stroke_width - expected_sw) < 0.01


# ═══════════════════════════════════════════════════════════════════════════
#  9. EditPlanner sets render_style on returned timeline
# ═══════════════════════════════════════════════════════════════════════════

class TestEditPlannerSetsRenderStyle:
    def _mock_llm(self) -> MagicMock:
        llm = MagicMock()
        llm.chat.return_value = {
            "content": __import__("json").dumps({
                "shots": [{"caption": "test", "moment_type": "hook"},
                           {"caption": "b", "moment_type": "build"},
                           {"caption": "c", "moment_type": "build"}],
                "hook_index": 0,
            })
        }
        return llm

    def _footage(self) -> list[dict]:
        return [
            {
                "asset_id": f"v{i}", "file_path": f"/fake/v{i}.mp4",
                "duration": 10.0,
                "usable_segments": [
                    {"start": 0.0, "end": 3.5, "score": 0.9, "asset_id": f"v{i}",
                     "tags": [], "description": ""}
                ],
            }
            for i in range(3)
        ]

    def test_timeline_has_render_style(self):
        from app.services.edit_planner import EditPlanner
        fp = {"ranking_profile": "vlog", "num_cuts": 2, "avg_shot_duration": 3.0,
              "pace": "medium", "shot_durations": [3.0, 3.0, 3.0],
              "transitions": ["hard_cut"], "caption_style": {}, "energy_level": "medium"}
        timeline = EditPlanner(self._mock_llm()).plan(fp, self._footage())
        assert timeline.render_style is not None

    def test_render_style_matches_fingerprint_profile(self):
        from app.services.edit_planner import EditPlanner
        for profile in ("travel_reel", "music_video", "talking_head"):
            fp = {"ranking_profile": profile, "num_cuts": 2,
                  "avg_shot_duration": 3.0, "pace": "fast",
                  "shot_durations": [3.0, 3.0, 3.0],
                  "transitions": ["hard_cut"], "caption_style": {}, "energy_level": "high"}
            timeline = EditPlanner(self._mock_llm()).plan(fp, self._footage())
            assert timeline.render_style is not None
            assert "caption_preset" in timeline.render_style
            assert "motion_preset" in timeline.render_style

    def test_render_style_caption_preset_has_typography(self):
        from app.services.edit_planner import EditPlanner
        fp = {"ranking_profile": "product_showcase", "num_cuts": 2,
              "avg_shot_duration": 3.0, "pace": "slow",
              "shot_durations": [3.0, 3.0, 3.0],
              "transitions": ["dissolve"], "caption_style": {}, "energy_level": "low"}
        timeline = EditPlanner(self._mock_llm()).plan(fp, self._footage())
        cp = timeline.render_style["caption_preset"]
        for key in ("font_family", "font_weight", "text_case",
                    "stroke_width", "shadow", "tracking", "animation"):
            assert key in cp, f"render_style.caption_preset missing {key!r}"

    def test_explicit_render_style_used_when_supplied(self):
        from app.services.edit_planner import EditPlanner
        fp = {"ranking_profile": "vlog", "num_cuts": 2, "avg_shot_duration": 3.0,
              "pace": "medium", "shot_durations": [3.0, 3.0, 3.0],
              "transitions": ["hard_cut"], "caption_style": {}, "energy_level": "medium"}
        custom_rs = {"caption_preset": {"safe_zone_frac": 0.99}}
        timeline = EditPlanner(self._mock_llm()).plan(
            fp, self._footage(), render_style=custom_rs
        )
        assert timeline.render_style["caption_preset"]["safe_zone_frac"] == 0.99
