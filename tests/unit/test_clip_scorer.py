"""Unit tests for app/services/clip_scorer.py.

Verifies:
  - ClipScores dataclass construction and serialisation
  - ReferenceStyle.from_fingerprint() parses all fields correctly
  - Each scoring dimension returns values in [0, 1]
  - Known boundary conditions (no prev, same face, identical motion, etc.)
  - ClipRanker.rank() sorts by composite score, respects used_ids and duration
  - Emotional arc: hook position rewards high-intensity clips
  - Motion continuity: large delta is penalised; same-motion scores ~1
  - Face consistency: same hash → 1.0; mismatch → 0.4
"""
from __future__ import annotations

import math
import pytest

from app.services.clip_scorer import (
    PROFILE_WEIGHTS,
    ClipRanker,
    ClipScores,
    ReferenceStyle,
    _cosine_similarity,
    _desired_arc_intensity,
    _detect_subject_continuity,
    _resolve_profile_weights,
)

# ── Shared fixtures ──────────────────────────────────────────────────────────

FINGERPRINT = {
    "pace":           "fast",
    "energy_level":   "high",
    "motion_style":   {"primary": "slow_push"},
    "avg_shot_duration": 0.7,
    "color_grade":    {"luma_avg": 145.0, "saturation": 1.2},
    "tone":           "aspirational",
    "shot_durations": [0.7, 0.7, 0.7],
}

# Sharp, stable, normal exposure — representative of high quality b-roll
SEG_SHARP = {
    "asset_id":      "footage_00",
    "start":         0.0,
    "end":           2.0,
    "score":         9.2,
    "sharpness":     12.0,
    "motion":        1.5,
    "tags":          ["sharp_detail"],
    "face_present":  False,
    "face_hash":     None,
    "intensity":     5.0,
    "clip_quality":  {"blur_score": 0.10, "brightness": "normal", "stability": "stable"},
}

# Soft, shaky, dark — representative of low quality footage
SEG_SOFT = {
    "asset_id":      "footage_01",
    "start":         0.0,
    "end":           2.0,
    "score":         3.0,
    "sharpness":     1.5,
    "motion":        0.1,
    "tags":          ["soft_blur", "static"],
    "face_present":  False,
    "face_hash":     None,
    "intensity":     0.5,
    "clip_quality":  {"blur_score": 0.80, "brightness": "dark", "stability": "shaky"},
}

# High motion, slight movement, face present — energetic action clip
SEG_MOTION = {
    "asset_id":      "footage_02",
    "start":         0.0,
    "end":           2.0,
    "score":         7.0,
    "sharpness":     8.0,
    "motion":        6.5,
    "tags":          ["sharp_detail", "motion"],
    "face_present":  True,
    "face_hash":     42,
    "intensity":     8.0,
    "clip_quality":  {"blur_score": 0.15, "brightness": "normal", "stability": "slight_movement"},
}


# ── _cosine_similarity ───────────────────────────────────────────────────────

class TestCosimeSimilarity:
    def test_identical_vectors_returns_1(self):
        v = [1.0, 0.5, 0.7, 0.3]
        assert _cosine_similarity(v, v) == pytest.approx(1.0, abs=1e-6)

    def test_orthogonal_vectors_returns_0(self):
        assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0, abs=1e-6)

    def test_zero_vector_returns_neutral(self):
        assert _cosine_similarity([0.0, 0.0], [1.0, 1.0]) == pytest.approx(0.5)

    def test_mismatched_lengths_returns_neutral(self):
        assert _cosine_similarity([1.0, 2.0], [1.0]) == pytest.approx(0.5)

    def test_empty_vector_returns_neutral(self):
        assert _cosine_similarity([], []) == pytest.approx(0.5)

    def test_result_clipped_to_0_1(self):
        # All-positive vectors → cosine in [0, 1]; no negative case needed
        for pair in [([0.1], [0.9]), ([0.5, 0.5], [0.5, 0.5]), ([1.0, 0.0], [1.0, 0.0])]:
            s = _cosine_similarity(*pair)
            assert 0.0 <= s <= 1.0, f"Out of range: {s}"


# ── _desired_arc_intensity ───────────────────────────────────────────────────

