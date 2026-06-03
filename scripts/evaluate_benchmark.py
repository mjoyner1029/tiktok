#!/usr/bin/env python3
"""Evaluation layer for benchmark_render_styles.py outputs.

Commands
--------
  evaluate   Score profiles automatically → ranked_profiles.json + evaluation_summary.md
  review     Interactive terminal: add user_rating / notes / status → updates report.json
  show       Print current rankings to stdout

Usage
-----
  python scripts/evaluate_benchmark.py evaluate --report ./benchmarks/report.json
  python scripts/evaluate_benchmark.py review   --report ./benchmarks/report.json
  python scripts/evaluate_benchmark.py show     --report ./benchmarks/report.json

Scoring dimensions (automatic, 0–10 each)
------------------------------------------
  pacing_similarity      Avg shot duration vs reference avg_shot_duration
  avg_clip_quality       Mean total_score across selected clips
  visual_variety         Unique-asset ratio
  continuity             Absence of back-to-back asset repetition
  render_style_strength  Richness of applied visual effects
  profile_distinctiveness  How unlike all other profiles' render_style this is

Manual scoring (set by `review`)
---------------------------------
  user_rating   1–5
  notes         free text
  status        approved | rejected | needs_revision | unreviewed

Final score = auto_score when no manual rating, else:
  0.5 * auto_score + 0.5 * (user_rating * 2)   [maps 1–5 → 2–10]
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── constants ─────────────────────────────────────────────────────────────────

PROFILES_ORDER = [
    "fashion_montage", "music_video", "talking_head",
    "product_showcase", "travel_reel", "vlog",
]

VALID_STATUSES = {"approved", "rejected", "needs_revision", "unreviewed"}

DEFAULT_WEIGHTS: dict[str, float] = {
    "pacing_similarity":      0.20,
    "avg_clip_quality":       0.25,
    "visual_variety":         0.20,
    "continuity":             0.10,
    "render_style_strength":  0.15,
    "profile_distinctiveness": 0.10,
}

# When manual user_rating is provided, it blends in at this weight
MANUAL_BLEND_WEIGHT = 0.40


# ═══════════════════════════════════════════════════════════════════════════
#  SCORING FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def _score_pacing_similarity(profile: dict, base_fp: dict) -> float:
    """How closely the edit's avg shot duration matches the reference.

    Returns 0–10; 10 = perfect match, 0 = 2x or more off.
    """
    ref_avg = base_fp.get("avg_shot_duration", 0.0)
    clips = profile.get("timeline_clips", [])
    if not clips or ref_avg <= 0:
        return 5.0  # neutral fallback

    durations = [c.get("duration", 0.0) for c in clips if c.get("duration", 0) > 0]
    if not durations:
        return 5.0

    actual_avg = sum(durations) / len(durations)
    # Relative error: 0 % → 10, 50 % → 5, 100 % → 0
    rel_err = abs(actual_avg - ref_avg) / ref_avg
    return round(max(0.0, 10.0 - rel_err * 10.0), 2)


def _score_avg_clip_quality(profile: dict) -> float:
    """Mean total_score across selected clips, scaled 0–10."""
    clips = profile.get("timeline_clips", [])
    scores = [c.get("total_score") for c in clips if c.get("total_score") is not None]
    if not scores:
        return 0.0
    avg = sum(scores) / len(scores)
    # total_score is 0–1; multiply by 10
    return round(min(10.0, avg * 10.0), 2)


def _score_visual_variety(profile: dict) -> float:
    """Ratio of unique asset IDs to total clips, scaled 0–10.

    Perfect variety (all unique) = 10; all same asset = 0.
    """
    clips = profile.get("timeline_clips", [])
    if not clips:
        return 0.0
    unique = len({c.get("asset_id", "") for c in clips})
    ratio = unique / len(clips)
    return round(ratio * 10.0, 2)


def _score_continuity(profile: dict) -> float:
    """Penalise consecutive clips that share the same source asset.

    A back-to-back repeat is jarring; the score reflects the absence of these.
    Returns 0–10; 10 = no repeats, 0 = every pair is a repeat.
    """
    clips = profile.get("timeline_clips", [])
    if len(clips) < 2:
        return 10.0

    consecutive_repeats = sum(
        1
        for i in range(len(clips) - 1)
        if clips[i].get("asset_id") == clips[i + 1].get("asset_id")
    )
    repeat_rate = consecutive_repeats / (len(clips) - 1)
    return round(max(0.0, 10.0 - repeat_rate * 20.0), 2)


def _score_render_style_strength(profile: dict) -> float:
    """Award up to 2 pts each for five notable visual treatments.

    color_grade boost | grain | vignette | extra_filters | non-static zoom
    Returns 0–10.
    """
    rs = profile.get("render_style") or {}
    score = 0.0

    # Color grade distinctiveness (contrast or saturation off neutral)
    cg = rs.get("color_grade", {})
    contrast_delta = abs(cg.get("contrast", 1.0) - 1.0)
    sat_delta = abs(cg.get("saturation", 1.0) - 1.0)
    if contrast_delta > 0.03 or sat_delta > 0.03:
        score += 2.0

    # Grain
    grain = rs.get("grain", {})
    if grain.get("enabled") and grain.get("strength", 0) > 0:
        score += 2.0

    # Vignette
    vig = rs.get("vignette", {})
    if vig.get("enabled") and vig.get("angle", 0) > 0:
        score += 2.0

    # Sharpening / extra_filters
    if cg.get("extra_filters", "").strip():
        score += 2.0

    # Non-static zoom
    zs = rs.get("zoom_style", {})
    if zs.get("type", "static") not in ("static", "") and zs.get("strength", 0) > 0:
        score += 2.0

    return round(min(10.0, score), 2)


def _style_feature_vector(render_style: dict | None) -> list[float]:
    """Extract a 7-dimensional numeric feature vector from a render_style dict."""
    if not render_style:
        return [0.0] * 7
    cg = render_style.get("color_grade", {})
    grain = render_style.get("grain", {})
    vig = render_style.get("vignette", {})
    zs = render_style.get("zoom_style", {})
    return [
        cg.get("contrast", 1.0) - 1.0,                      # 0–0.35 typical
        cg.get("saturation", 1.0) - 1.0,                    # 0–0.35
        cg.get("brightness", 0.0),                          # ±0.10
        1.0 if grain.get("enabled") else 0.0,
        min(1.0, grain.get("strength", 0.0) / 20.0),       # normalise to 0–1
        1.0 if vig.get("enabled") else 0.0,
        min(1.0, zs.get("strength", 0.0) / 0.12),          # normalise to 0–1
    ]


def _euclidean(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _score_profile_distinctiveness(
    target: dict, all_profiles: list[dict]
) -> float:
    """How unlike all other profiles' render_style this one is.

    Average pairwise L2 distance, normalised to 0–10.
    Profiles that share the same visual DNA score low; highly unique ones score high.
    """
    target_vec = _style_feature_vector(target.get("render_style"))
    others = [
        _style_feature_vector(p.get("render_style"))
        for p in all_profiles
        if p.get("profile") != target.get("profile") and p.get("success")
    ]
    if not others:
        return 10.0

    distances = [_euclidean(target_vec, o) for o in others]
    avg_dist = sum(distances) / len(distances)

    # Max theoretical distance between [0]*7 and [1]*7 = sqrt(7) ≈ 2.65
    # Scale so avg_dist=2.65 → 10, avg_dist=0 → 0
    max_dist = math.sqrt(7)
    return round(min(10.0, (avg_dist / max_dist) * 10.0), 2)


def _compute_dimensions(
    profile: dict,
    all_profiles: list[dict],
    base_fingerprint: dict,
) -> dict[str, float]:
    return {
        "pacing_similarity":      _score_pacing_similarity(profile, base_fingerprint),
        "avg_clip_quality":       _score_avg_clip_quality(profile),
        "visual_variety":         _score_visual_variety(profile),
        "continuity":             _score_continuity(profile),
        "render_style_strength":  _score_render_style_strength(profile),
        "profile_distinctiveness": _score_profile_distinctiveness(profile, all_profiles),
    }


def _auto_score(dimensions: dict[str, float], weights: dict[str, float]) -> float:
    total = sum(weights.get(k, 0.0) * v for k, v in dimensions.items())
    weight_sum = sum(weights.get(k, 0.0) for k in dimensions)
    return round(total / weight_sum if weight_sum > 0 else 0.0, 3)


def _final_score(auto_score: float, user_rating: int | None) -> float:
    """Blend auto score with manual rating when available."""
    if user_rating is None:
        return auto_score
    # user_rating 1–5 → 2–10 to match 0–10 scale
    manual_score = user_rating * 2.0
    blended = (1 - MANUAL_BLEND_WEIGHT) * auto_score + MANUAL_BLEND_WEIGHT * manual_score
    return round(blended, 3)


def _render_style_summary(rs: dict | None) -> dict:
    if not rs:
        return {}
    cg = rs.get("color_grade", {})
    return {
        "zoom_type":       rs.get("zoom_style", {}).get("type", "static"),
        "zoom_strength":   rs.get("zoom_style", {}).get("strength", 0.0),
        "transition_type": rs.get("transition_style", {}).get("type", "hard_cut"),
        "grain":           rs.get("grain", {}).get("enabled", False),
        "vignette":        rs.get("vignette", {}).get("enabled", False),
        "sharpening":      bool(cg.get("extra_filters", "").strip()),
        "contrast":        cg.get("contrast", 1.0),
        "saturation":      cg.get("saturation", 1.0),
        "brightness":      cg.get("brightness", 0.0),
        "caption_position": rs.get("caption_style", {}).get("position", "center"),
        "caption_anim":    rs.get("caption_style", {}).get("animation", "pop"),
        "caption_all_caps": rs.get("caption_style", {}).get("all_caps", True),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  INSIGHTS
# ═══════════════════════════════════════════════════════════════════════════

def _compute_insights(ranked: list[dict]) -> dict:
    succeeded = [r for r in ranked if r.get("_success")]
    if not succeeded:
        return {}

    best = succeeded[0]["profile"] if succeeded else None
    most_distinctive = max(
        succeeded, key=lambda r: r["dimension_scores"].get("profile_distinctiveness", 0),
        default={"profile": None}
    )["profile"]
    best_quality = max(
        succeeded, key=lambda r: r["dimension_scores"].get("avg_clip_quality", 0),
        default={"profile": None}
    )["profile"]
    needs_attention = [
        r["profile"]
        for r in ranked
        if r.get("review", {}).get("status") == "needs_revision"
        or not r.get("_success")
    ]
    approved = [r["profile"] for r in ranked if r.get("review", {}).get("status") == "approved"]
    rejected = [r["profile"] for r in ranked if r.get("review", {}).get("status") == "rejected"]

    return {
        "best_overall":          best,
        "most_distinctive":      most_distinctive,
        "best_clip_quality":     best_quality,
        "needs_attention":       needs_attention,
        "approved_profiles":     approved,
        "rejected_profiles":     rejected,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  MARKDOWN REPORT
# ═══════════════════════════════════════════════════════════════════════════

_STATUS_EMOJI = {
    "approved":       "✅",
    "rejected":       "❌",
    "needs_revision": "🔄",
    "unreviewed":     "⬜",
}

_STATUS_LABEL = {
    "approved":       "Approved",
    "rejected":       "Rejected",
    "needs_revision": "Needs revision",
    "unreviewed":     "Unreviewed",
}


def _md_table_row(cells: list[str], widths: list[int]) -> str:
    padded = [str(c).ljust(w) for c, w in zip(cells, widths)]
    return "| " + " | ".join(padded) + " |"


def _build_markdown(
    ranked: list[dict],
    base_fp: dict,
    report_path: Path,
    weights: dict[str, float],
    insights: dict,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    reference = base_fp.get("_source", str(report_path.parent))

    lines: list[str] = []

    lines += [
        "# Benchmark Evaluation Summary",
        "",
        f"Generated: {now}  ",
        f"Report: `{report_path}`  ",
        f"Reference fingerprint: pace=**{base_fp.get('pace', '—')}**, "
        f"avg_shot=**{base_fp.get('avg_shot_duration', 0):.2f}s**, "
        f"energy=**{base_fp.get('energy_level', '—')}**",
        "",
    ]

    # ── Rankings table ────────────────────────────────────────────────────
    lines += ["## Rankings", ""]
    headers = ["Rank", "Profile", "Final", "Auto", "Rating", "Status",
               "Pacing", "Quality", "Variety", "Cont.", "Style", "Distinct."]
    col_w =  [4, 22, 7, 7, 8, 16, 7, 7, 7, 6, 6, 8]

    sep = "|-" + "-|-".join("-" * w for w in col_w) + "-|"
    lines.append(_md_table_row(headers, col_w))
    lines.append(sep)

    for r in ranked:
        rv = r.get("review", {})
        status = rv.get("status", "unreviewed")
        rating = rv.get("user_rating")
        ds = r.get("dimension_scores", {})
        emoji = _STATUS_EMOJI.get(status, "⬜")
        row = [
            str(r["rank"]),
            r["profile"],
            f"{r['final_score']:.2f}",
            f"{r['auto_score']:.2f}",
            f"{rating}/5" if rating else "—",
            f"{emoji} {_STATUS_LABEL.get(status, status)}",
            f"{ds.get('pacing_similarity', 0):.1f}",
            f"{ds.get('avg_clip_quality', 0):.1f}",
            f"{ds.get('visual_variety', 0):.1f}",
            f"{ds.get('continuity', 0):.1f}",
            f"{ds.get('render_style_strength', 0):.1f}",
            f"{ds.get('profile_distinctiveness', 0):.1f}",
        ]
        lines.append(_md_table_row(row, col_w))

    lines += [""]

    # ── Scoring weights note ───────────────────────────────────────────────
    weight_strs = ", ".join(f"{k.replace('_', ' ')}={v:.0%}" for k, v in weights.items())
    lines += [
        f"> **Weights**: {weight_strs}  ",
        f"> Manual rating blends in at {MANUAL_BLEND_WEIGHT:.0%} when provided.",
        "",
    ]

    # ── Insights ──────────────────────────────────────────────────────────
    if insights:
        lines += ["## Insights", ""]
        if insights.get("best_overall"):
            lines.append(f"- **Best overall**: `{insights['best_overall']}`")
        if insights.get("most_distinctive"):
            lines.append(f"- **Most visually distinctive**: `{insights['most_distinctive']}`")
        if insights.get("best_clip_quality"):
            lines.append(f"- **Best clip quality**: `{insights['best_clip_quality']}`")
        if insights.get("approved_profiles"):
            lines.append(f"- **Approved**: {', '.join(f'`{p}`' for p in insights['approved_profiles'])}")
        if insights.get("rejected_profiles"):
            lines.append(f"- **Rejected**: {', '.join(f'`{p}`' for p in insights['rejected_profiles'])}")
        if insights.get("needs_attention"):
            lines.append(f"- **Needs attention**: {', '.join(f'`{p}`' for p in insights['needs_attention'])}")
        lines.append("")

    # ── Per-profile detail ─────────────────────────────────────────────────
    lines += ["## Profile Details", ""]

    for r in ranked:
        profile = r["profile"]
        rv = r.get("review", {})
        ds = r.get("dimension_scores", {})
        rs_sum = r.get("render_style_summary", {})
        clips = r.get("_clips", [])
        success = r.get("_success", False)

        status_str = _STATUS_LABEL.get(rv.get("status", "unreviewed"), "Unreviewed")
        emoji = _STATUS_EMOJI.get(rv.get("status", "unreviewed"), "⬜")

        lines += [
            f"### {r['rank']}. {profile.replace('_', ' ').title()}  "
            f"(Auto: {r['auto_score']:.2f} / Final: {r['final_score']:.2f})",
            "",
        ]

        if not success:
            err = r.get("_error", "unknown error")
            lines += [f"> ⚠️ Render failed: `{err}`", ""]
            continue

        # Contact sheet image (relative path)
        sheet = r.get("contact_sheet_path")
        if sheet:
            rel = _relative_path(Path(sheet), report_path.parent)
            lines += [f"![{profile} contact sheet]({rel})", ""]

        # Render style
        if rs_sum:
            feat_parts = []
            feat_parts.append(f"contrast={rs_sum['contrast']:.2f}")
            feat_parts.append(f"saturation={rs_sum['saturation']:.2f}")
            if rs_sum.get("sharpening"):
                feat_parts.append("sharpening=on")
            if rs_sum.get("grain"):
                feat_parts.append("grain=on")
            if rs_sum.get("vignette"):
                feat_parts.append("vignette=on")

            lines += [
                "**Render style:**",
                f"- Color: {', '.join(feat_parts)}",
                f"- Motion: {rs_sum.get('zoom_type', '—')} (strength={rs_sum.get('zoom_strength', 0):.3f})",
                f"- Transition: {rs_sum.get('transition_type', '—')}",
                f"- Caption: {rs_sum.get('caption_position', '—')}, "
                f"anim={rs_sum.get('caption_anim', '—')}, "
                f"ALL CAPS={rs_sum.get('caption_all_caps', True)}",
                "",
            ]

        # Clip selection stats
        if clips:
            scores = [c.get("total_score") for c in clips if c.get("total_score") is not None]
            avg_q = f"{(sum(scores)/len(scores)):.3f}" if scores else "—"
            unique_assets = len({c.get("asset_id") for c in clips})
            lines += [
                "**Clip selection:**",
                f"- {len(clips)} clips, "
                f"{r.get('total_duration_sec', 0):.1f}s total, "
                f"avg shot={sum(c.get('duration',0) for c in clips)/max(len(clips),1):.2f}s",
                f"- Avg clip quality: {avg_q}",
                f"- Unique assets: {unique_assets} / {len(clips)}",
                "",
            ]

        # Dimension scores breakdown
        lines += [
            "**Dimension scores:**",
            f"| Dimension | Score |",
            f"|---|---|",
        ]
        for dim, val in ds.items():
            lines.append(f"| {dim.replace('_', ' ').title()} | {val:.2f} |")
        lines.append("")

        # Review box
        lines += [
            "**Review:**",
            f"- Status: {emoji} {status_str}",
        ]
        if rv.get("user_rating"):
            lines.append(f"- Rating: {rv['user_rating']}/5")
        if rv.get("notes"):
            lines.append(f"- Notes: _{rv['notes']}_")
        if rv.get("reviewed_at"):
            lines.append(f"- Reviewed: {rv['reviewed_at']}")
        lines += ["", "---", ""]

    # ── Scoring methodology ─────────────────────────────────────────────────
    lines += [
        "## Scoring Methodology",
        "",
        "| Dimension | Weight | Description |",
        "|---|---|---|",
        f"| Pacing similarity | {weights.get('pacing_similarity',0):.0%} | Avg shot duration vs reference |",
        f"| Avg clip quality | {weights.get('avg_clip_quality',0):.0%} | Mean ranker score across selected clips |",
        f"| Visual variety | {weights.get('visual_variety',0):.0%} | Unique-asset ratio |",
        f"| Continuity | {weights.get('continuity',0):.0%} | No consecutive same-asset repeats |",
        f"| Render style strength | {weights.get('render_style_strength',0):.0%} | Active grain, vignette, sharpening, non-static zoom |",
        f"| Profile distinctiveness | {weights.get('profile_distinctiveness',0):.0%} | L2 distance from all other render styles |",
        "",
        f"When a manual rating (1–5) is provided, it blends in at {MANUAL_BLEND_WEIGHT:.0%}:",
        "",
        "```",
        "final = (1 - 0.40) × auto_score + 0.40 × (user_rating × 2)",
        "```",
        "",
    ]

    return "\n".join(lines)


def _relative_path(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


# ═══════════════════════════════════════════════════════════════════════════
#  evaluate COMMAND
# ═══════════════════════════════════════════════════════════════════════════

def cmd_evaluate(args: argparse.Namespace) -> int:
    report_path = Path(args.report).expanduser().resolve()
    if not report_path.exists():
        print(f"ERROR: report not found: {report_path}", file=sys.stderr)
        return 2

    report = json.loads(report_path.read_text(encoding="utf-8"))
    base_fp = report.get("base_fingerprint", {})
    base_fp["_source"] = report.get("reference_mp4", str(report_path.parent))
    profiles = report.get("profiles", [])

    if not profiles:
        print("ERROR: report contains no profiles.", file=sys.stderr)
        return 2

    # Load custom weights
    weights = copy.copy(DEFAULT_WEIGHTS)
    if args.weights:
        try:
            custom = json.loads(args.weights)
            weights.update(custom)
        except json.JSONDecodeError as exc:
            print(f"ERROR: --weights is not valid JSON: {exc}", file=sys.stderr)
            return 2

    # Normalise weights to sum to 1.0
    total_w = sum(weights.values())
    if total_w > 0:
        weights = {k: v / total_w for k, v in weights.items()}

    # ── Learned weights (from tune_profile_weights.py) override custom/default ──
    _per_profile_learned: dict[str, dict[str, float]] = {}
    if getattr(args, "learned_weights", None):
        lw_path = Path(args.learned_weights).expanduser().resolve()
        if not lw_path.exists():
            print(f"ERROR: --learned-weights not found: {lw_path}", file=sys.stderr)
            return 2
        try:
            lw_doc = json.loads(lw_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"ERROR: --learned-weights is not valid JSON: {exc}", file=sys.stderr)
            return 2
        if lw_doc.get("global"):
            lw_global = {
                k: float(v)
                for k, v in lw_doc["global"].items()
                if k in DEFAULT_WEIGHTS
            }
            if lw_global:
                total = sum(lw_global.values())
                weights = {k: v / total for k, v in lw_global.items()} if total > 0 else weights
        _per_profile_learned = {
            prof: {k: float(v) for k, v in pw.items() if k in DEFAULT_WEIGHTS}
            for prof, pw in lw_doc.get("per_profile", {}).items()
        }

    succeeded = [p for p in profiles if p.get("success")]

    # Compute dimensions for every succeeded profile
    dim_by_profile: dict[str, dict[str, float]] = {}
    for p in succeeded:
        dim_by_profile[p["profile"]] = _compute_dimensions(p, succeeded, base_fp)

    # Build ranked list
    ranked: list[dict] = []
    for p in profiles:
        profile = p["profile"]
        dims = dim_by_profile.get(profile, {k: 0.0 for k in weights})
        # Per-profile learned weights take priority over global when available
        pp_w = _per_profile_learned.get(profile)
        if pp_w:
            pp_total = sum(pp_w.values())
            eff_weights = {k: v / pp_total for k, v in pp_w.items()} if pp_total > 0 else weights
        else:
            eff_weights = weights
        auto = _auto_score(dims, eff_weights) if p.get("success") else 0.0
        existing_review = p.get("review", {})
        user_rating = existing_review.get("user_rating")
        final = _final_score(auto, user_rating)

        ranked.append({
            "rank":                0,  # set after sort
            "profile":             profile,
            "auto_score":          auto,
            "final_score":         final,
            "dimension_scores":    dims,
            "render_style_summary": _render_style_summary(p.get("render_style")),
            "review":              existing_review,
            "output_path":         p.get("output_path"),
            "contact_sheet_path":  p.get("contact_sheet_path"),
            "total_duration_sec":  p.get("total_duration_sec", 0.0),
            "num_clips":           p.get("num_clips", 0),
            # private fields used for markdown but not written to JSON
            "_success": p.get("success", False),
            "_error":   p.get("error"),
            "_clips":   p.get("timeline_clips", []),
        })

    ranked.sort(key=lambda r: r["final_score"], reverse=True)
    for i, r in enumerate(ranked):
        r["rank"] = i + 1

    insights = _compute_insights(ranked)

    # ── Write ranked_profiles.json ──────────────────────────────────────────
    out_dir = report_path.parent
    ranked_path = out_dir / "ranked_profiles.json"

    # Strip private _ fields before writing
    ranked_clean = [
        {k: v for k, v in r.items() if not k.startswith("_")}
        for r in ranked
    ]
    ranked_doc = {
        "ranked_at":       datetime.now(timezone.utc).isoformat(),
        "report_path":     str(report_path),
        "reference_mp4":   report.get("reference_mp4"),
        "scoring_weights": weights,
        "manual_blend_weight": MANUAL_BLEND_WEIGHT,
        "ranked":          ranked_clean,
        "insights":        insights,
    }
    ranked_path.write_text(json.dumps(ranked_doc, indent=2, default=str), encoding="utf-8")
    print(f"Wrote: {ranked_path}")

    # ── Write evaluation_summary.md ─────────────────────────────────────────
    md_path = out_dir / "evaluation_summary.md"
    md = _build_markdown(ranked, base_fp, report_path, weights, insights)
    md_path.write_text(md, encoding="utf-8")
    print(f"Wrote: {md_path}")

    # ── Print quick table ───────────────────────────────────────────────────
    _print_rankings_table(ranked)
    return 0


# ═══════════════════════════════════════════════════════════════════════════
#  review COMMAND
# ═══════════════════════════════════════════════════════════════════════════

_STATUS_SHORTCUTS = {
    "a": "approved",
    "r": "rejected",
    "n": "needs_revision",
    "s": "unreviewed",  # skip / clear
}


def _prompt(msg: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"  {msg}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        raise KeyboardInterrupt
    return val if val else default


def _review_profile(profile_data: dict) -> dict | None:
    """Interactively collect review for one profile. Returns updated review dict or None to skip."""
    existing = profile_data.get("review", {})
    name = profile_data["profile"].replace("_", " ").upper()

    print()
    print(f"  ╔═ {name} ═{'═'*max(0,40-len(name))}╗")

    if not profile_data.get("success"):
        print(f"  │  ⚠  Render FAILED: {profile_data.get('error', '?')}")
        print(f"  ╚{'═'*44}╝")
        ans = _prompt("Rate anyway? (y/N)", "N")
        if ans.lower() != "y":
            return None

    # Stats
    clips = profile_data.get("timeline_clips", [])
    scores = [c.get("total_score", 0) for c in clips if c.get("total_score") is not None]
    avg_q = f"{sum(scores)/len(scores):.3f}" if scores else "—"
    rs = profile_data.get("render_style") or {}
    zs = rs.get("zoom_style", {})
    gn = rs.get("grain", {})
    vg = rs.get("vignette", {})

    print(f"  │  Clips: {profile_data.get('num_clips', '?')}  "
          f"Duration: {profile_data.get('total_duration_sec', 0):.1f}s  "
          f"Avg quality: {avg_q}")
    print(f"  │  Zoom: {zs.get('type','?')}  Grain: {'on' if gn.get('enabled') else 'off'}  "
          f"Vignette: {'on' if vg.get('enabled') else 'off'}")
    sheet = profile_data.get("contact_sheet_path")
    if sheet and Path(sheet).exists():
        print(f"  │  Sheet: {sheet}")
    print(f"  ╚{'═'*44}╝")

    current_rating = existing.get("user_rating")
    current_status = existing.get("status", "unreviewed")
    current_notes  = existing.get("notes", "")

    # Rating
    while True:
        raw = _prompt(
            "Rating 1–5  (Enter = keep current, 0 = clear)",
            str(current_rating) if current_rating else "",
        )
        if raw == "":
            new_rating = current_rating
            break
        if raw == "0":
            new_rating = None
            break
        try:
            val = int(raw)
            if 1 <= val <= 5:
                new_rating = val
                break
            print("  Please enter a number from 1 to 5.")
        except ValueError:
            print("  Please enter a number from 1 to 5.")

    # Status
    status_opts = "a=approved  r=rejected  n=needs_revision  s=skip/clear  Enter=keep"
    while True:
        raw = _prompt(f"Status ({status_opts})", "")
        if raw == "":
            new_status = current_status
            break
        mapped = _STATUS_SHORTCUTS.get(raw.lower())
        if mapped:
            new_status = mapped
            break
        if raw.lower() in VALID_STATUSES:
            new_status = raw.lower()
            break
        print(f"  Invalid. Use: {', '.join(_STATUS_SHORTCUTS.keys())} or full status name.")

    # Notes
    new_notes = _prompt("Notes (Enter = keep current)", current_notes)

    return {
        "status":      new_status,
        "user_rating": new_rating,
        "notes":       new_notes,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }


def cmd_review(args: argparse.Namespace) -> int:
    report_path = Path(args.report).expanduser().resolve()
    if not report_path.exists():
        print(f"ERROR: report not found: {report_path}", file=sys.stderr)
        return 2

    report = json.loads(report_path.read_text(encoding="utf-8"))
    profiles = report.get("profiles", [])

    if not profiles:
        print("No profiles in report.", file=sys.stderr)
        return 2

    # Filter to requested profiles
    target_profiles = args.profiles or [p["profile"] for p in profiles]
    profile_map = {p["profile"]: p for p in profiles}

    print()
    print("═" * 50)
    print("  PROFILE REVIEW SESSION")
    print(f"  {len(target_profiles)} profile(s) to review")
    print("  Ctrl+C at any time to save and exit.")
    print("═" * 50)

    updated = 0
    try:
        for name in target_profiles:
            pdata = profile_map.get(name)
            if not pdata:
                print(f"  ⚠ Profile '{name}' not found in report, skipping.")
                continue
            review = _review_profile(pdata)
            if review is not None:
                pdata["review"] = review
                updated += 1
                status_str = _STATUS_LABEL.get(review["status"], review["status"])
                rating_str = f"  rating={review['user_rating']}/5" if review["user_rating"] else ""
                print(f"  → Saved: {status_str}{rating_str}")
    except KeyboardInterrupt:
        print("\n  Interrupted — saving partial results.")

    if updated == 0:
        print("\nNo profiles updated.")
        return 0

    # Write updated report.json
    report["profiles"] = profiles
    report["last_reviewed"] = datetime.now(timezone.utc).isoformat()
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nUpdated {updated} profile(s) in: {report_path}")
    print("Run `evaluate` to regenerate ranked_profiles.json with the new ratings.")
    return 0


# ═══════════════════════════════════════════════════════════════════════════
#  show COMMAND
# ═══════════════════════════════════════════════════════════════════════════

def cmd_show(args: argparse.Namespace) -> int:
    report_path = Path(args.report).expanduser().resolve()
    if not report_path.exists():
        print(f"ERROR: report not found: {report_path}", file=sys.stderr)
        return 2

    # Prefer ranked_profiles.json if it exists
    ranked_path = report_path.parent / "ranked_profiles.json"
    if ranked_path.exists():
        doc = json.loads(ranked_path.read_text(encoding="utf-8"))
        ranked_raw = doc.get("ranked", [])
        print(f"\nRankings from: {ranked_path}")
        print(f"Generated:     {doc.get('ranked_at', '?')}")

        _print_rankings_table([
            {
                "rank":           r["rank"],
                "profile":        r["profile"],
                "auto_score":     r["auto_score"],
                "final_score":    r["final_score"],
                "dimension_scores": r.get("dimension_scores", {}),
                "review":         r.get("review", {}),
                "_success":       r.get("output_path") is not None,
            }
            for r in ranked_raw
        ])
    else:
        # Fall back to raw report
        report = json.loads(report_path.read_text(encoding="utf-8"))
        profiles = report.get("profiles", [])
        print(f"\nReport: {report_path}  (run `evaluate` to score)")
        print(f"{'Profile':<22} {'Clips':>6} {'Duration':>9} {'Status':<18}")
        print("-" * 60)
        for p in profiles:
            rv = p.get("review", {})
            status = _STATUS_LABEL.get(rv.get("status", "unreviewed"), "—")
            print(
                f"  {p['profile']:<20} {p.get('num_clips', '?'):>6} "
                f"  {p.get('total_duration_sec', 0):>6.1f}s  {status}"
            )

    return 0


def _print_rankings_table(ranked: list[dict]) -> None:
    print()
    print(f"  {'Rk':<3} {'Profile':<22} {'Auto':>5} {'Final':>6} {'Rating':<7} {'Status':<16}")
    print("  " + "-" * 66)
    for r in ranked:
        rv = r.get("review", {})
        rating = rv.get("user_rating")
        status = rv.get("status", "unreviewed")
        emoji = _STATUS_EMOJI.get(status, "⬜")
        ok = "  " if r.get("_success", True) else "! "
        print(
            f"  {ok}{r['rank']:<3} {r['profile']:<22} "
            f"{r.get('auto_score', 0):>5.2f} {r.get('final_score', 0):>6.2f}"
            f"  {f'{rating}/5' if rating else '—  ':<7}"
            f"  {emoji} {_STATUS_LABEL.get(status, status)}"
        )
    print()


# ═══════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="evaluate_benchmark",
        description=textwrap.dedent("""\
            Evaluation layer for benchmark_render_styles.py outputs.

              evaluate   Score profiles → ranked_profiles.json + evaluation_summary.md
              review     Interactive rating session → updates report.json
              show       Print current rankings table
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)

    # ── evaluate ───────────────────────────────────────────────────────────
    ev = sub.add_parser("evaluate", help="Compute scores and write output files.")
    ev.add_argument("--report", "-r", required=True, metavar="JSON",
                    help="Path to benchmark report.json")
    ev.add_argument(
        "--weights", metavar="JSON",
        help=(
            "Override dimension weights as a JSON object. "
            "E.g.: '{\"avg_clip_quality\":0.4,\"pacing_similarity\":0.1}' "
            "(values are renormalised to sum to 1)."
        ),
    )
    ev.add_argument(
        "--learned-weights", metavar="JSON",
        help=(
            "Path to learned_weights.json produced by tune_profile_weights.py. "
            "Overrides --weights for global scoring and applies per-profile "
            "weights when available."
        ),
    )

    # ── review ─────────────────────────────────────────────────────────────
    rv = sub.add_parser("review", help="Interactively rate profiles.")
    rv.add_argument("--report", "-r", required=True, metavar="JSON",
                    help="Path to benchmark report.json")
    rv.add_argument(
        "--profiles", "-p", nargs="+",
        help="Specific profiles to review (default: all).",
    )

    # ── show ───────────────────────────────────────────────────────────────
    sh = sub.add_parser("show", help="Print current rankings table.")
    sh.add_argument("--report", "-r", required=True, metavar="JSON",
                    help="Path to benchmark report.json")

    return p


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    dispatch = {
        "evaluate": cmd_evaluate,
        "review":   cmd_review,
        "show":     cmd_show,
    }
    fn = dispatch.get(args.command)
    if fn is None:
        parser.print_help()
        return 1
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
