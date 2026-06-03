"""
ClipScorer — multi-dimensional clip ranking for timeline assembly.

Scoring dimensions (all normalised to [0, 1]):
  1. raw_quality        sharpness + base quality score from FootageAnalyzer
  2. motion_continuity  Gaussian penalty for sudden motion-level jumps vs prev clip
  3. semantic_fit       CLIP-proxy cosine similarity to reference style
  4. aesthetic_quality  sharpness × exposure × stability × blur composite
  5. visual_intensity   energy level (motion magnitude + action tags)
  6. face_consistency   same-person continuity with the previous clip
  7. arc_fit            emotional-escalation position fit (hook→build→climax→closer)

The composite score is a weighted sum of all seven dimensions.  Default weights
sum to 1.0 and can be overridden via ``ClipRanker.__init__(weights=...)``.

Public API
----------
  ClipRanker.from_fingerprint(fingerprint)        → ClipRanker
  ClipRanker.rank(candidates, *, used_ids, ...)   → list[dict]   # sorted best→worst
  ClipScores                                       dataclass, one per candidate
  ReferenceStyle.from_fingerprint(fingerprint)    → ReferenceStyle

Explainability fields attached to every ranked candidate
---------------------------------------------------------
  _clip_scores          ClipScores dataclass (raw per-dimension floats)
  _explainability       dict with:
      total_score                 composite float
      profile_used                ranking_profile name or None
      weights_used                {dim: weight}
      score_breakdown             {dim: {score, weight, contribution}}
      subject_continuity_enabled  bool
      reason                      human-readable string (top-2 dimensions)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# Optional CLIP embedding support — graceful no-op when unavailable
try:
    from app.services.embeddings import CLIP_AVAILABLE as _CLIP_AVAILABLE
    from app.services.embeddings import cosine_similarity as _emb_cosine
except ImportError:  # pragma: no cover
    _CLIP_AVAILABLE: bool = False
    _emb_cosine = None  # type: ignore[assignment]

# ── Dimension display labels ─────────────────────────────────────────────────

_DIMENSION_LABELS: dict[str, str] = {
    "raw_quality":       "raw quality",
    "motion_continuity": "motion continuity",
    "semantic_fit":      "semantic fit",
    "aesthetic_quality": "aesthetic quality",
    "visual_intensity":  "visual intensity",
    "face_consistency":  "face consistency",
    "arc_fit":           "arc fit",
}

# ── Default scoring weights (must sum to 1.0) ─────────────────────────────────

_DEFAULT_WEIGHTS: dict[str, float] = {
    "raw_quality":       0.20,
    "motion_continuity": 0.20,
    "semantic_fit":      0.20,
    "aesthetic_quality": 0.15,
    "visual_intensity":  0.10,
    "face_consistency":  0.10,
    "arc_fit":           0.05,
}

# ── Style-profile weight presets ──────────────────────────────────────────────

#: Per-profile weight overrides.  All rows sum to 1.0.
#: Profiles not listed here fall back to ``_DEFAULT_WEIGHTS``.
PROFILE_WEIGHTS: dict[str, dict[str, float]] = {
    # Fast aesthetic cuts; visual quality + variety over continuity
    "fashion_montage": {
        "raw_quality":       0.20,
        "motion_continuity": 0.10,
        "semantic_fit":      0.20,
        "aesthetic_quality": 0.25,
        "visual_intensity":  0.10,
        "face_consistency":  0.10,
        "arc_fit":           0.05,
    },
    # Single speaker; face continuity & motion smoothness dominate
    "talking_head": {
        "raw_quality":       0.15,
        "motion_continuity": 0.20,
        "semantic_fit":      0.15,
        "aesthetic_quality": 0.15,
        "visual_intensity":  0.05,
        "face_consistency":  0.25,
        "arc_fit":           0.05,
    },
    # Scenic B-roll variety; arc + intensity matter; faces unimportant
    "travel_reel": {
        "raw_quality":       0.20,
        "motion_continuity": 0.15,
        "semantic_fit":      0.20,
        "aesthetic_quality": 0.15,
        "visual_intensity":  0.15,
        "face_consistency":  0.05,
        "arc_fit":           0.10,
    },
    # Crisp hero shots; raw quality + aesthetics paramount
    "product_showcase": {
        "raw_quality":       0.25,
        "motion_continuity": 0.15,
        "semantic_fit":      0.20,
        "aesthetic_quality": 0.25,
        "visual_intensity":  0.05,
        "face_consistency":  0.05,
        "arc_fit":           0.05,
    },
    # Beat-driven energy; intensity + arc shape + visual punch
    "music_video": {
        "raw_quality":       0.15,
        "motion_continuity": 0.15,
        "semantic_fit":      0.15,
        "aesthetic_quality": 0.15,
        "visual_intensity":  0.20,
        "face_consistency":  0.05,
        "arc_fit":           0.15,
    },
    # Personal presenter; face continuity + smooth flow
    "vlog": {
        "raw_quality":       0.15,
        "motion_continuity": 0.20,
        "semantic_fit":      0.15,
        "aesthetic_quality": 0.15,
        "visual_intensity":  0.10,
        "face_consistency":  0.20,
        "arc_fit":           0.05,
    },
}

# Profiles where consistent subject/face tracking is important
_SUBJECT_CONTINUITY_PROFILES: frozenset[str] = frozenset({"talking_head", "vlog"})

# Profiles where face presence is incidental — continuity penalties should be soft
_NON_FACE_PROFILES: frozenset[str] = frozenset({
    "fashion_montage", "travel_reel", "music_video", "product_showcase",
})

# Fingerprint keys (and accepted values) that signal face/person centrality
_FACE_CENTRAL_SIGNALS: dict[str, frozenset[str]] = {
    "subject_focus":   frozenset({"person", "people", "face", "presenter"}),
    "primary_subject": frozenset({"person", "people", "presenter", "talent"}),
    "content_type":    frozenset({"interview", "talking_head", "tutorial", "vlog"}),
}

# ── Reference style helpers ───────────────────────────────────────────────────

_PACE_INTENSITY: dict[str, float] = {
    "slow":       0.20,
    "medium":     0.40,
    "fast":       0.60,
    "ultra_fast": 0.80,
}

_ENERGY_INTENSITY: dict[str, float] = {
    "low":    0.20,
    "medium": 0.45,
    "high":   0.70,
}

_STABILITY_SCORE: dict[str, float] = {
    "stable":          1.00,
    "slight_movement": 0.85,
    "handheld":        0.60,
    "shaky":           0.30,
    "unknown":         0.50,
}


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ClipScores:
    """Per-dimension scores for one candidate segment."""

    raw_quality:       float = 0.5
    motion_continuity: float = 0.5
    semantic_fit:      float = 0.5
    aesthetic_quality: float = 0.5
    visual_intensity:  float = 0.5
    face_consistency:  float = 0.5
    arc_fit:           float = 0.5
    composite:         float = 0.5

    def asdict(self) -> dict[str, float]:
        return {
            "raw_quality":       self.raw_quality,
            "motion_continuity": self.motion_continuity,
            "semantic_fit":      self.semantic_fit,
            "aesthetic_quality": self.aesthetic_quality,
            "visual_intensity":  self.visual_intensity,
            "face_consistency":  self.face_consistency,
            "arc_fit":           self.arc_fit,
            "composite":         self.composite,
        }


@dataclass
class ReferenceStyle:
    """Distilled fingerprint used for scoring; computed once per plan() call."""

    avg_shot_duration: float = 1.5
    pace:              str   = "medium"
    energy_level:      str   = "medium"
    motion_style:      str   = "slow_push"
    target_intensity:  float = 0.425   # derived from pace + energy_level
    color_brightness:  float = 128.0   # luma_avg (0–255)
    color_saturation:  float = 1.0
    tone:              str   = "neutral"

    @classmethod
    def from_fingerprint(cls, fingerprint: dict[str, Any]) -> "ReferenceStyle":
        pace   = fingerprint.get("pace", "medium")
        energy = fingerprint.get("energy_level", "medium")

        motion_data  = fingerprint.get("motion_style") or {}
        motion_style = (
            motion_data.get("primary") if isinstance(motion_data, dict) else str(motion_data)
        ) or fingerprint.get("motion_pattern", "slow_push")

        cg  = fingerprint.get("color_grade") or fingerprint.get("color_profile") or {}
        luma = cg.get("luma_avg", 128.0)
        sat  = cg.get("saturation", 1.0)

        pace_i   = _PACE_INTENSITY.get(pace, 0.45)
        energy_i = _ENERGY_INTENSITY.get(energy, 0.45)
        target_i = round((pace_i + energy_i) / 2.0, 3)

        return cls(
            avg_shot_duration = fingerprint.get("avg_shot_duration", 1.5),
            pace              = pace,
            energy_level      = energy,
            motion_style      = motion_style,
            target_intensity  = target_i,
            color_brightness  = luma,
            color_saturation  = sat,
            tone              = fingerprint.get("tone", "neutral"),
        )


# ── ClipRanker ────────────────────────────────────────────────────────────────

class ClipRanker:
    """
    Ranks footage segments using a multi-dimensional composite score.

    Usage::

        ranker = ClipRanker.from_fingerprint(fingerprint)
        ranked = ranker.rank(
            candidates,
            used_ids=used_ids,
            shot_dur=shot_dur,
            prev_segment=prev,
            slot_index=i,
            total_slots=n,
        )
        best = ranked[0]   # highest composite score
    """

    def __init__(
        self,
        ref: ReferenceStyle,
        weights: dict[str, float] | None = None,
        *,
        subject_continuity: bool = True,
        profile: str | None = None,
        ref_embedding: list[float] | None = None,
    ) -> None:
        self.ref                 = ref
        self.weights             = {**_DEFAULT_WEIGHTS, **(weights or {})}
        self._subject_continuity = subject_continuity
        self._profile_name       = profile
        #: Pre-computed CLIP reference embedding; set via from_fingerprint or
        #: directly on the instance before calling rank().
        self._ref_embedding: list[float] | None = ref_embedding

    @classmethod
    def from_fingerprint(
        cls,
        fingerprint: dict[str, Any],
        weights: dict[str, float] | None = None,
    ) -> "ClipRanker":
        profile  = fingerprint.get("ranking_profile")
        resolved = _resolve_profile_weights(fingerprint, weights)
        subj     = _detect_subject_continuity(fingerprint, profile)
        return cls(
            ReferenceStyle.from_fingerprint(fingerprint),
            weights=resolved,
            subject_continuity=subj,
            profile=profile,
            ref_embedding=fingerprint.get("_ref_embedding"),
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def rank(
        self,
        candidates: list[dict[str, Any]],
        *,
        used_ids: set[str],
        shot_dur: float,
        prev_segment: dict[str, Any] | None,
        slot_index: int,
        total_slots: int,
    ) -> list[dict[str, Any]]:
        """
        Return *candidates* sorted by composite score (highest first).

        Segments are first filtered to those that:
          - are not in ``used_ids``
          - have sufficient duration (``end - start >= shot_dur - 0.05``)

        If that yields nothing, ``used_ids`` is relaxed; if still nothing, the
        duration constraint is also relaxed so callers always get something back
        (unless *candidates* is empty).
        """
        if not candidates:
            return []

        position_ratio = slot_index / max(total_slots - 1, 1)

        def _score_and_wrap(seg: dict[str, Any]) -> tuple[float, dict[str, Any]]:
            scores  = self._score_segment(seg, prev_segment, position_ratio)
            explain = _build_explainability(
                scores,
                profile=self._profile_name,
                weights=self.weights,
                subject_continuity=self._subject_continuity,
            )
            return scores.composite, {**seg, "_clip_scores": scores, "_explainability": explain}

        # Pass 1: unused + fits duration
        scored = [
            _score_and_wrap(s)
            for s in candidates
            if f"{s['asset_id']}_{s['start']:.3f}" not in used_ids
            and s["end"] - s["start"] >= shot_dur - 0.05
        ]

        # Pass 2: relax used_ids
        if not scored:
            scored = [
                _score_and_wrap(s)
                for s in candidates
                if s["end"] - s["start"] >= shot_dur - 0.05
            ]

        # Pass 3: relax duration constraint too
        if not scored:
            scored = [_score_and_wrap(s) for s in candidates]

        scored.sort(key=lambda t: t[0], reverse=True)
        return [seg for _, seg in scored]

    # ── Scoring dimensions ────────────────────────────────────────────────────

    def _score_segment(
        self,
        seg: dict[str, Any],
        prev: dict[str, Any] | None,
        position_ratio: float,
    ) -> ClipScores:
        rq = self._raw_quality(seg)
        mc = self._motion_continuity(seg, prev)
        sf = self._semantic_fit(seg)
        aq = self._aesthetic_quality(seg)
        vi = self._visual_intensity(seg)
        fc = self._face_consistency(seg, prev)
        af = self._arc_fit(seg, position_ratio)

        w = self.weights
        composite = (
            w["raw_quality"]       * rq
            + w["motion_continuity"] * mc
            + w["semantic_fit"]      * sf
            + w["aesthetic_quality"] * aq
            + w["visual_intensity"]  * vi
            + w["face_consistency"]  * fc
            + w["arc_fit"]           * af
        )

        return ClipScores(
            raw_quality       = round(rq, 4),
            motion_continuity = round(mc, 4),
            semantic_fit      = round(sf, 4),
            aesthetic_quality = round(aq, 4),
            visual_intensity  = round(vi, 4),
            face_consistency  = round(fc, 4),
            arc_fit           = round(af, 4),
            composite         = round(composite, 4),
        )

    def _raw_quality(self, seg: dict[str, Any]) -> float:
        """Normalise FootageAnalyzer score (0–15 range) to [0, 1].

        Scores above 12 are treated as 1.0 to avoid penalising very high
        quality segments for being better than the reference cap.
        """
        return min(seg.get("score", 5.0) / 12.0, 1.0)

    def _motion_continuity(
        self,
        seg: dict[str, Any],
        prev: dict[str, Any] | None,
    ) -> float:
        """Gaussian continuity penalty for abrupt motion-level jumps.

        Score = exp(-delta² / σ²)  where σ = 3.0 (motion scale ~0–10).
        Returns 1.0 when there is no previous clip.
        """
        if prev is None:
            return 1.0
        delta = abs(seg.get("motion", 0.0) - prev.get("motion", 0.0))
        return math.exp(-(delta ** 2) / 9.0)   # σ² = 3² = 9

    def embedding_similarity(self, seg: dict[str, Any]) -> float:
        """Cosine similarity between the segment's CLIP embedding and the reference.

        Returns 0.5 (neutral) when either embedding is absent.  The score is
        in [0, 1] (shifted from [-1, 1]) so it slots directly into the
        composite-score formula alongside the other dimensions.
        """
        ref_emb = self._ref_embedding
        seg_emb = seg.get("_embedding")
        if ref_emb is None or seg_emb is None:
            return 0.5
        # _emb_cosine is cosine_similarity from embeddings.py (shifted to [0,1])
        if _emb_cosine is not None:
            return _emb_cosine(ref_emb, seg_emb)
        # Fallback: inline computation (shouldn't reach here when CLIP is up)
        dot = sum(x * y for x, y in zip(ref_emb, seg_emb))
        return (max(-1.0, min(1.0, dot)) + 1.0) / 2.0

    def _semantic_fit(self, seg: dict[str, Any]) -> float:
        """Semantic similarity: uses real CLIP embeddings when available,
        otherwise falls back to the hand-crafted proxy score.

        The proxy path is always used when:
          - CLIP is not installed, or
          - no reference embedding was computed (``_ref_embedding`` is None), or
          - the segment was not enriched with ``_embedding``.
        """
        if (
            _CLIP_AVAILABLE
            and self._ref_embedding is not None
            and seg.get("_embedding") is not None
        ):
            return self.embedding_similarity(seg)
        return self._semantic_proxy_score(seg)

    def _semantic_proxy_score(self, seg: dict[str, Any]) -> float:
        """CLIP-proxy: cosine similarity of per-segment feature vector vs reference.

        Feature vector (8 dims, all in [0, 1]):
          0  sharpness_norm        normalised sharpness (→ visual clarity)
          1  motion_norm           normalised optical-flow magnitude
          2  has_face              face detected in the frame
          3  has_speech            speech tag present
          4  not_static            inverse of "static" tag
          5  brightness_harmony    proximity of luma to reference luma
          6  saturation_proxy      motion-derived vibrancy proxy
          7  motion_style_match    does the clip match the reference motion style?

        The reference vector encodes the ideal values for each dimension as
        implied by the fingerprint.
        """
        ref    = self.ref
        sharp  = seg.get("sharpness", 5.0)
        motion = seg.get("motion", 0.0)
        tags   = seg.get("tags", [])

        not_static = 1.0 - float("static" in tags)

        # Brightness proximity: closer to reference luma → higher score
        dominant = seg.get("dominant_color")
        if dominant and len(dominant) == 3:
            luma_est = 0.299 * dominant[0] + 0.587 * dominant[1] + 0.114 * dominant[2]
        else:
            luma_est = 128.0   # neutral fallback
        brightness_harmony = 1.0 - abs(luma_est - ref.color_brightness) / 255.0

        # Saturation proxy: vibrancy estimated from motion
        sat_proxy = min(motion / 5.0, 1.0)
        ref_sat_target = max(0.0, min(1.0, ref.color_saturation - 0.5))
        sat_harmony = max(0.0, 1.0 - abs(sat_proxy - ref_sat_target))

        # Motion style: prefer non-static for zooming styles
        ref_ms = ref.motion_style.lower().replace(" ", "_")
        if ref_ms in ("zoom_in", "slow_push", "zoom_out"):
            motion_style_match = not_static * 0.5 + 0.5   # slight preference for motion
        elif ref_ms == "static":
            motion_style_match = (1.0 - not_static) * 0.5 + 0.25
        else:
            motion_style_match = 0.6

        seg_vec = [
            min(sharp / 15.0, 1.0),
            min(motion / 10.0, 1.0),
            float(seg.get("face_present", False)),
            float("speech" in tags),
            not_static,
            brightness_harmony,
            sat_harmony,
            motion_style_match,
        ]
        ref_vec = [
            0.70,                                      # prefer sharp
            _PACE_INTENSITY.get(ref.pace, 0.45),       # motion from pace
            0.50,                                      # neutral on face
            0.30,                                      # mild speech preference
            0.70,                                      # prefer some movement
            1.00,                                      # ref brightness = ideal
            1.00,                                      # ref saturation = ideal
            1.00,                                      # ref motion style = ideal
        ]

        return _cosine_similarity(seg_vec, ref_vec)

    def _aesthetic_quality(self, seg: dict[str, Any]) -> float:
        """Composite aesthetic score from sharpness, exposure, stability, and blur.

        Weights:  sharpness 40%, brightness 25%, stability 20%, blur 15%.
        All sub-scores are in [0, 1].
        """
        sharp         = seg.get("sharpness", 5.0)
        sharpness_norm = min(sharp / 12.0, 1.0)

        cq = seg.get("clip_quality", {})
        brightness_score = {
            "normal":      1.00,
            "underexposed": 0.50,
            "dark":         0.30,
            "overexposed":  0.40,
        }.get(cq.get("brightness", "normal"), 0.70)

        stability_score = _STABILITY_SCORE.get(cq.get("stability", "unknown"), 0.50)
        blur_penalty    = max(0.0, 1.0 - cq.get("blur_score", 0.5))  # low blur → high

        return round(
            0.40 * sharpness_norm
            + 0.25 * brightness_score
            + 0.20 * stability_score
            + 0.15 * blur_penalty,
            4,
        )

    def _visual_intensity(self, seg: dict[str, Any]) -> float:
        """Visual energy/intensity of the segment, normalised to [0, 1].

        If FootageAnalyzer pre-computed an ``intensity`` field (0–10 scale), it
        takes precedence.  Otherwise intensity is derived from optical-flow
        motion magnitude and action tags.
        """
        precomputed = seg.get("intensity")
        if precomputed is not None:
            return min(float(precomputed) / 10.0, 1.0)

        motion      = seg.get("motion", 0.0)
        tags        = seg.get("tags", [])
        motion_norm = min(motion / 8.0, 1.0)
        tag_bonus   = 0.10 if "motion" in tags else 0.0
        tag_penalty = 0.15 if "static" in tags else 0.0

        return max(0.0, min(1.0, motion_norm + tag_bonus - tag_penalty))

    def _raw_face_consistency(
        self,
        seg: dict[str, Any],
        prev: dict[str, Any] | None,
    ) -> float:
        """Unscaled face/person continuity score (used by _face_consistency).

        Rules:
          - No previous clip               → 1.0  (no constraint)
          - Both have face + same hash     → 1.0  (same person)
          - Both have face, different hash → 0.6  (different faces)
          - Both have face, no hash        → 0.7  (faces but unverified)
          - Both no face                   → 0.8  (consistent B-roll)
          - One has face, other does not   → 0.4  (jarring cut)
        """
        if prev is None:
            return 1.0

        curr_face = bool(seg.get("face_present", False))
        prev_face = bool(prev.get("face_present", False))

        if curr_face and prev_face:
            curr_hash = seg.get("face_hash")
            prev_hash = prev.get("face_hash")
            if curr_hash is not None and prev_hash is not None:
                return 1.0 if curr_hash == prev_hash else 0.6
            return 0.7
        elif not curr_face and not prev_face:
            return 0.8
        else:
            return 0.4  # face appeared / disappeared

    def _face_consistency(
        self,
        seg: dict[str, Any],
        prev: dict[str, Any] | None,
    ) -> float:
        """Person/face continuity between adjacent clips, scaled by profile context.

        When the ranking profile (or fingerprint) indicates faces/people are NOT
        central to the content, the raw score is compressed toward neutral (0.5)
        so that face-presence changes have a softer impact on clip selection.
        For subject-continuity profiles (talking_head, vlog) and fingerprints with
        explicit face-central signals, the full raw score is preserved.
        """
        if prev is None:
            return 1.0   # always 1.0 for the first clip regardless of profile
        raw = self._raw_face_consistency(seg, prev)
        if not self._subject_continuity:
            # Compress toward neutral — face transitions are cosmetic, not jarring
            return round(0.5 + (raw - 0.5) * 0.4, 4)
        return raw

    def _arc_fit(self, seg: dict[str, Any], position_ratio: float) -> float:
        """Emotional-arc position fit: how well the clip's intensity matches the
        desired escalation curve at this point in the timeline.

        Score = 1 − |actual_intensity − desired_intensity|
        """
        desired = _desired_arc_intensity(position_ratio, self.ref.target_intensity)
        actual  = self._visual_intensity(seg)
        return max(0.0, 1.0 - abs(actual - desired))


# ── Module-level helpers (exported for testing) ───────────────────────────────

def _build_explainability(
    scores: "ClipScores",
    *,
    profile: str | None,
    weights: dict[str, float],
    subject_continuity: bool,
) -> dict[str, Any]:
    """Build the explainability payload attached to every ranked candidate.

    Returns a dict with keys:
      total_score, profile_used, weights_used, score_breakdown,
      subject_continuity_enabled, reason

    ``score_breakdown`` has one entry per scoring dimension::

        {dim: {"score": float, "weight": float, "contribution": float}}

    ``reason`` is a human-readable sentence naming the top-2 contributing
    dimensions by weighted contribution (score × weight).
    """
    raw = scores.asdict()
    breakdown: dict[str, dict[str, float]] = {}
    for dim, weight in weights.items():
        s = raw.get(dim, 0.5)
        breakdown[dim] = {
            "score":        round(s, 4),
            "weight":       round(weight, 4),
            "contribution": round(s * weight, 4),
        }

    # Top-2 by contribution (highest first)
    top2 = sorted(breakdown.items(), key=lambda kv: kv[1]["contribution"], reverse=True)[:2]
    reason_parts = [
        f"{_DIMENSION_LABELS.get(dim, dim)} ({info['contribution']:.2f})"
        for dim, info in top2
    ]
    reason = f"Selected for {reason_parts[0]} and {reason_parts[1]}"

    return {
        "total_score":                round(scores.composite, 4),
        "profile_used":               profile,
        "weights_used":               {k: round(v, 4) for k, v in weights.items()},
        "score_breakdown":            breakdown,
        "subject_continuity_enabled": subject_continuity,
        "reason":                     reason,
    }


def _resolve_profile_weights(
    fingerprint: dict[str, Any],
    override: dict[str, float] | None,
) -> dict[str, float]:
    """Return the effective weight dict for a fingerprint + optional override.

    Resolution order (highest priority last, so override wins over profile):
      1. ``_DEFAULT_WEIGHTS``
      2. ``PROFILE_WEIGHTS[fingerprint["ranking_profile"]]``  (if present)
      3. *override* key-by-key merge

    The result is renormalised to sum to 1.0 after merging so partial overrides
    remain valid.
    """
    profile = fingerprint.get("ranking_profile")
    base    = PROFILE_WEIGHTS.get(profile, _DEFAULT_WEIGHTS) if profile else _DEFAULT_WEIGHTS
    if not override:
        return dict(base)
    merged = {**base, **override}
    total  = sum(merged.values())
    return {k: v / total for k, v in merged.items()} if total > 0 else dict(base)


def _detect_subject_continuity(
    fingerprint: dict[str, Any],
    profile: str | None,
) -> bool:
    """Return True when the content calls for consistent face/subject tracking.

    Decision logic (first match wins):
      1. Profile in ``_SUBJECT_CONTINUITY_PROFILES``  → True
      2. Fingerprint has explicit face-central signal  → True
      3. ``fingerprint["faces_central"] is True``      → True
      4. Profile in ``_NON_FACE_PROFILES``             → False
      5. No profile present                            → True  (safe default)
    """
    if profile in _SUBJECT_CONTINUITY_PROFILES:
        return True
    for key, values in _FACE_CENTRAL_SIGNALS.items():
        val = fingerprint.get(key)
        if isinstance(val, str) and val.lower() in values:
            return True
    if fingerprint.get("faces_central") is True:
        return True
    if profile in _NON_FACE_PROFILES:
        return False
    return True   # unknown / no profile → preserve original behaviour


def _desired_arc_intensity(position: float, base_intensity: float) -> float:
    """Desired intensity at *position* (0–1) along the emotional arc.

    Curve shape (positions are approximate):
      Hook   0.00–0.15  high   (grab attention)
      Build  0.15–0.50  rising
      Climax 0.50–0.75  peak
      Closer 0.75–1.00  tapering

    The curve is scaled by *base_intensity* (derived from reference pace + energy).
    """
    scale = max(base_intensity, 0.30)   # floor so very low-energy refs still vary

    if position <= 0.15:            # Hook
        return min(1.0, scale * 1.4)
    elif position <= 0.50:          # Build – linear rise
        t = (position - 0.15) / 0.35
        return min(1.0, scale * (0.80 + 0.30 * t))
    elif position <= 0.75:          # Climax
        return min(1.0, scale * 1.5)
    else:                           # Closer – taper
        t = (position - 0.75) / 0.25
        return min(1.0, scale * (1.20 - 0.40 * t))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors, clipped to [0, 1].

    Returns 0.5 (neutral) for zero or mismatched vectors.
    """
    if len(a) != len(b) or not a:
        return 0.5
    dot   = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.5
    return max(0.0, min(1.0, dot / (mag_a * mag_b)))
