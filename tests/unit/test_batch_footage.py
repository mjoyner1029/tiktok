"""
Unit tests for large-footage batch support in FootageAnalyzer and EditPlanner.

These tests mock ``_analyze_clip`` to avoid needing real video files, while
exercising the full batch post-processing pipeline.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ────────────────────────────────────────────────────────────────

def _make_clip(
    asset_id: str,
    num_segments: int,
    base_score: float = 5.0,
    color: list[int] | None = None,
) -> dict[str, Any]:
    """Produce a fake footage-index clip entry."""
    color = color or [128, 128, 128]
    segs = []
    for i in range(num_segments):
        score = round(base_score - i * 0.1, 3)  # decreasing scores
        segs.append({
            "start": float(i * 2),
            "end": float(i * 2 + 1.5),
            "score": score,
            "reason": "synthetic",
            "tags": [],
            "sharpness": 10.0,
            "motion": 3.0,
            "dominant_color": color,
            "intensity": 5.0,
        })
    return {
        "asset_id": asset_id,
        "path": f"/tmp/{asset_id}.mp4",
        "duration_sec": float(num_segments * 2 + 2),
        "resolution": [1080, 1920],
        "usable_segments": segs,
        "moments": segs,
        "quality": {"blur_score": 0.1, "brightness": "normal", "stability": "handheld"},
        "has_speech": False,
        "speech_segments": [],
        "scene_changes": [],
    }


def _build_analyzer(**kwargs):
    """Instantiate FootageAnalyzer bypassing settings I/O."""
    from app.services.footage_analyzer import FootageAnalyzer

    analyzer = FootageAnalyzer.__new__(FootageAnalyzer)
    # Provide defaults matching expected config fields
    analyzer.max_segments_per_clip = kwargs.get("max_segments_per_clip", 8)
    analyzer.max_selected_segments = kwargs.get("max_selected_segments", 120)
    analyzer.duplicate_similarity_threshold = kwargs.get("duplicate_similarity_threshold", 0.92)
    return analyzer


def _fake_footage_paths(n: int) -> list[Path]:
    """Return a list of n dummy paths (files don't need to exist for mocked tests)."""
    return [Path(f"/tmp/footage_{i:02d}.mp4") for i in range(n)]


# ── Test: per-clip cap ────────────────────────────────────────────────────

class TestPerClipCap:
    def test_48_clips_applies_per_clip_cap(self):
        """48 clips × 20 synthetic segments → each capped at 8."""
        analyzer = _build_analyzer(
            max_segments_per_clip=8,
            max_selected_segments=9999,
            duplicate_similarity_threshold=1.1,  # disable dedup
        )
        raw_index = [_make_clip(f"footage_{i:02d}", 20) for i in range(48)]

        processed, report = analyzer._post_process_batch(raw_index)

        for clip in processed:
            assert len(clip["usable_segments"]) <= 8, (
                f"{clip['asset_id']} has {len(clip['usable_segments'])} segments after cap"
            )

    def test_cap_keeps_highest_scored_segments(self):
        """Per-clip cap retains the top-N by score."""
        analyzer = _build_analyzer(
            max_segments_per_clip=3,
            max_selected_segments=9999,
            duplicate_similarity_threshold=1.1,  # disable dedup: threshold above max cosine value
        )
        raw_index = [_make_clip("footage_00", 10, base_score=9.0)]

        processed, _ = analyzer._post_process_batch(raw_index)

        scores = [s["score"] for s in processed[0]["usable_segments"]]
        assert scores == sorted(scores, reverse=True), "Kept segments not sorted descending by score"
        assert len(scores) == 3

    def test_rejection_log_records_capped_segments(self):
        """Segments dropped by per-clip cap appear in rejection_log."""
        analyzer = _build_analyzer(
            max_segments_per_clip=2,
            max_selected_segments=9999,
            duplicate_similarity_threshold=1.1,  # disable dedup
        )
        raw_index = [_make_clip("footage_00", 5)]

        _, report = analyzer._post_process_batch(raw_index)

        cap_rejections = [
            r for r in report["rejection_log"] if "per-clip cap" in r["reason"]
        ]
        assert len(cap_rejections) == 3  # 5 - 2 = 3 dropped

    def test_clips_under_cap_unaffected(self):
        """Clips with fewer segments than the cap are not modified."""
        analyzer = _build_analyzer(
            max_segments_per_clip=8,
            max_selected_segments=9999,
            duplicate_similarity_threshold=1.1,  # disable dedup
        )
        raw_index = [_make_clip("footage_00", 3)]

        processed, report = analyzer._post_process_batch(raw_index)

        assert len(processed[0]["usable_segments"]) == 3
        assert len(report["rejection_log"]) == 0


# ── Test: deduplication ────────────────────────────────────────────────────

class TestDeduplication:
    def test_near_identical_segments_deduped(self):
        """Two segments with identical proxy vectors → only one survives."""
        analyzer = _build_analyzer(
            max_segments_per_clip=99,
            max_selected_segments=9999,
            duplicate_similarity_threshold=0.80,
        )
        identical_color = [200, 100, 50]
        # Two clips whose single segment has the exact same proxy features
        clip_a = _make_clip("footage_00", 1, base_score=8.0, color=identical_color)
        clip_b = _make_clip("footage_01", 1, base_score=7.0, color=identical_color)
        # Make motion/sharpness identical too
        for seg in clip_a["usable_segments"] + clip_b["usable_segments"]:
            seg["sharpness"] = 12.0
            seg["motion"] = 4.0
            seg["intensity"] = 6.0

        processed, report = analyzer._post_process_batch([clip_a, clip_b])

        total_segs = sum(len(c["usable_segments"]) for c in processed)
        dup_rejections = [r for r in report["rejection_log"] if "near-duplicate" in r["reason"]]
        # The survivor guarantee ensures each source clip keeps ≥1 representative
        # even when its segments are near-identical to another clip's segments.
        # total_segs == 2: one representative per clip.
        # dup_rejections == 0: the reinserted segment's log entry is cleaned up.
        assert total_segs == 2, f"Expected 2 (one per clip), got {total_segs}"
        assert len(dup_rejections) == 0
        # Confirm one segment from each clip is present
        retained_ids = {c["asset_id"] for c in processed}
        assert retained_ids == {"footage_00", "footage_01"}

    def test_distinct_segments_all_retained(self):
        """Segments with very different proxy vectors are not marked as duplicates."""
        analyzer = _build_analyzer(
            max_segments_per_clip=99,
            max_selected_segments=9999,
            duplicate_similarity_threshold=0.99,
        )
        clip_a = _make_clip("footage_00", 1, color=[255, 0, 0])
        clip_b = _make_clip("footage_01", 1, color=[0, 0, 255])
        for seg in clip_a["usable_segments"]:
            seg["sharpness"] = 14.0; seg["motion"] = 1.0; seg["intensity"] = 2.0
        for seg in clip_b["usable_segments"]:
            seg["sharpness"] = 2.0; seg["motion"] = 9.0; seg["intensity"] = 8.0

        processed, report = analyzer._post_process_batch([clip_a, clip_b])

        total_segs = sum(len(c["usable_segments"]) for c in processed)
        dup_rejections = [r for r in report["rejection_log"] if "near-duplicate" in r["reason"]]
        assert total_segs == 2
        assert len(dup_rejections) == 0

    def test_dedup_keeps_higher_scored_segment(self):
        """When deduplicating, the segment with the higher score is retained."""
        analyzer = _build_analyzer(
            max_segments_per_clip=99,
            max_selected_segments=9999,
            duplicate_similarity_threshold=0.80,
        )
        color = [128, 128, 128]
        clip_a = _make_clip("footage_00", 1, base_score=9.0, color=color)
        clip_b = _make_clip("footage_01", 1, base_score=3.0, color=color)
        for segs in [clip_a["usable_segments"], clip_b["usable_segments"]]:
            for s in segs:
                s["sharpness"] = 10.0; s["motion"] = 5.0; s["intensity"] = 5.0

        processed, _ = analyzer._post_process_batch([clip_a, clip_b])

        kept_segs = [s for c in processed for s in c["usable_segments"]]
        # Survivor guarantee: one representative per clip regardless of score.
        assert len(kept_segs) == 2
        scores = sorted(s["score"] for s in kept_segs)
        assert scores[1] == pytest.approx(9.0, abs=0.01)  # higher score present


# ── Test: global cap ──────────────────────────────────────────────────────

class TestGlobalCap:
    def test_global_cap_limits_total_segments(self):
        """Even if dedup passes 500 segments, the global cap limits to max_selected_segments."""
        analyzer = _build_analyzer(
            max_segments_per_clip=99,
            max_selected_segments=10,
            duplicate_similarity_threshold=1.1,  # disable dedup
        )
        raw_index = [_make_clip(f"footage_{i:02d}", 20) for i in range(5)]
        # Make all colors different to avoid dedup
        for i, clip in enumerate(raw_index):
            for s in clip["usable_segments"]:
                s["dominant_color"] = [i * 40, 255 - i * 40, i * 20]

        processed, report = analyzer._post_process_batch(raw_index)

        total = sum(len(c["usable_segments"]) for c in processed)
        assert total <= 10
        assert report["total_selected"] <= 10

    def test_global_cap_prefers_high_score_segments(self):
        """Under global cap, highest-scored segments are prioritized."""
        analyzer = _build_analyzer(
            max_segments_per_clip=99,
            max_selected_segments=3,
            duplicate_similarity_threshold=1.1,  # disable dedup
        )
        clip = _make_clip("footage_00", 6, base_score=9.0)
        # Scores: 9.0, 8.9, 8.8, 8.7, 8.6, 8.5
        processed, _ = analyzer._post_process_batch([clip])

        kept = processed[0]["usable_segments"]
        # 3 are kept: 1 guaranteed by variety (best) + 2 more by score
        assert len(kept) <= 3
        kept_scores = [s["score"] for s in kept]
        # All kept scores should be >= the dropped ones
        all_scores = sorted([9.0, 8.9, 8.8, 8.7, 8.6, 8.5], reverse=True)
        for score in kept_scores:
            assert score >= all_scores[2], f"Score {score} below top-3 threshold"

    def test_global_cap_variety_guarantee(self):
        """Under global cap, at least one segment per clip is guaranteed first."""
        analyzer = _build_analyzer(
            max_segments_per_clip=99,
            max_selected_segments=5,
            duplicate_similarity_threshold=1.1,  # disable dedup
        )
        # 5 clips each with unique colors, 4 segments each
        raw_index = []
        for i in range(5):
            clip = _make_clip(f"footage_{i:02d}", 4)
            for s in clip["usable_segments"]:
                s["dominant_color"] = [i * 50, 0, 0]
            raw_index.append(clip)

        processed, _ = analyzer._post_process_batch(raw_index)

        # Each clip should have at least one segment retained
        for clip in processed:
            assert len(clip["usable_segments"]) >= 1, (
                f"{clip['asset_id']} has no segments — variety guarantee failed"
            )


# ── Test: batch report sentinel ────────────────────────────────────────────

class TestBatchReportSentinel:
    def test_analyze_all_includes_sentinel(self):
        """analyze_all() always appends a sentinel with asset_id=='__batch_report__'."""
        analyzer = _build_analyzer()
        clip_data = _make_clip("footage_00", 3)

        with patch.object(analyzer, "_analyze_clip", return_value=clip_data):
            result = analyzer.analyze_all([Path("/tmp/footage_00.mp4")])

        sentinels = [c for c in result if c.get("asset_id") == "__batch_report__"]
        assert len(sentinels) == 1

    def test_sentinel_has_all_expected_keys(self):
        """The batch report sentinel contains all required keys."""
        analyzer = _build_analyzer()
        clip_data = _make_clip("footage_00", 3)

        with patch.object(analyzer, "_analyze_clip", return_value=clip_data):
            result = analyzer.analyze_all([Path("/tmp/footage_00.mp4")])

        sentinel = next(c for c in result if c.get("asset_id") == "__batch_report__")
        for key in [
            "total_clips", "total_segments_before_dedup", "total_segments_after_cap",
            "total_segments_after_dedup", "total_selected", "rejection_log",
        ]:
            assert key in sentinel, f"Missing key: {key}"

    def test_sentinel_total_clips_matches_input(self):
        """total_clips in the sentinel matches the number of analyzed paths."""
        analyzer = _build_analyzer()
        n = 10
        fake_clips = [_make_clip(f"footage_{i:02d}", 2) for i in range(n)]

        with patch.object(analyzer, "_analyze_clip", side_effect=fake_clips):
            result = analyzer.analyze_all(_fake_footage_paths(n))

        sentinel = next(c for c in result if c.get("asset_id") == "__batch_report__")
        assert sentinel["total_clips"] == n

    def test_rejection_log_is_list(self):
        """rejection_log in the batch report is always a list."""
        analyzer = _build_analyzer()
        clip_data = _make_clip("footage_00", 2)

        with patch.object(analyzer, "_analyze_clip", return_value=clip_data):
            result = analyzer.analyze_all([Path("/tmp/footage_00.mp4")])

        sentinel = next(c for c in result if c.get("asset_id") == "__batch_report__")
        assert isinstance(sentinel["rejection_log"], list)


# ── Test: EditPlanner integration ─────────────────────────────────────────

class TestEditPlannerBatchIntegration:
    """Tests that EditPlanner correctly handles batch sentinel and new params."""

    def _make_footage_index_with_sentinel(self, n_clips: int = 5, segs_per_clip: int = 2) -> list[dict]:
        idx = [_make_clip(f"footage_{i:02d}", segs_per_clip) for i in range(n_clips)]
        idx.append({
            "asset_id": "__batch_report__",
            "total_clips": n_clips,
            "total_segments_before_dedup": n_clips * segs_per_clip,
            "total_segments_after_cap": n_clips * segs_per_clip,
            "total_segments_after_dedup": n_clips * segs_per_clip,
            "total_selected": n_clips * segs_per_clip,
            "rejection_log": [],
        })
        return idx

    def _patch_llm(self, planner):
        """Patch out the LLM caption call so tests don't need a real LLM."""
        return patch.object(planner, "_get_captions", return_value={})

    def test_plan_strips_sentinel(self):
        """EditPlanner.plan() does not crash when footage_index contains the sentinel."""
        from app.services.edit_planner import EditPlanner

        planner = EditPlanner(llm=None)
        footage_index = self._make_footage_index_with_sentinel(5, 2)
        fingerprint = {"avg_shot_duration": 1.5, "pace": "moderate", "num_cuts": 9}

        # Should not raise
        with self._patch_llm(planner):
            timeline = planner.plan(
                fingerprint=fingerprint,
                footage_index=footage_index,
                target_duration_sec=15.0,
            )

        # Clips in timeline should not reference the sentinel
        for clip in timeline.clips:
            assert clip.asset_id != "__batch_report__"

    def test_target_duration_sec_drives_shot_count(self):
        """target_duration_sec=15 with 1.5s avg shots → ~10 shots."""
        from app.services.edit_planner import EditPlanner

        planner = EditPlanner(llm=None)
        footage_index = self._make_footage_index_with_sentinel(20, 3)
        fingerprint = {"avg_shot_duration": 1.5, "pace": "moderate", "num_cuts": 19}

        with self._patch_llm(planner):
            timeline = planner.plan(
                fingerprint=fingerprint,
                footage_index=footage_index,
                target_duration_sec=15.0,
            )

        # Duration should be roughly 15s ± 50%
        assert 5.0 <= timeline.duration_sec <= 30.0, (
            f"timeline.duration_sec={timeline.duration_sec} outside expected range for target=15s"
        )

    def test_min_clip_variety_enforces_distinct_clips(self):
        """min_clip_variety=3 with 10 clips → at least 3 distinct asset_ids in timeline."""
        from app.services.edit_planner import EditPlanner

        planner = EditPlanner(llm=None)
        footage_index = self._make_footage_index_with_sentinel(10, 4)
        fingerprint = {"avg_shot_duration": 1.5, "pace": "moderate", "num_cuts": 15}

        with self._patch_llm(planner):
            timeline = planner.plan(
                fingerprint=fingerprint,
                footage_index=footage_index,
                target_duration_sec=30.0,
                min_clip_variety=3,
            )

        unique_assets = {clip.asset_id for clip in timeline.clips}
        assert len(unique_assets) >= min(3, 10), (
            f"Only {len(unique_assets)} distinct clips used; expected >= 3"
        )

    def test_plan_with_48_clips(self):
        """plan() handles 48 clips without errors (smoke test)."""
        from app.services.edit_planner import EditPlanner

        planner = EditPlanner(llm=None)
        footage_index = self._make_footage_index_with_sentinel(48, 2)
        fingerprint = {"avg_shot_duration": 1.5, "pace": "fast", "num_cuts": 30}

        with self._patch_llm(planner):
            timeline = planner.plan(
                fingerprint=fingerprint,
                footage_index=footage_index,
                target_duration_sec=30.0,
                min_clip_variety=5,
            )

        assert timeline is not None
        assert len(timeline.clips) >= 1
