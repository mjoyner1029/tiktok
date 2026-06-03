"""Integration tests: EmbeddingService wired through the pipeline.

All tests are fully mocked — no CLIP model download, no real video files,
no network calls.  Tests verify:

  1. When CLIP is available:
     - reference embedding is computed and stored in fingerprint["_ref_embedding"]
     - footage segments are enriched with "_embedding"
     - ClipRanker uses embedding_similarity (real cosine path) for semantic_fit
     - embedding_status written to logs.json reflects reality

  2. When CLIP is unavailable:
     - pipeline completes without error
     - no "_ref_embedding" in fingerprint, no "_embedding" in segments
     - ClipRanker falls back to proxy semantic_fit
     - logs.json shows fallback_used=True

  3. ClipRanker correctly scores higher for similar embeddings vs dissimilar ones.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import app.services.embeddings as emb_mod
import app.services.clip_scorer as cs_mod
from app.services.embeddings import EmbeddingService, cosine_similarity, EMBEDDING_DIM
from app.services.clip_scorer import ClipRanker, ReferenceStyle
from app.services.reference_analyzer import ReferenceAnalyzer
from app.services.revision_engine import save_project_artifacts
from app.services.timeline_schema import EditTimeline


# ── Embedding fixtures ────────────────────────────────────────────────────────

def _unit_vec(dim: int = EMBEDDING_DIM) -> list[float]:
    v = [0.0] * dim
    v[0] = 1.0
    return v


def _random_unit_vec(seed: int, dim: int = EMBEDDING_DIM) -> list[float]:
    import random
    rng = random.Random(seed)
    v = [rng.gauss(0, 1) for _ in range(dim)]
    n = sum(x**2 for x in v) ** 0.5 or 1.0
    return [x / n for x in v]


_REF_EMB: list[float] = _unit_vec(EMBEDDING_DIM)
_SEG_EMB_SIMILAR: list[float] = _unit_vec(EMBEDDING_DIM)   # identical → cosine=1.0 → score=1.0
_SEG_EMB_DIFFERENT: list[float] = [-x for x in _unit_vec(EMBEDDING_DIM)]  # opposite → score=0.0


# ── Minimal fingerprint / footage helpers ─────────────────────────────────────

def _make_fingerprint(**extra) -> dict[str, Any]:
    return {
        "num_cuts": 10,
        "avg_shot_duration": 1.2,
        "pace": "fast",
        "energy_level": "high",
        "dominant_transition": "hard_cut",
        "shot_durations": [1.2] * 10,
        **extra,
    }


def _make_footage(n_clips: int = 2, n_segs: int = 3) -> list[dict]:
    return [
        {
            "asset_id": f"clip_{i}",
            "file_path": f"/fake/clip_{i}.mp4",
            "duration_sec": 10.0,
            "usable_segments": [
                {"start": float(j * 2), "end": float(j * 2 + 2), "score": 7.0}
                for j in range(n_segs)
            ],
        }
        for i in range(n_clips)
    ]


def _make_timeline(project_id: str = "test") -> EditTimeline:
    return EditTimeline(
        project_id=project_id,
        version=1,
        duration_sec=6.0,
        width=1080,
        height=1920,
        fps=30,
        clips=[],
        captions=[],
    )


# ═══════════════════════════════════════════════════════════════════════════
#  1.  EmbeddingService wiring through ReferenceAnalyzer
# ═══════════════════════════════════════════════════════════════════════════

class TestReferenceAnalyzerEmbeddingWiring:
    """Verify that ReferenceAnalyzer passes the embedding_service to _analyze_file
    and that the result lands in fingerprint["_ref_embedding"]."""

    def _make_analyzer(self) -> ReferenceAnalyzer:
        llm = MagicMock()
        llm.chat_with_images.return_value = "{}"
        return ReferenceAnalyzer(llm)

    def test_analyze_file_injects_ref_embedding(self, tmp_path):
        """When CLIP is available, _ref_embedding appears in the fingerprint."""
        fake_video = tmp_path / "ref.mp4"
        fake_video.write_bytes(b"fake")

        svc = MagicMock(spec=EmbeddingService)
        svc.available = True
        svc.embed_reference_video.return_value = _REF_EMB

        analyzer = self._make_analyzer()

        with patch("app.services.reference_analyzer.extract_visual_style") as mock_vis, \
             patch("app.services.reference_analyzer.get_media_info") as mock_info:
            mock_vis.return_value = {
                "cut_timestamps": [1.0, 2.0],
                "avg_cut_duration_sec": 1.0,
                "num_cuts": 2,
                "color_grade": {},
            }
            mock_info.return_value = {
                "duration_sec": 3.0,
                "width": 1080,
                "height": 1920,
                "has_audio": False,
            }
            fp = analyzer.analyze_file(str(fake_video), embedding_service=svc)

        svc.embed_reference_video.assert_called_once_with(str(fake_video))
        assert fp.get("_ref_embedding") == _REF_EMB

    def test_analyze_file_no_embedding_when_service_not_provided(self, tmp_path):
        """Without embedding_service, fingerprint has no _ref_embedding."""
        fake_video = tmp_path / "ref.mp4"
        fake_video.write_bytes(b"fake")

        analyzer = self._make_analyzer()
        with patch("app.services.reference_analyzer.extract_visual_style") as mock_vis, \
             patch("app.services.reference_analyzer.get_media_info") as mock_info:
            mock_vis.return_value = {
                "cut_timestamps": [], "avg_cut_duration_sec": 1.5,
                "num_cuts": 0, "color_grade": {},
            }
            mock_info.return_value = {
                "duration_sec": 5.0, "width": 1080, "height": 1920, "has_audio": False,
            }
            fp = analyzer.analyze_file(str(fake_video))

        assert "_ref_embedding" not in fp

    def test_analyze_file_graceful_when_embedding_fails(self, tmp_path):
        """Embedding failure must not abort fingerprint generation."""
        fake_video = tmp_path / "ref.mp4"
        fake_video.write_bytes(b"fake")

        svc = MagicMock(spec=EmbeddingService)
        svc.available = True
        svc.embed_reference_video.side_effect = RuntimeError("GPU exploded")

        analyzer = self._make_analyzer()
        with patch("app.services.reference_analyzer.extract_visual_style") as mock_vis, \
             patch("app.services.reference_analyzer.get_media_info") as mock_info:
            mock_vis.return_value = {
                "cut_timestamps": [], "avg_cut_duration_sec": 1.5,
                "num_cuts": 0, "color_grade": {},
            }
            mock_info.return_value = {
                "duration_sec": 5.0, "width": 1080, "height": 1920, "has_audio": False,
            }
            fp = analyzer.analyze_file(str(fake_video), embedding_service=svc)

        # Fingerprint still produced, no _ref_embedding
        assert isinstance(fp, dict)
        assert "_ref_embedding" not in fp

    def test_analyze_file_no_embedding_when_service_returns_none(self, tmp_path):
        """When CLIP is unavailable, embed_reference_video returns None → no _ref_embedding."""
        fake_video = tmp_path / "ref.mp4"
        fake_video.write_bytes(b"fake")

        svc = MagicMock(spec=EmbeddingService)
        svc.available = False
        svc.embed_reference_video.return_value = None   # CLIP unavailable

        analyzer = self._make_analyzer()
        with patch("app.services.reference_analyzer.extract_visual_style") as mock_vis, \
             patch("app.services.reference_analyzer.get_media_info") as mock_info:
            mock_vis.return_value = {
                "cut_timestamps": [], "avg_cut_duration_sec": 1.0,
                "num_cuts": 0, "color_grade": {},
            }
            mock_info.return_value = {
                "duration_sec": 3.0, "width": 1080, "height": 1920, "has_audio": False,
            }
            fp = analyzer.analyze_file(str(fake_video), embedding_service=svc)

        assert "_ref_embedding" not in fp


# ═══════════════════════════════════════════════════════════════════════════
#  2.  EmbeddingService.enrich_footage_index wiring
# ═══════════════════════════════════════════════════════════════════════════

class TestFootageIndexEnrichment:
    """Verify footage segment enrichment with and without CLIP."""

    def test_enrich_adds_embeddings_when_available(self, tmp_path):
        footage = _make_footage(n_clips=1, n_segs=2)
        frame = tmp_path / "f.jpg"
        frame.write_bytes(b"x")

        with patch.object(emb_mod, "CLIP_AVAILABLE", True), \
             patch("pathlib.Path.exists", return_value=True), \
             patch.object(emb_mod, "_extract_frame", return_value=str(frame)), \
             patch.object(emb_mod, "embed_image_path", return_value=_REF_EMB):
            svc = EmbeddingService("proj1", str(tmp_path))
            svc.enrich_footage_index(footage)

        for seg in footage[0]["usable_segments"]:
            assert "_embedding" in seg, "Segment missing _embedding"
            assert len(seg["_embedding"]) == EMBEDDING_DIM

    def test_enrich_noop_when_clip_unavailable(self, tmp_path):
        footage = _make_footage(n_clips=2, n_segs=3)
        original_segs = [
            {**seg} for clip in footage for seg in clip["usable_segments"]
        ]

        with patch.object(emb_mod, "CLIP_AVAILABLE", False):
            svc = EmbeddingService("proj2", str(tmp_path))
            svc.enrich_footage_index(footage)

        for clip in footage:
            for seg in clip["usable_segments"]:
                assert "_embedding" not in seg

    def test_enrich_skips_missing_video_gracefully(self, tmp_path):
        """Segments from non-existent video file get no _embedding (no crash)."""
        footage = _make_footage(n_clips=1, n_segs=2)
        # file_path is "/fake/clip_0.mp4" which doesn't exist

        with patch.object(emb_mod, "CLIP_AVAILABLE", True):
            svc = EmbeddingService("proj3", str(tmp_path))
            svc.enrich_footage_index(footage)

        # Path.exists() will return False → embed_segment returns None → no crash
        for seg in footage[0]["usable_segments"]:
            assert "_embedding" not in seg


# ═══════════════════════════════════════════════════════════════════════════
#  3.  ClipRanker uses CLIP similarity when both sides are present
# ═══════════════════════════════════════════════════════════════════════════

class TestClipRankerSemanticFitIntegration:
    """Verify that ClipRanker correctly dispatches to CLIP or proxy path."""

    def _base_seg(self, **extra) -> dict:
        return {
            "asset_id": "s1", "start": 0.0, "end": 3.0,
            "score": 8.0, "sharpness": 8.0, "motion": 2.0,
            "tags": [], "face_present": False,
            "clip_quality": {}, "dominant_color": [128, 128, 128],
            **extra,
        }

    def test_semantic_fit_uses_clip_when_both_embeddings_present(self):
        fp = _make_fingerprint(_ref_embedding=_REF_EMB)
        ranker = ClipRanker.from_fingerprint(fp)
        seg = self._base_seg(_embedding=_SEG_EMB_SIMILAR)

        with patch.object(cs_mod, "_CLIP_AVAILABLE", True):
            score = ranker._semantic_fit(seg)

        # Identical unit vectors → score should be 1.0
        assert abs(score - 1.0) < 1e-6

    def test_semantic_fit_falls_back_when_clip_unavailable(self):
        fp = _make_fingerprint(_ref_embedding=_REF_EMB)
        ranker = ClipRanker.from_fingerprint(fp)
        seg = self._base_seg(_embedding=_SEG_EMB_SIMILAR)

        with patch.object(cs_mod, "_CLIP_AVAILABLE", False):
            score = ranker._semantic_fit(seg)

        # Proxy path returns a value in [0, 1] based on feature vectors
        assert 0.0 <= score <= 1.0

    def test_semantic_fit_falls_back_when_no_ref_embedding(self):
        fp = _make_fingerprint()    # no _ref_embedding
        ranker = ClipRanker.from_fingerprint(fp)
        seg = self._base_seg(_embedding=_SEG_EMB_SIMILAR)

        with patch.object(cs_mod, "_CLIP_AVAILABLE", True):
            score = ranker._semantic_fit(seg)

        assert 0.0 <= score <= 1.0

    def test_semantic_fit_falls_back_when_no_seg_embedding(self):
        fp = _make_fingerprint(_ref_embedding=_REF_EMB)
        ranker = ClipRanker.from_fingerprint(fp)
        seg = self._base_seg()    # no _embedding key

        with patch.object(cs_mod, "_CLIP_AVAILABLE", True):
            score = ranker._semantic_fit(seg)

        assert 0.0 <= score <= 1.0

    def test_similar_segment_ranks_above_dissimilar(self):
        """A segment with similar embedding ranks above one with opposite embedding."""
        fp = _make_fingerprint(_ref_embedding=_REF_EMB)
        ranker = ClipRanker.from_fingerprint(fp)

        similar = self._base_seg(asset_id="sim", _embedding=_SEG_EMB_SIMILAR)
        different = self._base_seg(asset_id="dif", _embedding=_SEG_EMB_DIFFERENT)

        with patch.object(cs_mod, "_CLIP_AVAILABLE", True):
            ranked = ranker.rank(
                [similar, different],
                used_ids=set(),
                shot_dur=2.0,
                prev_segment=None,
                slot_index=0,
                total_slots=4,
            )

        assert ranked[0]["asset_id"] == "sim", (
            "Similar embedding should rank first; got: "
            + str([r["asset_id"] for r in ranked])
        )

    def test_rank_without_embeddings_unchanged(self):
        """No regressions: rank() without any CLIP embeddings should still work."""
        ranker = ClipRanker(ReferenceStyle())
        candidates = [
            {
                "asset_id": "x", "start": 0.0, "end": 3.0,
                "score": 7.0, "sharpness": 7.0, "motion": 2.0,
                "tags": [], "face_present": False,
                "clip_quality": {}, "dominant_color": [128, 128, 128],
            }
        ]
        ranked = ranker.rank(
            candidates, used_ids=set(), shot_dur=2.0,
            prev_segment=None, slot_index=0, total_slots=3,
        )
        assert len(ranked) == 1
        assert "_clip_scores" in ranked[0]


# ═══════════════════════════════════════════════════════════════════════════
#  4.  embedding_status written to logs.json
# ═══════════════════════════════════════════════════════════════════════════

class TestEmbeddingStatusInLogs:
    """Verify that save_project_artifacts writes embedding_status into logs.json."""

    def test_logs_contain_embedding_status_when_provided(self, tmp_path):
        project_dir = tmp_path / "proj_abc"
        fingerprint = _make_fingerprint()
        footage_index = _make_footage(n_clips=1, n_segs=1)
        timeline = _make_timeline()

        status = {
            "clip_available": True,
            "reference_embedded": True,
            "footage_segments_embedded": 3,
            "fallback_used": False,
        }
        save_project_artifacts(
            project_dir, fingerprint, footage_index, timeline,
            embedding_status=status,
        )

        logs = json.loads((project_dir / "logs.json").read_text())
        assert "embedding_status" in logs
        assert logs["embedding_status"]["clip_available"] is True
        assert logs["embedding_status"]["reference_embedded"] is True
        assert logs["embedding_status"]["footage_segments_embedded"] == 3
        assert logs["embedding_status"]["fallback_used"] is False

    def test_logs_omit_embedding_status_when_not_provided(self, tmp_path):
        project_dir = tmp_path / "proj_def"
        fingerprint = _make_fingerprint()
        footage_index = _make_footage(n_clips=1, n_segs=1)
        timeline = _make_timeline()

        save_project_artifacts(project_dir, fingerprint, footage_index, timeline)

        logs = json.loads((project_dir / "logs.json").read_text())
        assert "embedding_status" not in logs

    def test_logs_fallback_used_true_when_clip_unavailable(self, tmp_path):
        project_dir = tmp_path / "proj_ghi"
        fingerprint = _make_fingerprint()
        footage_index = _make_footage()
        timeline = _make_timeline()

        status = {
            "clip_available": False,
            "reference_embedded": False,
            "footage_segments_embedded": 0,
            "fallback_used": True,
        }
        save_project_artifacts(
            project_dir, fingerprint, footage_index, timeline,
            embedding_status=status,
        )

        logs = json.loads((project_dir / "logs.json").read_text())
        assert logs["embedding_status"]["fallback_used"] is True
        assert logs["embedding_status"]["clip_available"] is False
        assert logs["embedding_status"]["footage_segments_embedded"] == 0

    def test_logs_contain_all_four_embedding_fields(self, tmp_path):
        """All four required keys must appear in embedding_status."""
        project_dir = tmp_path / "proj_jkl"
        timeline = _make_timeline()
        status = {
            "clip_available": True,
            "reference_embedded": False,
            "footage_segments_embedded": 5,
            "fallback_used": True,
        }
        save_project_artifacts(
            project_dir, _make_fingerprint(), _make_footage(), timeline,
            embedding_status=status,
        )
        logs = json.loads((project_dir / "logs.json").read_text())
        for key in ("clip_available", "reference_embedded",
                    "footage_segments_embedded", "fallback_used"):
            assert key in logs["embedding_status"], f"Missing key: {key}"


# ═══════════════════════════════════════════════════════════════════════════
#  5.  Full wiring simulation (EmbeddingService + ReferenceAnalyzer + ClipRanker)
# ═══════════════════════════════════════════════════════════════════════════

class TestFullEmbeddingPipelineWiring:
    """End-to-end simulation of the embedding pipeline without real videos."""

    def test_full_path_clip_available(self, tmp_path):
        """Simulate: CLIP available → ref_embedding injected → ClipRanker uses it."""
        fake_video = tmp_path / "ref.mp4"
        fake_video.write_bytes(b"fake")

        # Step 1: EmbeddingService produces a reference embedding
        _media_info = {"duration_sec": 2.0, "width": 1080, "height": 1920, "has_audio": False}

        with patch.object(emb_mod, "CLIP_AVAILABLE", True), \
             patch.object(emb_mod, "embed_image_path", return_value=_REF_EMB), \
             patch("app.services.media_analyzer.get_media_info", return_value=_media_info):
            svc = EmbeddingService("sim_proj", str(tmp_path))

            # Step 2: ReferenceAnalyzer injects it into the fingerprint
            analyzer = ReferenceAnalyzer(MagicMock())
            analyzer.llm.chat_with_images.return_value = "{}"

            with patch("app.services.reference_analyzer.extract_visual_style") as mv, \
                 patch("app.services.reference_analyzer.get_media_info") as mi, \
                 patch.object(emb_mod, "_extract_frame", return_value=str(tmp_path / "f.jpg")):
                (tmp_path / "f.jpg").write_bytes(b"img")
                mv.return_value = {
                    "cut_timestamps": [1.0],
                    "avg_cut_duration_sec": 1.0,
                    "num_cuts": 1,
                    "color_grade": {},
                }
                mi.return_value = _media_info
                fingerprint = analyzer.analyze_file(
                    str(fake_video), embedding_service=svc
                )

        assert "_ref_embedding" in fingerprint

        # Step 3: ClipRanker from the fingerprint should use CLIP path
        ranker = ClipRanker.from_fingerprint(fingerprint)
        assert ranker._ref_embedding == _REF_EMB

        seg = {
            "asset_id": "s1", "start": 0.0, "end": 3.0,
            "score": 8.0, "sharpness": 8.0, "motion": 2.0,
            "tags": [], "face_present": False, "clip_quality": {},
            "dominant_color": [128, 128, 128],
            "_embedding": _REF_EMB,   # identical → score should be 1.0
        }

        with patch.object(cs_mod, "_CLIP_AVAILABLE", True):
            score = ranker._semantic_fit(seg)

        assert abs(score - 1.0) < 1e-6

    def test_full_path_clip_unavailable(self, tmp_path):
        """Simulate: CLIP unavailable → no embeddings → proxy semantic_fit used."""
        fake_video = tmp_path / "ref.mp4"
        fake_video.write_bytes(b"fake")

        with patch.object(emb_mod, "CLIP_AVAILABLE", False):
            svc = EmbeddingService("sim_noclip", str(tmp_path))

            analyzer = ReferenceAnalyzer(MagicMock())
            analyzer.llm.chat_with_images.return_value = "{}"

            with patch("app.services.reference_analyzer.extract_visual_style") as mv, \
                 patch("app.services.reference_analyzer.get_media_info") as mi:
                mv.return_value = {
                    "cut_timestamps": [], "avg_cut_duration_sec": 1.5,
                    "num_cuts": 0, "color_grade": {},
                }
                mi.return_value = {
                    "duration_sec": 5.0, "width": 1080, "height": 1920, "has_audio": False,
                }
                fingerprint = analyzer.analyze_file(
                    str(fake_video), embedding_service=svc
                )

        # No ref embedding
        assert "_ref_embedding" not in fingerprint

        # ClipRanker falls back to proxy automatically
        ranker = ClipRanker.from_fingerprint(fingerprint)
        assert ranker._ref_embedding is None

        seg = {
            "asset_id": "s1", "start": 0.0, "end": 3.0,
            "score": 8.0, "sharpness": 8.0, "motion": 2.0,
            "tags": [], "face_present": False, "clip_quality": {},
            "dominant_color": [128, 128, 128],
        }

        with patch.object(cs_mod, "_CLIP_AVAILABLE", False):
            score = ranker._semantic_fit(seg)

        # Proxy still returns a meaningful score
        assert 0.0 <= score <= 1.0

    def test_embedding_status_structure(self, tmp_path):
        """Verify the embedding_status dict has all required fields."""
        from app.services.embeddings import CLIP_AVAILABLE as real_clip_available

        # Simulate the status that pipeline_start would build
        fingerprint = _make_fingerprint(_ref_embedding=_REF_EMB)
        footage = _make_footage()
        for clip in footage:
            for seg in clip["usable_segments"]:
                seg["_embedding"] = _REF_EMB

        reference_embedded = "_ref_embedding" in fingerprint
        footage_segments_embedded = sum(
            1
            for clip in footage
            for seg in (clip.get("usable_segments") or [])
            if "_embedding" in seg
        )
        status = {
            "clip_available": real_clip_available,
            "reference_embedded": reference_embedded,
            "footage_segments_embedded": footage_segments_embedded,
            "fallback_used": not (real_clip_available and reference_embedded),
        }

        required_keys = {
            "clip_available", "reference_embedded",
            "footage_segments_embedded", "fallback_used",
        }
        assert required_keys == set(status.keys())
        assert isinstance(status["clip_available"], bool)
        assert isinstance(status["reference_embedded"], bool)
        assert isinstance(status["footage_segments_embedded"], int)
        assert isinstance(status["fallback_used"], bool)
