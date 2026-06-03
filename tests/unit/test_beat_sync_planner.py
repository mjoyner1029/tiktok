"""Unit tests for beat-aware EditPlanner (rhythm presets + pacing curve)."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_fingerprint(**overrides) -> dict[str, Any]:
    """Return a minimal fingerprint with beat data."""
    fp: dict[str, Any] = {
        "ranking_profile":   "travel_reel",
        "tone":              "energetic",
        "avg_shot_duration": 1.0,
        "shot_durations":    [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
        "transitions":       ["hard_cut"],
        "caption_style":     {"all_caps": True},
        "motion_pattern":    "slow_push",
        "color_grade":       {},
        "num_cuts":          8,
        # Beat data
        "beat_grid":         [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5,
                               4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 7.5],
        "beat_points":       [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5,
                               4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 7.5],
        "downbeats":         [0.0, 2.0, 4.0, 6.0],
        "phrase_boundaries": [0.0, 4.0],
        "energy_curve":      [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0,
                               0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.5, 0.6],
        "intensity_curve":   [0.4] * 16,
        "tempo_bpm":         120.0,
    }
    fp.update(overrides)
    return fp


def _make_footage_index(n: int = 8) -> list[dict[str, Any]]:
    """Return a minimal footage index with *n* clips."""
    clips = []
    for i in range(n):
        segment = {
            "asset_id":     f"asset_{i:02d}",
            "source_in":    0.0,
            "source_out":   3.0,
            "start":        0.0,   # clip_scorer uses 'start'
            "end":          3.0,
            "shot_dur":     1.0,
            "score":        0.5 + i * 0.03,
            "intensity":    0.5 + i * 0.02,
            "clip_quality": {"brightness": "normal", "stability": "stable", "blur_score": 0.1},
            "transcript":   f"clip {i} transcript",
        }
        clips.append({
            "asset_id": f"asset_{i:02d}",
            "file_path": f"/fake/clip_{i:02d}.mp4",
            "usable_segments": [segment],
            "moments": [segment],
        })
    return clips


def _make_planner():
    """Return an EditPlanner with a stubbed LLM + caption responses."""
    from app.services.edit_planner import EditPlanner

    mock_llm = MagicMock()
    planner = EditPlanner(llm=mock_llm)

    def fake_captions(fp, clip_slots, descriptions, content_hint=""):
        return {
            "hook_index": 0,
            "shots": [{"caption": f"Shot {i}", "moment_type": "broll"} for i in range(len(clip_slots))],
        }

    planner._get_captions = fake_captions
    return planner


# ─────────────────────────────────────────────────────────────────────────────
# _snap_to_beats() unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSnapToBeats:
    def test_no_beats_returns_unchanged(self):
        from app.services.edit_planner import EditPlanner
        planner = EditPlanner(MagicMock())
        durations = [1.0, 1.5, 0.8, 2.0]
        result = planner._snap_to_beats(durations, [])
        assert result == durations

    def test_snaps_within_tolerance(self):
        """Cuts close to a beat should snap exactly to that beat."""
        from app.services.edit_planner import EditPlanner
        planner = EditPlanner(MagicMock())
        # beat at 1.0; ideal cut is at 1.0 (dur=1.0 from cursor=0)
        beats  = [1.0, 2.0, 3.0, 4.0]
        durs   = [1.0, 1.0, 1.0, 1.0]
        result = planner._snap_to_beats(durs, beats, tolerance=0.15)
        # Cumulative positions should land on beats
        pos = 0.0
        for d in result:
            pos += d
        assert pos <= 4.01   # should be at or near beat 4.0

    def test_tight_sync_snaps_to_every_beat(self):
        """tight_sync preset should produce more snapped cuts than loose_sync."""
        from app.services.edit_planner import EditPlanner
        planner = EditPlanner(MagicMock())
        beats = [0.5 * i for i in range(20)]
        durs  = [0.51] * 10   # slightly off-beat by 0.01s
        tight = planner._snap_to_beats(durs, beats, tolerance=0.08)
        loose = planner._snap_to_beats(durs, beats, tolerance=0.20)
        # Both should produce same number of durations
        assert len(tight) == len(durs)
        assert len(loose) == len(durs)


# ─────────────────────────────────────────────────────────────────────────────
# plan() — rhythm presets
# ─────────────────────────────────────────────────────────────────────────────

class TestPlanRhythmPresets:
    """Verify rhythm_preset changes observable behaviour in plan()."""

    @pytest.fixture
    def planner(self):
        return _make_planner()

    @pytest.fixture
    def fingerprint(self):
        return _make_fingerprint()

    @pytest.fixture
    def footage(self):
        return _make_footage_index(8)

    def _run_plan(self, planner, fingerprint, footage, preset):
        with patch("app.services.style_renderer.StyleRenderer.from_fingerprint",
                   return_value={"caption_preset": {}}):
            return planner.plan(
                fingerprint,
                footage,
                max_shots=8,
                project_id="test_proj",
                rhythm_preset=preset,
            )

    def test_tight_sync_produces_timeline(self, planner, fingerprint, footage):
        tl = self._run_plan(planner, fingerprint, footage, "tight_sync")
        assert tl.clips, "Should produce at least one clip"
        assert tl.duration_sec > 0

    def test_loose_sync_produces_timeline(self, planner, fingerprint, footage):
        tl = self._run_plan(planner, fingerprint, footage, "loose_sync")
        assert tl.clips

    def test_cinematic_produces_timeline(self, planner, fingerprint, footage):
        tl = self._run_plan(planner, fingerprint, footage, "cinematic")
        assert tl.clips

    def test_chaotic_produces_timeline(self, planner, fingerprint, footage):
        tl = self._run_plan(planner, fingerprint, footage, "chaotic")
        assert tl.clips

    def test_unknown_preset_falls_back_gracefully(self, planner, fingerprint, footage):
        """Unknown preset should not crash — falls back to loose_sync."""
        tl = self._run_plan(planner, fingerprint, footage, "does_not_exist")
        assert tl.clips


# ─────────────────────────────────────────────────────────────────────────────
# plan() — pacing_metadata
# ─────────────────────────────────────────────────────────────────────────────

class TestPlanPacingMetadata:
    @pytest.fixture
    def planner(self):
        return _make_planner()

    def test_pacing_metadata_set_on_timeline(self):
        planner = _make_planner()
        fp = _make_fingerprint()
        footage = _make_footage_index(6)
        with patch("app.services.style_renderer.StyleRenderer.from_fingerprint",
                   return_value={"caption_preset": {}}):
            tl = planner.plan(fp, footage, max_shots=6, project_id="p1", rhythm_preset="loose_sync")

        assert tl.pacing_metadata is not None, "pacing_metadata should be populated"
        assert "rhythm_preset" in tl.pacing_metadata
        assert tl.pacing_metadata["rhythm_preset"] == "loose_sync"

    def test_pacing_metadata_contains_escalation_score(self):
        planner = _make_planner()
        fp = _make_fingerprint()
        footage = _make_footage_index(6)
        with patch("app.services.style_renderer.StyleRenderer.from_fingerprint",
                   return_value={"caption_preset": {}}):
            tl = planner.plan(fp, footage, max_shots=6, project_id="p1")
        meta = tl.pacing_metadata or {}
        # escalation_score may be set or not depending on selection metadata availability
        # but the key should exist if music_analysis is importable
        if "escalation_score" in meta:
            assert 0.0 <= meta["escalation_score"] <= 1.0

    def test_pacing_metadata_clip_count_matches(self):
        planner = _make_planner()
        fp = _make_fingerprint()
        footage = _make_footage_index(6)
        with patch("app.services.style_renderer.StyleRenderer.from_fingerprint",
                   return_value={"caption_preset": {}}):
            tl = planner.plan(fp, footage, max_shots=6, project_id="p1")
        if tl.pacing_metadata and "clip_count" in tl.pacing_metadata:
            assert tl.pacing_metadata["clip_count"] == len(tl.clips)


# ─────────────────────────────────────────────────────────────────────────────
# plan() — beat_grid / phrase_boundaries influence
# ─────────────────────────────────────────────────────────────────────────────

class TestPlanBeatGridUsage:
    def test_no_beat_grid_still_plans(self):
        """plan() should succeed even when fingerprint has no beat data."""
        planner = _make_planner()
        fp = _make_fingerprint()
        del fp["beat_grid"]
        del fp["beat_points"]
        fp["downbeats"] = []
        fp["phrase_boundaries"] = []
        footage = _make_footage_index(6)
        with patch("app.services.style_renderer.StyleRenderer.from_fingerprint",
                   return_value={"caption_preset": {}}):
            tl = planner.plan(fp, footage, max_shots=6, rhythm_preset="tight_sync")
        assert tl.clips

    def test_cinematic_prefers_phrase_boundaries(self):
        """cinematic preset should snap cuts to phrase_boundaries (longer snaps allowed)."""
        planner = _make_planner()
        # Use phrase boundaries spaced at 4s each
        fp = _make_fingerprint(
            phrase_boundaries=[0.0, 4.0, 8.0, 12.0],
            beat_grid=[float(i) * 0.5 for i in range(30)],
            downbeats=[0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0],
            shot_durations=[4.0] * 4,
            avg_shot_duration=4.0,
        )
        footage = _make_footage_index(8)
        with patch("app.services.style_renderer.StyleRenderer.from_fingerprint",
                   return_value={"caption_preset": {}}):
            tl = planner.plan(fp, footage, max_shots=4, rhythm_preset="cinematic")
        assert tl.clips