class TestDesiredArcIntensity:
    def test_hook_position_high(self):
        i = _desired_arc_intensity(0.0, 0.5)
        assert i >= 0.5

    def test_climax_position_peaks(self):
        hook_i   = _desired_arc_intensity(0.0,  0.5)
        climax_i = _desired_arc_intensity(0.60, 0.5)
        assert climax_i >= hook_i * 0.9

    def test_build_rises_monotonically(self):
        # Positions in the build window (0.15 – 0.50) should increase
        vals = [_desired_arc_intensity(p, 0.5) for p in [0.20, 0.30, 0.40, 0.49]]
        for i in range(len(vals) - 1):
            assert vals[i + 1] >= vals[i], f"Not rising at index {i}: {vals}"

    def test_all_positions_in_valid_range(self):
        for p in [0.0, 0.1, 0.3, 0.5, 0.6, 0.75, 0.9, 1.0]:
            i = _desired_arc_intensity(p, 0.5)
            assert 0.0 <= i <= 1.1, f"Out of range at p={p}: {i}"  # slight overshoot ok

    def test_low_base_intensity_still_varies(self):
        low  = _desired_arc_intensity(0.0,  0.2)
        high = _desired_arc_intensity(0.6,  0.2)
        # Even a low-energy reference should have a range
        assert abs(low - high) >= 0.01


# ── ReferenceStyle ───────────────────────────────────────────────────────────

class TestReferenceStyle:
    def test_from_fingerprint_pace_and_energy(self):
        ref = ReferenceStyle.from_fingerprint(FINGERPRINT)
        assert ref.pace         == "fast"
        assert ref.energy_level == "high"
        assert ref.target_intensity > 0.5

    def test_motion_style_from_dict(self):
        ref = ReferenceStyle.from_fingerprint(FINGERPRINT)
        assert ref.motion_style == "slow_push"

    def test_motion_style_from_string(self):
        fp  = {**FINGERPRINT, "motion_style": "zoom_in"}
        ref = ReferenceStyle.from_fingerprint(fp)
        assert ref.motion_style == "zoom_in"

    def test_color_grade_parsed(self):
        ref = ReferenceStyle.from_fingerprint(FINGERPRINT)
        assert ref.color_brightness == pytest.approx(145.0)
        assert ref.color_saturation == pytest.approx(1.2)

    def test_empty_fingerprint_defaults(self):
        ref = ReferenceStyle.from_fingerprint({})
        assert ref.avg_shot_duration > 0
        assert ref.pace  == "medium"
        assert ref.tone  == "neutral"
        assert 0.0 < ref.target_intensity < 1.0


# ── ClipRanker: raw_quality ──────────────────────────────────────────────────

class TestRawQuality:
    def setup_method(self):
        self.ranker = ClipRanker.from_fingerprint(FINGERPRINT)

    def test_score_12_returns_1(self):
        assert self.ranker._raw_quality({"score": 12.0}) == pytest.approx(1.0)

    def test_score_0_returns_0(self):
        assert self.ranker._raw_quality({"score": 0.0}) == pytest.approx(0.0)

    def test_mid_score_in_range(self):
        q = self.ranker._raw_quality({"score": 6.0})
        assert 0.4 < q < 0.6

    def test_capped_at_1(self):
        assert self.ranker._raw_quality({"score": 999.0}) == pytest.approx(1.0)

    def test_missing_score_uses_default(self):
        q = self.ranker._raw_quality({})
        assert 0.0 <= q <= 1.0


# ── ClipRanker: motion_continuity ───────────────────────────────────────────

class TestMotionContinuity:
    def setup_method(self):
        self.ranker = ClipRanker.from_fingerprint(FINGERPRINT)

    def test_no_prev_returns_1(self):
        assert self.ranker._motion_continuity(SEG_SHARP, None) == pytest.approx(1.0)

    def test_identical_motion_returns_1(self):
        prev = {**SEG_SHARP, "motion": 2.0}
        curr = {**SEG_SHARP, "motion": 2.0}
        assert self.ranker._motion_continuity(curr, prev) == pytest.approx(1.0)

    def test_large_delta_heavily_penalised(self):
        prev  = {**SEG_SHARP, "motion": 0.0}
        curr  = {**SEG_SHARP, "motion": 8.0}
        score = self.ranker._motion_continuity(curr, prev)
        assert score < 0.10   # exp(-64/9) ≈ 0.00083

    def test_small_delta_near_1(self):
        prev  = {**SEG_SHARP, "motion": 1.5}
        curr  = {**SEG_SHARP, "motion": 2.0}
        score = self.ranker._motion_continuity(curr, prev)
        assert score > 0.90

    def test_output_always_in_0_1(self):
        for m1, m2 in [(0.0, 10.0), (5.0, 0.0), (3.0, 3.5)]:
            s = self.ranker._motion_continuity(
                {**SEG_SHARP, "motion": m2},
                {**SEG_SHARP, "motion": m1},
            )
            assert 0.0 <= s <= 1.0


