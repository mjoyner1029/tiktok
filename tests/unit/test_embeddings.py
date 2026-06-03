"""Unit tests for app/services/embeddings.py and ClipRanker embedding integration.

All tests are designed to run in CI without any CLIP model download.
CLIP-dependent code paths are exercised by patching ``CLIP_AVAILABLE``,
``embed_image_path``, and ``_extract_frame`` so the real model is never loaded.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

import app.services.embeddings as emb_mod
from app.services.embeddings import (
    CLIP_AVAILABLE,
    EMBEDDING_DIM,
    EmbeddingService,
    cosine_similarity,
    mean_embedding,
)
from app.services.clip_scorer import ClipRanker, ReferenceStyle


# ── helpers ───────────────────────────────────────────────────────────────────

def _unit_vec(dim: int = EMBEDDING_DIM, value: float = 1.0) -> list[float]:
    """Return a normalised vector pointing in the first dimension."""
    norm = math.sqrt(dim) * value / dim
    # Simple: first component = 1/sqrt(dim), rest = 0
    v = [0.0] * dim
    v[0] = 1.0
    return v


def _uniform_vec(dim: int = EMBEDDING_DIM, fill: float = 1.0) -> list[float]:
    """Return a uniform vector normalised to unit length."""
    mag = math.sqrt(dim) * abs(fill)
    return [fill / mag for _ in range(dim)]


# ═══════════════════════════════════════════════════════════════════════════
#  cosine_similarity
# ═══════════════════════════════════════════════════════════════════════════

class TestCosineSimilarity:
    def test_identical_unit_vectors(self):
        v = _unit_vec()
        assert abs(cosine_similarity(v, v) - 1.0) < 1e-9

    def test_opposite_vectors(self):
        a = _unit_vec()
        b = [-x for x in a]
        assert abs(cosine_similarity(a, b) - 0.0) < 1e-9

    def test_orthogonal_vectors_returns_half(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(cosine_similarity(a, b) - 0.5) < 1e-9

    def test_empty_list_returns_neutral(self):
        assert cosine_similarity([], []) == 0.5

    def test_length_mismatch_returns_neutral(self):
        assert cosine_similarity([1.0, 0.0], [0.5]) == 0.5

    def test_output_in_zero_one_range(self):
        import random
        rng = random.Random(42)
        for _ in range(20):
            a = [rng.gauss(0, 1) for _ in range(16)]
            b = [rng.gauss(0, 1) for _ in range(16)]
            # Normalize
            na = sum(x**2 for x in a) ** 0.5 or 1.0
            nb = sum(x**2 for x in b) ** 0.5 or 1.0
            a = [x / na for x in a]
            b = [x / nb for x in b]
            s = cosine_similarity(a, b)
            assert 0.0 <= s <= 1.0, f"Out of range: {s}"

    def test_parallel_vectors_returns_one(self):
        a = _uniform_vec(dim=8)
        b = _uniform_vec(dim=8)
        assert abs(cosine_similarity(a, b) - 1.0) < 1e-6


# ═══════════════════════════════════════════════════════════════════════════
#  mean_embedding
# ═══════════════════════════════════════════════════════════════════════════

class TestMeanEmbedding:
    def test_empty_returns_none(self):
        assert mean_embedding([]) is None

    def test_single_embedding_unchanged(self):
        v = _unit_vec(dim=4)
        result = mean_embedding([v])
        # Should still be unit-norm
        norm = sum(x**2 for x in result) ** 0.5
        assert abs(norm - 1.0) < 1e-6

    def test_two_identical_embeddings(self):
        v = _unit_vec(dim=4)
        result = mean_embedding([v, v])
        # Mean of two identical unit vectors = same unit vector
        for a, b in zip(v, result):
            assert abs(a - b) < 1e-6

    def test_result_is_unit_norm(self):
        vecs = [_unit_vec(dim=8), _uniform_vec(dim=8)]
        result = mean_embedding(vecs)
        norm = sum(x**2 for x in result) ** 0.5
        assert abs(norm - 1.0) < 1e-6

    def test_opposite_vectors_cancellation(self):
        a = [1.0, 0.0, 0.0, 0.0]
        b = [-1.0, 0.0, 0.0, 0.0]
        # Mean is zero vector — function should handle gracefully
        result = mean_embedding([a, b])
        # Either None or a valid list
        if result is not None:
            assert len(result) == 4


# ═══════════════════════════════════════════════════════════════════════════
#  EmbeddingService — CLIP unavailable (no-op path)
# ═══════════════════════════════════════════════════════════════════════════

class TestEmbeddingServiceUnavailable:
    @pytest.fixture
    def svc(self, tmp_path):
        with patch.object(emb_mod, "CLIP_AVAILABLE", False):
            yield EmbeddingService("proj_x", storage_root=str(tmp_path))

    def test_available_property_false(self, svc):
        with patch.object(emb_mod, "CLIP_AVAILABLE", False):
            assert svc.available is False

    def test_embed_frames_returns_none(self, svc, tmp_path):
        with patch.object(emb_mod, "CLIP_AVAILABLE", False):
            result = svc.embed_frames(["nonexistent.jpg"])
        assert result is None

    def test_embed_reference_video_returns_none(self, svc):
        with patch.object(emb_mod, "CLIP_AVAILABLE", False):
            result = svc.embed_reference_video("nonexistent.mp4")
        assert result is None

    def test_embed_segment_returns_none(self, svc):
        with patch.object(emb_mod, "CLIP_AVAILABLE", False):
            result = svc.embed_segment("a1", "v.mp4", 0.0, 5.0)
        assert result is None

    def test_enrich_is_noop(self, svc):
        footage = [{"asset_id": "a", "moments": [{"start": 0, "end": 5}]}]
        with patch.object(emb_mod, "CLIP_AVAILABLE", False):
            out = svc.enrich_footage_index(footage)
        assert out is footage
        assert "_embedding" not in footage[0]["moments"][0]


# ═══════════════════════════════════════════════════════════════════════════
#  EmbeddingService — CLIP available (mocked model)
# ═══════════════════════════════════════════════════════════════════════════

_FAKE_EMB: list[float] = _unit_vec(EMBEDDING_DIM)


@pytest.fixture
def svc_with_clip(tmp_path):
    """EmbeddingService with CLIP_AVAILABLE=True and a mocked embed function."""
    with patch.object(emb_mod, "CLIP_AVAILABLE", True):
        yield EmbeddingService("proj_test", storage_root=str(tmp_path)), tmp_path


class TestEmbedFrames:
    def test_returns_none_for_no_valid_frames(self, tmp_path):
        with patch.object(emb_mod, "CLIP_AVAILABLE", True):
            svc = EmbeddingService("p1", str(tmp_path))
            result = svc.embed_frames(["/nonexistent/frame.jpg"])
        assert result is None

    def test_calls_embed_per_existing_frame(self, tmp_path):
        # Create dummy image files
        frames = []
        for i in range(3):
            p = tmp_path / f"frame_{i}.jpg"
            p.write_bytes(b"fake")
            frames.append(str(p))

        fake_emb = _unit_vec(EMBEDDING_DIM)
        with patch.object(emb_mod, "CLIP_AVAILABLE", True), \
             patch.object(emb_mod, "embed_image_path", return_value=fake_emb) as mock_embed:
            svc = EmbeddingService("p1", str(tmp_path))
            result = svc.embed_frames(frames, cache_key="test_key")

        assert mock_embed.call_count == 3
        assert result is not None
        assert len(result) == EMBEDDING_DIM

    def test_result_written_to_cache(self, tmp_path):
        p = tmp_path / "f.jpg"
        p.write_bytes(b"x")
        with patch.object(emb_mod, "CLIP_AVAILABLE", True), \
             patch.object(emb_mod, "embed_image_path", return_value=_FAKE_EMB):
            svc = EmbeddingService("p2", str(tmp_path))
            svc.embed_frames([str(p)], cache_key="ck1")

        cache_file = tmp_path / "projects" / "p2" / "embeddings" / "ck1.json"
        assert cache_file.exists()
        stored = json.loads(cache_file.read_text())
        assert len(stored) == EMBEDDING_DIM

    def test_second_call_reads_cache_not_model(self, tmp_path):
        p = tmp_path / "f.jpg"
        p.write_bytes(b"x")
        with patch.object(emb_mod, "CLIP_AVAILABLE", True), \
             patch.object(emb_mod, "embed_image_path", return_value=_FAKE_EMB) as mock_embed:
            svc = EmbeddingService("p3", str(tmp_path))
            svc.embed_frames([str(p)], cache_key="ck2")   # first call — model used
            result2 = svc.embed_frames([str(p)], cache_key="ck2")  # second — cache

        assert mock_embed.call_count == 1   # model only called once
        assert result2 is not None

    def test_existing_cache_returned_immediately(self, tmp_path):
        # Pre-populate cache manually
        cache_dir = tmp_path / "projects" / "p4" / "embeddings"
        cache_dir.mkdir(parents=True)
        (cache_dir / "my_key.json").write_text(json.dumps(_FAKE_EMB))

        with patch.object(emb_mod, "CLIP_AVAILABLE", True), \
             patch.object(emb_mod, "embed_image_path") as mock_embed:
            svc = EmbeddingService("p4", str(tmp_path))
            result = svc.embed_frames([], cache_key="my_key")

        mock_embed.assert_not_called()
        assert result == _FAKE_EMB


class TestEmbedSegment:
    def test_returns_none_for_missing_video(self, tmp_path):
        with patch.object(emb_mod, "CLIP_AVAILABLE", True):
            svc = EmbeddingService("p5", str(tmp_path))
            result = svc.embed_segment("a1", "/no/such/video.mp4", 0.0, 5.0)
        assert result is None

    def test_extracts_midpoint_frame(self, tmp_path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake")

        frame = tmp_path / "frame.jpg"
        frame.write_bytes(b"fake")

        with patch.object(emb_mod, "CLIP_AVAILABLE", True), \
             patch.object(emb_mod, "_extract_frame", return_value=str(frame)) as mock_extract, \
             patch.object(emb_mod, "embed_image_path", return_value=_FAKE_EMB):
            svc = EmbeddingService("p6", str(tmp_path))
            svc.embed_segment("asset1", str(video), 2.0, 6.0)

        # Should have been called with the midpoint (2+6)/2 = 4.0
        mock_extract.assert_called_once_with(str(video), 4.0)

    def test_result_cached_to_disk(self, tmp_path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake")
        frame = tmp_path / "frame.jpg"
        frame.write_bytes(b"fake")

        with patch.object(emb_mod, "CLIP_AVAILABLE", True), \
             patch.object(emb_mod, "_extract_frame", return_value=str(frame)), \
             patch.object(emb_mod, "embed_image_path", return_value=_FAKE_EMB):
            svc = EmbeddingService("p7", str(tmp_path))
            svc.embed_segment("a1", str(video), 1.0, 3.0)

        cache_dir = tmp_path / "projects" / "p7" / "embeddings"
        assert any(f.suffix == ".json" for f in cache_dir.iterdir())

    def test_second_call_uses_cache(self, tmp_path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake")
        frame = tmp_path / "frame.jpg"
        frame.write_bytes(b"fake")

        with patch.object(emb_mod, "CLIP_AVAILABLE", True), \
             patch.object(emb_mod, "_extract_frame", return_value=str(frame)), \
             patch.object(emb_mod, "embed_image_path", return_value=_FAKE_EMB) as mock_emb:
            svc = EmbeddingService("p8", str(tmp_path))
            svc.embed_segment("a2", str(video), 0.0, 4.0)
            svc.embed_segment("a2", str(video), 0.0, 4.0)

        assert mock_emb.call_count == 1  # model only called once

    def test_returns_none_when_frame_extraction_fails(self, tmp_path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake")

        with patch.object(emb_mod, "CLIP_AVAILABLE", True), \
             patch.object(emb_mod, "_extract_frame", return_value=None):
            svc = EmbeddingService("p9", str(tmp_path))
            result = svc.embed_segment("a1", str(video), 0.0, 3.0)

        assert result is None


# ═══════════════════════════════════════════════════════════════════════════
#  enrich_footage_index
# ═══════════════════════════════════════════════════════════════════════════

class TestEnrichFootageIndex:
    def _make_footage(self, n_clips: int = 2, n_segs: int = 3) -> list[dict]:
        return [
            {
                "asset_id": f"clip_{i}",
                "file_path": f"/videos/clip_{i}.mp4",
                "usable_segments": [
                    {"start": float(j), "end": float(j + 1)}
                    for j in range(n_segs)
                ],
            }
            for i in range(n_clips)
        ]

    def test_injects_embedding_into_segments(self, tmp_path):
        footage = self._make_footage(n_clips=1, n_segs=2)
        video = Path(footage[0]["file_path"])

        frame = tmp_path / "f.jpg"
        frame.write_bytes(b"x")

        with patch.object(emb_mod, "CLIP_AVAILABLE", True), \
             patch("pathlib.Path.exists", return_value=True), \
             patch.object(emb_mod, "_extract_frame", return_value=str(frame)), \
             patch.object(emb_mod, "embed_image_path", return_value=_FAKE_EMB):
            svc = EmbeddingService("pe", str(tmp_path))
            svc.enrich_footage_index(footage)

        for seg in footage[0]["usable_segments"]:
            assert "_embedding" in seg

    def test_returns_same_list_object(self, tmp_path):
        footage = self._make_footage(n_clips=1, n_segs=1)
        with patch.object(emb_mod, "CLIP_AVAILABLE", False):
            svc = EmbeddingService("pf", str(tmp_path))
            result = svc.enrich_footage_index(footage)
        assert result is footage

    def test_segments_without_embed_left_unchanged(self, tmp_path):
        footage = self._make_footage(n_clips=1, n_segs=2)
        with patch.object(emb_mod, "CLIP_AVAILABLE", True), \
             patch("pathlib.Path.exists", return_value=False):
            svc = EmbeddingService("pg", str(tmp_path))
            svc.enrich_footage_index(footage)

        for seg in footage[0]["usable_segments"]:
            assert "_embedding" not in seg

    def test_moments_key_also_supported(self, tmp_path):
        footage = [{
            "asset_id": "m1",
            "file_path": "/v.mp4",
            "moments": [{"start": 0.0, "end": 2.0}],
        }]
        frame = tmp_path / "f.jpg"
        frame.write_bytes(b"x")

        with patch.object(emb_mod, "CLIP_AVAILABLE", True), \
             patch("pathlib.Path.exists", return_value=True), \
             patch.object(emb_mod, "_extract_frame", return_value=str(frame)), \
             patch.object(emb_mod, "embed_image_path", return_value=_FAKE_EMB):
            svc = EmbeddingService("ph", str(tmp_path))
            svc.enrich_footage_index(footage)

        assert "_embedding" in footage[0]["moments"][0]


# ═══════════════════════════════════════════════════════════════════════════
#  ClipRanker — embedding_similarity and _semantic_fit dispatch
# ═══════════════════════════════════════════════════════════════════════════

def _make_ranker(ref_embedding=None):
    fp = {"pace": "medium", "energy_level": "medium"}
    if ref_embedding is not None:
        fp["_ref_embedding"] = ref_embedding
    return ClipRanker.from_fingerprint(fp)


def _basic_seg(**extra) -> dict:
    return {
        "asset_id": "seg1",
        "start": 0.0,
        "end": 3.0,
        "score": 8.0,
        "sharpness": 8.0,
        "motion": 2.0,
        "tags": [],
        "face_present": False,
        "dominant_color": [128, 128, 128],
        "clip_quality": {},
        **extra,
    }


class TestClipRankerRefEmbedding:
    def test_from_fingerprint_reads_ref_embedding(self):
        ref_emb = _unit_vec(EMBEDDING_DIM)
        ranker = _make_ranker(ref_embedding=ref_emb)
        assert ranker._ref_embedding == ref_emb

    def test_from_fingerprint_none_when_absent(self):
        ranker = ClipRanker.from_fingerprint({"pace": "medium"})
        assert ranker._ref_embedding is None

    def test_ref_embedding_settable_directly(self):
        ranker = ClipRanker.from_fingerprint({})
        ref_emb = _unit_vec(EMBEDDING_DIM)
        ranker._ref_embedding = ref_emb
        assert ranker._ref_embedding == ref_emb


class TestEmbeddingSimilarity:
    def test_returns_neutral_when_no_ref_embedding(self):
        ranker = _make_ranker()
        seg = _basic_seg(_embedding=_unit_vec(EMBEDDING_DIM))
        assert ranker.embedding_similarity(seg) == 0.5

    def test_returns_neutral_when_no_seg_embedding(self):
        ranker = _make_ranker(ref_embedding=_unit_vec(EMBEDDING_DIM))
        assert ranker.embedding_similarity(_basic_seg()) == 0.5

    def test_identical_embeddings_return_one(self):
        emb = _unit_vec(EMBEDDING_DIM)
        ranker = _make_ranker(ref_embedding=emb)
        seg = _basic_seg(_embedding=emb)
        score = ranker.embedding_similarity(seg)
        assert abs(score - 1.0) < 1e-6

    def test_opposite_embeddings_return_zero(self):
        emb = _unit_vec(EMBEDDING_DIM)
        anti = [-x for x in emb]
        ranker = _make_ranker(ref_embedding=emb)
        seg = _basic_seg(_embedding=anti)
        score = ranker.embedding_similarity(seg)
        assert abs(score - 0.0) < 1e-6

    def test_score_in_zero_one_range(self):
        import random
        rng = random.Random(7)
        ref = [rng.gauss(0, 1) for _ in range(EMBEDDING_DIM)]
        norm = sum(x**2 for x in ref) ** 0.5
        ref = [x / norm for x in ref]
        seg_emb = [rng.gauss(0, 1) for _ in range(EMBEDDING_DIM)]
        norm = sum(x**2 for x in seg_emb) ** 0.5
        seg_emb = [x / norm for x in seg_emb]

        ranker = _make_ranker(ref_embedding=ref)
        seg = _basic_seg(_embedding=seg_emb)
        score = ranker.embedding_similarity(seg)
        assert 0.0 <= score <= 1.0


class TestSemanticFitDispatch:
    def test_uses_proxy_when_clip_unavailable(self):
        import app.services.clip_scorer as cs_mod
        ranker = _make_ranker(ref_embedding=_unit_vec(EMBEDDING_DIM))
        seg = _basic_seg(_embedding=_unit_vec(EMBEDDING_DIM))

        with patch.object(cs_mod, "_CLIP_AVAILABLE", False):
            proxy_score = ranker._semantic_fit(seg)

        # When CLIP unavailable, must use proxy (won't be 0.5 neutral for a well-
        # crafted segment, but should be a valid float in [0, 1])
        assert 0.0 <= proxy_score <= 1.0

    def test_uses_embedding_when_clip_available_and_embeddings_present(self):
        import app.services.clip_scorer as cs_mod
        ref_emb = _unit_vec(EMBEDDING_DIM)
        ranker = _make_ranker(ref_embedding=ref_emb)
        seg = _basic_seg(_embedding=ref_emb)  # identical → score ≈ 1.0

        with patch.object(cs_mod, "_CLIP_AVAILABLE", True):
            score = ranker._semantic_fit(seg)

        assert abs(score - 1.0) < 1e-6

    def test_falls_back_to_proxy_when_no_seg_embedding(self):
        import app.services.clip_scorer as cs_mod
        ref_emb = _unit_vec(EMBEDDING_DIM)
        ranker = _make_ranker(ref_embedding=ref_emb)
        seg = _basic_seg()  # no _embedding key

        with patch.object(cs_mod, "_CLIP_AVAILABLE", True):
            score = ranker._semantic_fit(seg)

        # Falls back to proxy — result is a valid float from the proxy formula
        assert 0.0 <= score <= 1.0

    def test_falls_back_to_proxy_when_no_ref_embedding(self):
        import app.services.clip_scorer as cs_mod
        ranker = _make_ranker()  # no ref embedding
        seg = _basic_seg(_embedding=_unit_vec(EMBEDDING_DIM))

        with patch.object(cs_mod, "_CLIP_AVAILABLE", True):
            score = ranker._semantic_fit(seg)

        assert 0.0 <= score <= 1.0

    def test_proxy_score_called_as_fallback(self):
        import app.services.clip_scorer as cs_mod
        ranker = _make_ranker()
        seg = _basic_seg()

        with patch.object(cs_mod, "_CLIP_AVAILABLE", False), \
             patch.object(ranker, "_semantic_proxy_score", return_value=0.77) as mock_proxy:
            result = ranker._semantic_fit(seg)

        mock_proxy.assert_called_once_with(seg)
        assert result == 0.77

    def test_embedding_similarity_called_when_available(self):
        import app.services.clip_scorer as cs_mod
        ref_emb = _unit_vec(EMBEDDING_DIM)
        ranker = _make_ranker(ref_embedding=ref_emb)
        seg = _basic_seg(_embedding=ref_emb)

        with patch.object(cs_mod, "_CLIP_AVAILABLE", True), \
             patch.object(ranker, "embedding_similarity", return_value=0.88) as mock_emb:
            result = ranker._semantic_fit(seg)

        mock_emb.assert_called_once_with(seg)
        assert result == 0.88


# ═══════════════════════════════════════════════════════════════════════════
#  Integration: rank() still works end-to-end with embedded segments
# ═══════════════════════════════════════════════════════════════════════════

class TestRankWithEmbeddings:
    """Verify that rank() integrates cleanly with segments that have _embedding."""

    def _make_candidates(self) -> list[dict]:
        base = {
            "score": 7.0, "sharpness": 7.0, "motion": 3.0,
            "tags": [], "face_present": False,
            "clip_quality": {}, "dominant_color": [100, 100, 100],
        }
        return [
            {**base, "asset_id": "a1", "start": 0.0, "end": 3.0,
             "_embedding": _unit_vec(EMBEDDING_DIM)},
            {**base, "asset_id": "a2", "start": 0.0, "end": 3.0,
             "_embedding": [-x for x in _unit_vec(EMBEDDING_DIM)]},  # opposite
        ]

    def test_rank_with_embeddings_returns_sorted_list(self):
        import app.services.clip_scorer as cs_mod
        ref_emb = _unit_vec(EMBEDDING_DIM)
        ranker = ClipRanker(
            ReferenceStyle(),
            ref_embedding=ref_emb,
        )

        candidates = self._make_candidates()
        with patch.object(cs_mod, "_CLIP_AVAILABLE", True):
            ranked = ranker.rank(
                candidates,
                used_ids=set(),
                shot_dur=2.0,
                prev_segment=None,
                slot_index=0,
                total_slots=4,
            )

        assert len(ranked) == 2
        # a1 (identical embedding) should rank above a2 (opposite)
        assert ranked[0]["asset_id"] == "a1"

    def test_rank_without_embeddings_still_works(self):
        """No regressions: rank() without any _embedding should behave as before."""
        ranker = ClipRanker(ReferenceStyle())
        candidates = [
            {"asset_id": "x1", "start": 0.0, "end": 3.0,
             "score": 8.0, "sharpness": 8.0, "motion": 2.0,
             "tags": [], "face_present": False, "clip_quality": {},
             "dominant_color": [128, 128, 128]},
        ]
        ranked = ranker.rank(
            candidates, used_ids=set(), shot_dur=2.0,
            prev_segment=None, slot_index=0, total_slots=3,
        )
        assert len(ranked) == 1
        assert "_clip_scores" in ranked[0]
        assert "_explainability" in ranked[0]