# ── ClipRanker: semantic_fit ─────────────────────────────────────────────────

class TestSemanticFit:
    def setup_method(self):
        self.ranker = ClipRanker.from_fingerprint(FINGERPRINT)

    def test_returns_value_in_0_1(self):
        for seg in [SEG_SHARP, SEG_SOFT, SEG_MOTION]:
            s = self.ranker._semantic_fit(seg)
            assert 0.0 <= s <= 1.0, f"Out of range for {seg['asset_id']}: {s}"

    def test_sharp_segment_scores_above_floor(self):
        s = self.ranker._semantic_fit(SEG_SHARP)
        assert s > 0.30

    def test_missing_fields_graceful(self):
        s = self.ranker._semantic_fit({"score": 5.0})
        assert 0.0 <= s <= 1.0

    def test_dominant_color_affects_brightness_harmony(self):
        # Ref luma ≈ 145 (warm/bright).  Near-ref colour should score better.
        near  = {**SEG_SHARP, "dominant_color": [180, 160, 100]}   # ~warm 145 luma
        far   = {**SEG_SHARP, "dominant_color": [30,  30,  30]}    # dark
        assert self.ranker._semantic_fit(near) >= self.ranker._semantic_fit(far)


# ── ClipRanker: aesthetic_quality ───────────────────────────────────────────

class TestAestheticQuality:
    def setup_method(self):
        self.ranker = ClipRanker.from_fingerprint(FINGERPRINT)

    def test_sharp_stable_normal_scores_high(self):
        assert self.ranker._aesthetic_quality(SEG_SHARP) > 0.60

    def test_soft_dark_shaky_scores_low(self):
        assert self.ranker._aesthetic_quality(SEG_SOFT)  < 0.45

    def test_sharp_beats_soft(self):
        assert (
            self.ranker._aesthetic_quality(SEG_SHARP)
            > self.ranker._aesthetic_quality(SEG_SOFT)
        )

    def test_output_in_0_1(self):
        for seg in [SEG_SHARP, SEG_SOFT, SEG_MOTION]:
            s = self.ranker._aesthetic_quality(seg)
            assert 0.0 <= s <= 1.0


# ── ClipRanker: visual_intensity ─────────────────────────────────────────────

class TestVisualIntensity:
    def setup_method(self):
        self.ranker = ClipRanker.from_fingerprint(FINGERPRINT)

    def test_precomputed_intensity_field_used(self):
        # SEG_MOTION has intensity=8.0 → normalises to 0.8
        assert self.ranker._visual_intensity(SEG_MOTION) == pytest.approx(0.8)

    def test_high_motion_means_high_intensity(self):
        seg = {**SEG_SHARP, "intensity": None, "motion": 7.0, "tags": ["motion"]}
        assert self.ranker._visual_intensity(seg) > 0.60

    def test_static_tag_reduces_vs_moving(self):
        moving = {**SEG_SHARP, "intensity": None, "motion": 2.0, "tags": []}
        static = {**SEG_SHARP, "intensity": None, "motion": 2.0, "tags": ["static"]}
        assert self.ranker._visual_intensity(moving) >= self.ranker._visual_intensity(static)

    def test_output_in_0_1(self):
        for seg in [SEG_SHARP, SEG_SOFT, SEG_MOTION]:
            s = self.ranker._visual_intensity(seg)
            assert 0.0 <= s <= 1.0

    def test_zero_motion_no_tags_minimal_intensity(self):
        seg = {**SEG_SHARP, "intensity": None, "motion": 0.0, "tags": []}
        assert self.ranker._visual_intensity(seg) < 0.20


# ── ClipRanker: face_consistency ─────────────────────────────────────────────

class TestFaceConsistency:
    def setup_method(self):
        self.ranker = ClipRanker.from_fingerprint(FINGERPRINT)

    def test_no_prev_returns_1(self):
        assert self.ranker._face_consistency(SEG_MOTION, None) == pytest.approx(1.0)

    def test_same_face_hash_returns_1(self):
        prev = {**SEG_MOTION, "face_present": True,  "face_hash": 42}
        curr = {**SEG_MOTION, "face_present": True,  "face_hash": 42}
        assert self.ranker._face_consistency(curr, prev) == pytest.approx(1.0)

    def test_different_face_hash_returns_0_6(self):
        prev = {**SEG_MOTION, "face_present": True,  "face_hash": 42}
        curr = {**SEG_MOTION, "face_present": True,  "face_hash": 99}
        assert self.ranker._face_consistency(curr, prev) == pytest.approx(0.6)

    def test_both_no_face_returns_0_8(self):
        prev = {**SEG_SHARP, "face_present": False}
        curr = {**SEG_SHARP, "face_present": False}
        assert self.ranker._face_consistency(curr, prev) == pytest.approx(0.8)

    def test_face_mismatch_returns_0_4(self):
        prev_face    = {**SEG_MOTION, "face_present": True,  "face_hash": 42}
        curr_no_face = {**SEG_SHARP,  "face_present": False}
        assert self.ranker._face_consistency(curr_no_face, prev_face) == pytest.approx(0.4)

    def test_both_face_no_hash_returns_0_7(self):
        prev = {**SEG_MOTION, "face_present": True, "face_hash": None}
        curr = {**SEG_MOTION, "face_present": True, "face_hash": None}
        assert self.ranker._face_consistency(curr, prev) == pytest.approx(0.7)


# ── ClipRanker: arc_fit ───────────────────────────────────────────────────────

class TestArcFit:
    def setup_method(self):
        self.ranker = ClipRanker.from_fingerprint(FINGERPRINT)

    def test_hook_position_rewards_high_intensity(self):
        high = {**SEG_MOTION, "intensity": 9.0}
        low  = {**SEG_SOFT,   "intensity": 1.0}
        assert self.ranker._arc_fit(high, 0.0) > self.ranker._arc_fit(low, 0.0)

    def test_all_positions_output_in_0_1(self):
        for pos in [0.0, 0.1, 0.3, 0.5, 0.6, 0.8, 1.0]:
            for seg in [SEG_SHARP, SEG_SOFT, SEG_MOTION]:
                s = self.ranker._arc_fit(seg, pos)
                assert 0.0 <= s <= 1.0, f"arc_fit out of range at pos={pos}: {s}"

    def test_climax_position_rewards_high_intensity(self):
        high = {**SEG_MOTION, "intensity": 9.0}
        low  = {**SEG_SOFT,   "intensity": 0.5}
        assert self.ranker._arc_fit(high, 0.65) > self.ranker._arc_fit(low, 0.65)


# ── ClipRanker: rank() ────────────────────────────────────────────────────────

class TestClipRankerRank:
    def setup_method(self):
        self.ranker     = ClipRanker.from_fingerprint(FINGERPRINT)
        self.candidates = [SEG_SHARP, SEG_SOFT, SEG_MOTION]

    def test_returns_list(self):
        result = self.ranker.rank(
            self.candidates, used_ids=set(), shot_dur=1.5,
            prev_segment=None, slot_index=0, total_slots=5,
        )
        assert isinstance(result, list)

    def test_sorted_by_composite_descending(self):
        result = self.ranker.rank(
            self.candidates, used_ids=set(), shot_dur=1.0,
            prev_segment=None, slot_index=0, total_slots=5,
        )
        scores = [s["_clip_scores"].composite for s in result]
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1], (
                f"Not descending at index {i}: {scores[i]:.4f} < {scores[i+1]:.4f}"
            )

    def test_used_ids_excluded_from_primary_results(self):
        uid    = f"{SEG_SHARP['asset_id']}_{SEG_SHARP['start']:.3f}"
        result = self.ranker.rank(
            self.candidates, used_ids={uid}, shot_dur=1.0,
            prev_segment=None, slot_index=0, total_slots=5,
        )
        # The used segment should not appear in the first-pass results
        # (it may appear in fallback, but with two other candidates it won't)
        returned_uids = {f"{s['asset_id']}_{s['start']:.3f}" for s in result}
        assert uid not in returned_uids

    def test_result_carries_clip_scores(self):
        result = self.ranker.rank(
            self.candidates, used_ids=set(), shot_dur=1.0,
            prev_segment=None, slot_index=0, total_slots=5,
        )
        assert len(result) > 0
        for seg in result:
            assert "_clip_scores" in seg
            scores = seg["_clip_scores"]
            assert hasattr(scores, "composite")
            assert hasattr(scores, "motion_continuity")
            assert hasattr(scores, "face_consistency")

    def test_empty_candidates_returns_empty(self):
        result = self.ranker.rank(
            [], used_ids=set(), shot_dur=1.0,
            prev_segment=None, slot_index=0, total_slots=5,
        )
        assert result == []

    def test_all_used_relaxes_to_fallback(self):
        all_used = {f"{s['asset_id']}_{s['start']:.3f}" for s in self.candidates}
        result   = self.ranker.rank(
            self.candidates, used_ids=all_used, shot_dur=1.0,
            prev_segment=None, slot_index=0, total_slots=5,
        )
        assert len(result) > 0   # fallback always returns something

    def test_duration_too_long_triggers_fallback(self):
        # All candidates are 2s long; request 10s → duration filter fails
        result = self.ranker.rank(
            self.candidates, used_ids=set(), shot_dur=10.0,
            prev_segment=None, slot_index=0, total_slots=5,
        )
        # Fallback should still return all candidates rather than crashing
        assert isinstance(result, list)

    def test_motion_continuity_drives_ranking(self):
        """Clips whose motion matches the previous clip should rank higher."""
        # prev has motion=1.5; SEG_SHARP also has motion=1.5 → continuity≈1.0
        # SEG_MOTION has motion=6.5 → big delta → continuity≈0.001
        # Also face_consistency penalises SEG_MOTION (prev.no_face vs MOTION.face)
        prev   = {**SEG_SHARP, "motion": 1.5}
        result = self.ranker.rank(
            [SEG_MOTION, SEG_SHARP],
            used_ids=set(), shot_dur=1.0,
            prev_segment=prev, slot_index=2, total_slots=5,
        )
        assert result[0]["asset_id"] == SEG_SHARP["asset_id"], (
            "SEG_SHARP should rank first due to better motion continuity and face consistency"
        )

    def test_high_quality_beats_low_quality_without_prev(self):
        """Without a previous clip, higher raw quality should lead."""
        result = self.ranker.rank(
            [SEG_SOFT, SEG_SHARP],
            used_ids=set(), shot_dur=1.0,
            prev_segment=None, slot_index=0, total_slots=5,
        )
        assert result[0]["asset_id"] == SEG_SHARP["asset_id"], (
            "SEG_SHARP (score=9.2) should beat SEG_SOFT (score=3.0)"
        )


# ── ClipScores dataclass ──────────────────────────────────────────────────────

class TestClipScores:
    def test_asdict_contains_all_seven_dimensions_and_composite(self):
        scores = ClipScores(
            raw_quality=0.8, motion_continuity=0.7, semantic_fit=0.6,
            aesthetic_quality=0.75, visual_intensity=0.5,
            face_consistency=0.9, arc_fit=0.6, composite=0.7,
        )
        d = scores.asdict()
        for key in [
            "raw_quality", "motion_continuity", "semantic_fit",
            "aesthetic_quality", "visual_intensity",
            "face_consistency", "arc_fit", "composite",
        ]:
            assert key in d, f"Missing key: {key}"

    def test_default_values_are_midpoint(self):
        scores = ClipScores()
        assert scores.composite        == pytest.approx(0.5)
        assert scores.raw_quality      == pytest.approx(0.5)
        assert scores.face_consistency == pytest.approx(0.5)

    def test_all_dimensions_present_when_constructed_explicitly(self):
        scores = ClipScores(
            raw_quality=1.0, motion_continuity=1.0, semantic_fit=1.0,
            aesthetic_quality=1.0, visual_intensity=1.0,
            face_consistency=1.0, arc_fit=1.0, composite=1.0,
        )
        assert scores.composite == pytest.approx(1.0)


# ── PROFILE_WEIGHTS registry ─────────────────────────────────────────────────

_EXPECTED_PROFILES = {
    "fashion_montage", "talking_head", "travel_reel",
    "product_showcase", "music_video", "vlog",
}
_WEIGHT_KEYS = {
    "raw_quality", "motion_continuity", "semantic_fit",
    "aesthetic_quality", "visual_intensity", "face_consistency", "arc_fit",
}


class TestProfileWeights:
    def test_all_six_profiles_present(self):
        assert set(PROFILE_WEIGHTS.keys()) == _EXPECTED_PROFILES

    def test_each_profile_has_all_seven_keys(self):
        for name, w in PROFILE_WEIGHTS.items():
            assert set(w.keys()) == _WEIGHT_KEYS, f"{name} missing keys"

    def test_each_profile_sums_to_1(self):
        for name, w in PROFILE_WEIGHTS.items():
            total = sum(w.values())
            assert total == pytest.approx(1.0, abs=1e-9), (
                f"{name} weights sum to {total}"
            )

    def test_talking_head_has_highest_face_weight(self):
        # talking_head must prioritise face_consistency more than any other profile
        th_face = PROFILE_WEIGHTS["talking_head"]["face_consistency"]
        for name, w in PROFILE_WEIGHTS.items():
            if name != "talking_head":
                assert th_face >= w["face_consistency"], (
                    f"talking_head face weight < {name}"
                )

    def test_music_video_has_highest_arc_weight(self):
        mv_arc = PROFILE_WEIGHTS["music_video"]["arc_fit"]
        for name, w in PROFILE_WEIGHTS.items():
            if name != "music_video":
                assert mv_arc >= w["arc_fit"], f"music_video arc < {name}"

    def test_non_face_profiles_have_low_face_weight(self):
        for name in ("travel_reel", "music_video", "product_showcase"):
            assert PROFILE_WEIGHTS[name]["face_consistency"] <= 0.10, (
                f"{name} face weight unexpectedly high"
            )


# ── _resolve_profile_weights ─────────────────────────────────────────────────

class TestResolveProfileWeights:
    def test_no_profile_returns_defaults(self):
        w = _resolve_profile_weights({}, None)
        assert w["raw_quality"] == pytest.approx(0.20)
        assert w["face_consistency"] == pytest.approx(0.10)

    def test_known_profile_loaded(self):
        w = _resolve_profile_weights({"ranking_profile": "talking_head"}, None)
        assert w["face_consistency"] == pytest.approx(
            PROFILE_WEIGHTS["talking_head"]["face_consistency"]
        )

    def test_unknown_profile_falls_back_to_defaults(self):
        w = _resolve_profile_weights({"ranking_profile": "nonexistent_profile"}, None)
        assert w == pytest.approx({
            k: v for k, v in {
                "raw_quality": 0.20, "motion_continuity": 0.20,
                "semantic_fit": 0.20, "aesthetic_quality": 0.15,
                "visual_intensity": 0.10, "face_consistency": 0.10,
                "arc_fit": 0.05,
            }.items()
        })

    def test_override_merged_over_profile(self):
        w = _resolve_profile_weights(
            {"ranking_profile": "travel_reel"},
            {"face_consistency": 0.30},
        )
        # face_consistency should reflect override (after renormalisation it > travel default)
        assert w["face_consistency"] > PROFILE_WEIGHTS["travel_reel"]["face_consistency"]

    def test_override_result_still_sums_to_1(self):
        w = _resolve_profile_weights(
            {"ranking_profile": "vlog"},
            {"arc_fit": 0.20},
        )
        assert sum(w.values()) == pytest.approx(1.0, abs=1e-9)

    def test_empty_override_uses_profile(self):
        w = _resolve_profile_weights({"ranking_profile": "music_video"}, None)
        assert w["arc_fit"] == pytest.approx(PROFILE_WEIGHTS["music_video"]["arc_fit"])


# ── _detect_subject_continuity ────────────────────────────────────────────────

class TestDetectSubjectContinuity:
    def test_talking_head_is_true(self):
        assert _detect_subject_continuity({}, "talking_head") is True

    def test_vlog_is_true(self):
        assert _detect_subject_continuity({}, "vlog") is True

    def test_travel_reel_is_false(self):
        assert _detect_subject_continuity({}, "travel_reel") is False

    def test_music_video_is_false(self):
        assert _detect_subject_continuity({}, "music_video") is False

    def test_fashion_montage_is_false(self):
        assert _detect_subject_continuity({}, "fashion_montage") is False

    def test_no_profile_defaults_true(self):
        assert _detect_subject_continuity({}, None) is True

    def test_faces_central_flag_overrides_non_face_profile(self):
        # Explicit fingerprint signal beats profile classification
        assert _detect_subject_continuity(
            {"faces_central": True}, "travel_reel"
        ) is True

    def test_subject_focus_person_is_true(self):
        assert _detect_subject_continuity({"subject_focus": "person"}, None) is True

    def test_content_type_interview_is_true(self):
        assert _detect_subject_continuity({"content_type": "interview"}, None) is True

    def test_primary_subject_talent_is_true(self):
        assert _detect_subject_continuity({"primary_subject": "talent"}, None) is True


# ── ClipRanker: profile-aware weights ────────────────────────────────────────

class TestClipRankerProfileWeights:
    def test_from_fingerprint_loads_profile_weights(self):
        fp     = {**FINGERPRINT, "ranking_profile": "talking_head"}
        ranker = ClipRanker.from_fingerprint(fp)
        assert ranker.weights["face_consistency"] == pytest.approx(
            PROFILE_WEIGHTS["talking_head"]["face_consistency"]
        )

    def test_from_fingerprint_no_profile_uses_defaults(self):
        ranker = ClipRanker.from_fingerprint(FINGERPRINT)
        assert ranker.weights["face_consistency"] == pytest.approx(0.10)

    def test_explicit_weights_override_profile(self):
        fp     = {**FINGERPRINT, "ranking_profile": "travel_reel"}
        ranker = ClipRanker.from_fingerprint(fp, weights={"face_consistency": 0.30})
        assert ranker.weights["face_consistency"] > (
            PROFILE_WEIGHTS["travel_reel"]["face_consistency"]
        )

    def test_music_video_arc_weight_reflected(self):
        fp     = {**FINGERPRINT, "ranking_profile": "music_video"}
        ranker = ClipRanker.from_fingerprint(fp)
        assert ranker.weights["arc_fit"] == pytest.approx(
            PROFILE_WEIGHTS["music_video"]["arc_fit"]
        )


# ── _face_consistency: subject-continuity scaling ────────────────────────────

class TestFaceConsistencySubjectContinuity:
    """Verify that face_consistency scores are compressed for non-face profiles."""

    def _ranker(self, profile: str | None) -> ClipRanker:
        fp = {**FINGERPRINT}
        if profile:
            fp["ranking_profile"] = profile
        return ClipRanker.from_fingerprint(fp)

    # --- subject-continuity profiles: raw scores preserved ---

    def test_talking_head_mismatch_is_0_4(self):
        ranker   = self._ranker("talking_head")
        face_seg = {**SEG_MOTION, "face_present": True,  "face_hash": 42}
        no_face  = {**SEG_SHARP,  "face_present": False}
        assert ranker._face_consistency(no_face, face_seg) == pytest.approx(0.4)

    def test_talking_head_same_hash_is_1_0(self):
        ranker = self._ranker("talking_head")
        prev   = {**SEG_MOTION, "face_present": True, "face_hash": 42}
        curr   = {**SEG_MOTION, "face_present": True, "face_hash": 42}
        assert ranker._face_consistency(curr, prev) == pytest.approx(1.0)

    def test_vlog_both_no_face_is_0_8(self):
        ranker = self._ranker("vlog")
        no_face = {**SEG_SHARP, "face_present": False}
        assert ranker._face_consistency(no_face, no_face) == pytest.approx(0.8)

    # --- non-face profiles: scores compressed toward neutral ---

    def test_travel_reel_mismatch_compressed(self):
        ranker   = self._ranker("travel_reel")
        face_seg = {**SEG_MOTION, "face_present": True,  "face_hash": 42}
        no_face  = {**SEG_SHARP,  "face_present": False}
        score    = ranker._face_consistency(no_face, face_seg)
        # raw=0.4 → compressed to 0.5 + (0.4-0.5)*0.4 = 0.46
        assert score == pytest.approx(0.46, abs=1e-4)
        # Must be softer than the raw score
        assert score > 0.4

    def test_music_video_mismatch_compressed(self):
        ranker   = self._ranker("music_video")
        face_seg = {**SEG_MOTION, "face_present": True, "face_hash": 1}
        no_face  = {**SEG_SHARP,  "face_present": False}
        score    = ranker._face_consistency(no_face, face_seg)
        assert score > 0.4    # less harsh than full penalty

    def test_fashion_montage_same_hash_compressed(self):
        ranker = self._ranker("fashion_montage")
        prev   = {**SEG_MOTION, "face_present": True, "face_hash": 42}
        curr   = {**SEG_MOTION, "face_present": True, "face_hash": 42}
        score  = ranker._face_consistency(curr, prev)
        # raw=1.0 → 0.5 + (1.0-0.5)*0.4 = 0.7  (still the best possible)
        assert score == pytest.approx(0.7, abs=1e-4)

    def test_non_face_profile_compressed_range_is_narrow(self):
        ranker   = self._ranker("travel_reel")
        face_seg = {**SEG_MOTION, "face_present": True, "face_hash": 99}
        no_face  = {**SEG_SHARP,  "face_present": False}
        same_f   = {**SEG_MOTION, "face_present": True, "face_hash": 99}
        low  = ranker._face_consistency(no_face, face_seg)
        high = ranker._face_consistency(same_f, face_seg)
        # compressed range should be narrower than raw 0.4–1.0
        assert (high - low) < 0.6

    def test_no_prev_always_1_regardless_of_profile(self):
        for profile in ("travel_reel", "music_video", "fashion_montage", "talking_head"):
            ranker = self._ranker(profile)
            assert ranker._face_consistency(SEG_MOTION, None) == pytest.approx(1.0)

    def test_fingerprint_faces_central_keeps_raw_score(self):
        # Explicit fingerprint signal should override non-face profile default
        fp     = {**FINGERPRINT, "ranking_profile": "travel_reel", "faces_central": True}
        ranker = ClipRanker.from_fingerprint(fp)
        face   = {**SEG_MOTION, "face_present": True,  "face_hash": 42}
        no_f   = {**SEG_SHARP,  "face_present": False}
        assert ranker._face_consistency(no_f, face) == pytest.approx(0.4)


# ── Explainability ────────────────────────────────────────────────────────────

RANK_FINGERPRINT = {
    **FINGERPRINT,
    "ranking_profile": "travel_reel",
}

CANDIDATES = [SEG_SHARP, SEG_MOTION, SEG_SOFT]


class TestExplainability:
    """ClipRanker.rank() attaches _explainability to every ranked candidate."""

    @pytest.fixture
    def ranked(self):
        ranker = ClipRanker.from_fingerprint(RANK_FINGERPRINT)
        return ranker.rank(
            CANDIDATES,
            used_ids={},
            shot_dur=0.7,
            prev_segment=None,
            slot_index=0,
            total_slots=5,
        )

    def test_explain_key_present_in_all_results(self, ranked):
        for r in ranked:
            assert "_explainability" in r

    def test_score_breakdown_has_all_7_dimensions(self, ranked):
        expected = {
            "raw_quality", "motion_continuity", "semantic_fit",
            "aesthetic_quality", "visual_intensity", "face_consistency", "arc_fit",
        }
        for r in ranked:
            assert set(r["_explainability"]["score_breakdown"].keys()) == expected

    def test_weights_used_sum_to_1(self, ranked):
        for r in ranked:
            total = sum(r["_explainability"]["weights_used"].values())
            assert total == pytest.approx(1.0, abs=1e-6)

    def test_total_score_matches_clip_scores_composite(self, ranked):
        for r in ranked:
            assert r["_explainability"]["total_score"] == pytest.approx(
                r["_clip_scores"].composite, abs=1e-3
            )

    def test_profile_used_reflects_fingerprint(self, ranked):
        for r in ranked:
            assert r["_explainability"]["profile_used"] == "travel_reel"

    def test_profile_used_none_when_no_profile(self):
        ranker = ClipRanker.from_fingerprint(FINGERPRINT)  # no ranking_profile
        results = ranker.rank(
            CANDIDATES,
            used_ids={},
            shot_dur=0.7,
            prev_segment=None,
            slot_index=0,
            total_slots=5,
        )
        for r in results:
            assert r["_explainability"]["profile_used"] is None

    def test_subject_continuity_enabled_matches_profile(self, ranked):
        # travel_reel → subject_continuity should be False
        for r in ranked:
            assert r["_explainability"]["subject_continuity_enabled"] is False

    def test_subject_continuity_true_for_talking_head(self):
        fp = {**FINGERPRINT, "ranking_profile": "talking_head"}
        ranker = ClipRanker.from_fingerprint(fp)
        results = ranker.rank(
            CANDIDATES, used_ids={}, shot_dur=0.7,
            prev_segment=None, slot_index=0, total_slots=5,
        )
        for r in results:
            assert r["_explainability"]["subject_continuity_enabled"] is True

    def test_reason_is_non_empty_string(self, ranked):
        for r in ranked:
            assert isinstance(r["_explainability"]["reason"], str)
            assert len(r["_explainability"]["reason"]) > 0

    def test_reason_mentions_top_2_contributing_dimensions(self, ranked):
        """reason must contain the names of the two highest-contribution dimensions."""
        _labels = {
            "raw_quality": "raw quality",
            "motion_continuity": "motion continuity",
            "semantic_fit": "semantic fit",
            "aesthetic_quality": "aesthetic quality",
            "visual_intensity": "visual intensity",
            "face_consistency": "face consistency",
            "arc_fit": "arc fit",
        }
        for r in ranked:
            explain = r["_explainability"]
            bd = explain["score_breakdown"]
            top2_dims = sorted(bd, key=lambda d: bd[d]["contribution"], reverse=True)[:2]
            reason = explain["reason"]
            for dim in top2_dims:
                assert _labels[dim] in reason, (
                    f"Expected '{_labels[dim]}' in reason '{reason}' for dim '{dim}'"
                )

    def test_score_breakdown_entries_have_score_weight_contribution(self, ranked):
        for r in ranked:
            for dim, entry in r["_explainability"]["score_breakdown"].items():
                assert "score" in entry
                assert "weight" in entry
                assert "contribution" in entry
                assert 0.0 <= entry["score"] <= 1.0
                assert 0.0 <= entry["weight"] <= 1.0
                assert entry["contribution"] == pytest.approx(
                    entry["score"] * entry["weight"], abs=1e-4
                )

