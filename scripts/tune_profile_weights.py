#!/usr/bin/env python3
"""Weight-tuning script for the evaluate_benchmark scoring system.

Reads one or more benchmark report.json files (and their sibling
ranked_profiles.json), collects all manually-rated profile outputs,
then fits non-negative dimension weights via Projected Gradient Descent.

Math
----
For each rated sample we have:
  d  — dimension-score vector (0–10 per dim)
  y  — user_rating * 2  (maps 1–5 → 2–10 to match auto_score scale)

We minimise  MSE(w) = (1/n) Σ (w·d_i − y_i)²

subject to:
  Σ w_j = 1
  LO ≤ w_j ≤ HI  (default 0.05 ≤ w_j ≤ 0.40)

Solver: Projected Gradient Descent with Lipschitz-optimal step size.
Projection: bisection search on the Lagrange multiplier for the
            bounded-simplex constraint.

Outputs
-------
  learned_weights.json   — global + per-profile weight dicts
  tuning_summary.md      — human-readable breakdown

Usage
-----
  python scripts/tune_profile_weights.py \\
      --reports ./benchmarks/report.json [more_report.json ...] \\
      --out     ./benchmarks/learned_weights.json \\
      [--min-rating 4] \\
      [--profile fashion_montage] \\
      [--dry-run]
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── constants ─────────────────────────────────────────────────────────────────

DIMS: list[str] = [
    "avg_clip_quality",
    "pacing_similarity",
    "visual_variety",
    "render_style_strength",
    "continuity",
    "profile_distinctiveness",
]

WEIGHT_LO: float = 0.05
WEIGHT_HI: float = 0.40

# Minimum samples required to attempt fitting; below this we fall back
MIN_SAMPLES_TO_FIT: int = 2

# Default weights (fallback when fitting is not possible)
_FALLBACK_WEIGHTS: dict[str, float] = {
    "avg_clip_quality":        0.25,
    "pacing_similarity":       0.20,
    "visual_variety":          0.20,
    "render_style_strength":   0.15,
    "continuity":              0.10,
    "profile_distinctiveness": 0.10,
}


# ═══════════════════════════════════════════════════════════════════════════
#  PURE-PYTHON OPTIMIZER
# ═══════════════════════════════════════════════════════════════════════════

def _project_bounded_simplex(
    v: list[float],
    lo: float = WEIGHT_LO,
    hi: float = WEIGHT_HI,
) -> list[float]:
    """Project vector v onto { w : Σwⱼ = 1, lo ≤ wⱼ ≤ hi }.

    Uses bisection on the Lagrange multiplier μ so that
    wⱼ = clip(vⱼ − μ, lo, hi)  and  Σwⱼ = 1.

    Time: O(n · log(1/ε)), Space: O(n).
    """
    n = len(v)
    if n == 0:
        return []

    # Feasibility guard
    if n * hi < 1.0 - 1e-9 or n * lo > 1.0 + 1e-9:
        # Infeasible bounds — return uniform
        return [1.0 / n] * n

    # σ(μ) = Σ clip(vⱼ − μ, lo, hi) is strictly decreasing in μ.
    # We want σ(μ) = 1.
    mu_lo = min(v) - hi   # σ(mu_lo) ≥ n*lo ≥ (if feasible) > 0
    mu_hi = max(v) - lo   # σ(mu_hi) ≤ n*hi

    mu = (mu_lo + mu_hi) / 2.0
    for _ in range(200):
        mu = (mu_lo + mu_hi) / 2.0
        s = sum(max(lo, min(hi, vj - mu)) for vj in v)
        if abs(s - 1.0) < 1e-12:
            break
        if s > 1.0:
            mu_lo = mu
        else:
            mu_hi = mu

    w = [max(lo, min(hi, vj - mu)) for vj in v]
    # Force exact sum-to-1 (floating-point cleanup)
    s = sum(w)
    return [x / s for x in w] if s > 0 else w


def _mse(D: list[list[float]], w: list[float], y: list[float]) -> float:
    n = len(D)
    if n == 0:
        return 0.0
    n_dims = len(w)
    total = 0.0
    for i in range(n):
        pred = sum(D[i][j] * w[j] for j in range(n_dims))
        total += (pred - y[i]) ** 2
    return total / n


def _grad(D: list[list[float]], w: list[float], y: list[float]) -> list[float]:
    """∂MSE/∂wⱼ = (2/n) Σᵢ Dᵢⱼ (w·dᵢ − yᵢ)."""
    n = len(D)
    n_dims = len(w)
    residuals = [sum(D[i][j] * w[j] for j in range(n_dims)) - y[i] for i in range(n)]
    return [
        2.0 * sum(D[i][j] * residuals[i] for i in range(n)) / n
        for j in range(n_dims)
    ]


def _pgd_fit(
    D: list[list[float]],
    y: list[float],
    lo: float = WEIGHT_LO,
    hi: float = WEIGHT_HI,
    max_iter: int = 6000,
) -> tuple[list[float], float]:
    """Projected Gradient Descent on the bounded simplex.

    D  — n_samples × n_dims matrix, values on the 0–10 auto-score scale.
    y  — n_samples target vector (user_rating * 2, range 2–10).

    Returns (weights, final_mse).
    """
    n_samples, n_dims = len(D), len(D[0]) if D else 0
    if n_dims == 0 or n_samples == 0:
        return [1.0 / len(DIMS)] * len(DIMS), float("inf")

    # Lipschitz-optimal step: α = 1 / (2 * max_j Σᵢ Dᵢⱼ² / n)
    col_sq = [
        sum(D[i][j] ** 2 for i in range(n_samples)) / n_samples
        for j in range(n_dims)
    ]
    L = 2.0 * max(col_sq) if col_sq else 1.0
    lr = 1.0 / max(L, 1e-9)

    # Uniform initialisation (always feasible)
    w = [1.0 / n_dims] * n_dims
    best_w = w[:]
    best_loss = _mse(D, w, y)

    for _ in range(max_iter):
        g = _grad(D, w, y)
        w_new = [w[j] - lr * g[j] for j in range(n_dims)]
        w = _project_bounded_simplex(w_new, lo, hi)
        loss = _mse(D, w, y)
        if loss < best_loss:
            best_loss = loss
            best_w = w[:]

    return best_w, best_loss


def _normalise_weights(raw: list[float], dims: list[str]) -> dict[str, float]:
    """Return {dim: weight} dict guaranteed to sum to 1.0."""
    s = sum(raw)
    if s <= 0:
        return {d: 1.0 / len(dims) for d in dims}
    return {d: round(v / s, 6) for d, v in zip(dims, raw)}


# ═══════════════════════════════════════════════════════════════════════════
#  DATA LOADING
# ═══════════════════════════════════════════════════════════════════════════

def _load_evaluate_module():
    """Import evaluate_benchmark.py from the scripts/ sibling directory."""
    ev_path = Path(__file__).parent / "evaluate_benchmark.py"
    if not ev_path.exists():
        return None
    spec = importlib.util.spec_from_file_location("_evaluate_benchmark", ev_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _compute_dims_fallback(profile_entry: dict, all_entries: list[dict], base_fp: dict) -> dict[str, float]:
    """Compute dimension scores when ranked_profiles.json is absent.

    Imports scoring functions from evaluate_benchmark.py; returns an empty
    dict if that module is unavailable.
    """
    ev = _load_evaluate_module()
    if ev is None:
        return {}
    try:
        return ev._compute_dimensions(profile_entry, all_entries, base_fp)
    except Exception:
        return {}


Sample = dict[str, Any]  # {profile, dimensions, user_rating, status, source}


def collect_samples(
    report_paths: list[Path],
    min_rating: int = 1,
    profile_filter: str | None = None,
) -> list[Sample]:
    """Load rated samples from one or more report.json paths.

    For each report, prefers the sibling ranked_profiles.json for precomputed
    dimension scores. Falls back to recomputing from report.json + evaluate_benchmark.py.
    """
    samples: list[Sample] = []

    for rpath in report_paths:
        rpath = rpath.expanduser().resolve()
        if not rpath.exists():
            print(f"  WARN: report not found: {rpath}", file=sys.stderr)
            continue

        ranked_path = rpath.parent / "ranked_profiles.json"

        if ranked_path.exists():
            doc = json.loads(ranked_path.read_text(encoding="utf-8"))
            for entry in doc.get("ranked", []):
                profile = entry.get("profile", "")
                if profile_filter and profile != profile_filter:
                    continue
                rv = entry.get("review", {})
                rating = rv.get("user_rating")
                if rating is None or int(rating) < min_rating:
                    continue
                dims = {k: float(v) for k, v in entry.get("dimension_scores", {}).items()
                        if k in DIMS}
                if not dims:
                    continue
                samples.append({
                    "profile":     profile,
                    "dimensions":  dims,
                    "user_rating": int(rating),
                    "status":      rv.get("status", "unreviewed"),
                    "source":      str(rpath),
                })
        else:
            # Recompute dimensions from raw report.json
            report = json.loads(rpath.read_text(encoding="utf-8"))
            base_fp = report.get("base_fingerprint", {})
            all_entries = [p for p in report.get("profiles", []) if p.get("success")]
            for p in report.get("profiles", []):
                profile = p.get("profile", "")
                if profile_filter and profile != profile_filter:
                    continue
                rv = p.get("review", {})
                rating = rv.get("user_rating")
                if rating is None or int(rating) < min_rating:
                    continue
                dims = _compute_dims_fallback(p, all_entries, base_fp)
                if not dims:
                    print(f"  WARN: could not compute dimensions for {profile} "
                          f"in {rpath.name}. Run `evaluate_benchmark evaluate` first.",
                          file=sys.stderr)
                    continue
                samples.append({
                    "profile":     profile,
                    "dimensions":  dims,
                    "user_rating": int(rating),
                    "status":      rv.get("status", "unreviewed"),
                    "source":      str(rpath),
                })

    return samples


# ═══════════════════════════════════════════════════════════════════════════
#  FIT LOGIC
# ═══════════════════════════════════════════════════════════════════════════

FitResult = dict[str, Any]   # {weights, mse, n_samples, converged, note}


def _samples_to_matrices(
    samples: list[Sample],
) -> tuple[list[list[float]], list[float]]:
    """Convert samples to (D, y) for _pgd_fit.

    D  : n × n_dims, dimension scores (0–10 scale)
    y  : n-vector, user_rating * 2  (2–10 scale)
    """
    D = [
        [s["dimensions"].get(d, 0.0) for d in DIMS]
        for s in samples
    ]
    y = [s["user_rating"] * 2.0 for s in samples]
    return D, y


def fit_weights(
    samples: list[Sample],
    lo: float = WEIGHT_LO,
    hi: float = WEIGHT_HI,
    label: str = "global",
) -> FitResult:
    """Fit dimension weights from a set of rated samples.

    Returns a FitResult dict; if there are too few samples the fallback
    weights are returned with converged=False.
    """
    n = len(samples)
    if n < MIN_SAMPLES_TO_FIT:
        return {
            "weights":   copy.copy(_FALLBACK_WEIGHTS),
            "mse":       None,
            "n_samples": n,
            "converged": False,
            "note":      f"Too few samples ({n} < {MIN_SAMPLES_TO_FIT}); using defaults.",
        }

    # Warn if all ratings are identical — regression is undefined
    ratings = [s["user_rating"] for s in samples]
    if len(set(ratings)) == 1:
        return {
            "weights":   copy.copy(_FALLBACK_WEIGHTS),
            "mse":       None,
            "n_samples": n,
            "converged": False,
            "note":      "All ratings are identical — cannot fit. Using defaults.",
        }

    D, y = _samples_to_matrices(samples)
    raw_w, mse = _pgd_fit(D, y, lo=lo, hi=hi)
    weights = _normalise_weights(raw_w, DIMS)

    return {
        "weights":   weights,
        "mse":       round(mse, 6),
        "n_samples": n,
        "converged": True,
        "note":      f"Fitted from {n} sample(s). Final MSE={mse:.4f}.",
    }


def fit_all(
    samples: list[Sample],
    lo: float = WEIGHT_LO,
    hi: float = WEIGHT_HI,
) -> tuple[FitResult, dict[str, FitResult]]:
    """Return (global_result, {profile: FitResult})."""
    global_result = fit_weights(samples, lo=lo, hi=hi, label="global")

    profiles_seen = sorted({s["profile"] for s in samples})
    per_profile: dict[str, FitResult] = {}
    for p in profiles_seen:
        p_samples = [s for s in samples if s["profile"] == p]
        per_profile[p] = fit_weights(p_samples, lo=lo, hi=hi, label=p)

    return global_result, per_profile


# ═══════════════════════════════════════════════════════════════════════════
#  OUTPUT BUILDERS
# ═══════════════════════════════════════════════════════════════════════════

def build_learned_weights_doc(
    global_result: FitResult,
    per_profile: dict[str, FitResult],
    min_rating: int,
    profile_filter: str | None,
    lo: float,
    hi: float,
    report_paths: list[Path],
) -> dict:
    return {
        "schema_version":  "1.0",
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "sources":         [str(p) for p in report_paths],
        "config": {
            "min_rating":      min_rating,
            "profile_filter":  profile_filter,
            "weight_lo":       lo,
            "weight_hi":       hi,
        },
        "global": global_result["weights"] if global_result["converged"] else None,
        "per_profile": {
            p: res["weights"]
            for p, res in per_profile.items()
            if res["converged"]
        },
        "fit_stats": {
            "global": {
                k: v for k, v in global_result.items() if k != "weights"
            },
            "per_profile": {
                p: {k: v for k, v in res.items() if k != "weights"}
                for p, res in per_profile.items()
            },
        },
    }


def build_tuning_summary(
    global_result: FitResult,
    per_profile: dict[str, FitResult],
    samples: list[Sample],
    min_rating: int,
    profile_filter: str | None,
    lo: float,
    hi: float,
    report_paths: list[Path],
    out_path: Path,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []

    lines += [
        "# Profile Weight Tuning Summary",
        "",
        f"Generated: {now}  ",
        f"Sources: {', '.join(f'`{p.name}`' for p in report_paths)}  ",
        f"Min rating: **{min_rating}** / 5  ",
        f"Profile filter: **{profile_filter or 'all'}**  ",
        f"Weight bounds: [{lo}, {hi}]",
        "",
        "---",
        "",
    ]

    # ── Training data ──────────────────────────────────────────────────────
    lines += ["## Training Data", ""]
    profile_counts: dict[str, int] = {}
    rating_dist: dict[int, int] = {}
    for s in samples:
        profile_counts[s["profile"]] = profile_counts.get(s["profile"], 0) + 1
        rating_dist[s["user_rating"]] = rating_dist.get(s["user_rating"], 0) + 1

    lines += [
        f"- Total rated samples: **{len(samples)}**",
        f"- Profiles with data:  {', '.join(f'`{p}` ({n})' for p, n in sorted(profile_counts.items()))}",
        "- Rating distribution: "
        + ", ".join(f"{r}★={c}" for r, c in sorted(rating_dist.items())),
        "",
    ]

    # ── Global weights ─────────────────────────────────────────────────────
    lines += ["## Global Learned Weights", ""]
    if global_result["converged"]:
        gw = global_result["weights"]
        lines += [
            f"Fitted from **{global_result['n_samples']}** samples.  ",
            f"Final MSE = `{global_result['mse']:.5f}`",
            "",
            "| Dimension | Learned Weight | Default Weight | Δ |",
            "|---|---|---|---|",
        ]
        for d in DIMS:
            lw = gw.get(d, 0.0)
            dw = _FALLBACK_WEIGHTS.get(d, 0.0)
            delta = lw - dw
            arrow = "▲" if delta > 0.005 else ("▼" if delta < -0.005 else "≈")
            lines.append(
                f"| {d.replace('_', ' ').title()} "
                f"| **{lw:.4f}** | {dw:.4f} | {arrow} {delta:+.4f} |"
            )
        # Highlight top dimension
        top_dim = max(gw, key=gw.get)
        lines += [
            "",
            f"> **Most important dimension**: `{top_dim}` ({gw[top_dim]:.4f})",
            "",
        ]
    else:
        lines += [
            f"> ⚠️ {global_result['note']}",
            "> Falling back to default weights.",
            "",
        ]

    # ── Per-profile weights ────────────────────────────────────────────────
    lines += ["## Per-Profile Learned Weights", ""]
    if per_profile:
        # Header row
        lines += [
            "| Profile | " + " | ".join(d.replace("_", " ").title() for d in DIMS) + " | n | MSE |",
            "|---|" + "|".join(["---"] * len(DIMS)) + "|---|---|",
        ]
        for profile in sorted(per_profile.keys()):
            res = per_profile[profile]
            if res["converged"]:
                w_cells = " | ".join(f"{res['weights'].get(d, 0):.3f}" for d in DIMS)
                lines.append(
                    f"| {profile} | {w_cells} | {res['n_samples']} | {res['mse']:.4f} |"
                )
            else:
                lines.append(
                    f"| {profile} | _(fallback)_ "
                    + "| " * len(DIMS)
                    + f"| {res['n_samples']} | — |"
                )
        lines.append("")
    else:
        lines += ["> No per-profile data available.", ""]

    # ── How to use ─────────────────────────────────────────────────────────
    lines += [
        "## Usage",
        "",
        "Pass the learned weights to `evaluate_benchmark.py` via `--learned-weights`:",
        "",
        "```bash",
        f"python scripts/evaluate_benchmark.py evaluate \\",
        f"    --report ./benchmarks/report.json \\",
        f"    --learned-weights {out_path}",
        "```",
        "",
        "Global weights are used for cross-profile ranking.  ",
        "Per-profile weights are applied per-entry when available,  ",
        "making the within-profile quality score more representative.",
        "",
        "---",
        "",
        "## Methodology",
        "",
        "Weights are fitted by Projected Gradient Descent on the bounded simplex:",
        "",
        "```",
        "minimise  (1/n) Σ (w·dᵢ − yᵢ)²",
        "subject to  Σwⱼ = 1,  lo ≤ wⱼ ≤ hi",
        "",
        "where  dᵢ = dimension scores (0–10)  for sample i",
        "       yᵢ = user_rating × 2  (maps 1–5 → 2–10)",
        "```",
    ]

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tune_profile_weights",
        description=textwrap.dedent("""\
            Learn dimension weights from manually-reviewed benchmark reports.

            Reads one or more report.json files (and their sibling
            ranked_profiles.json), then fits non-negative weights via
            Projected Gradient Descent to reproduce the manual ratings.
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--reports", "-r",
        required=True,
        nargs="+",
        metavar="JSON",
        help="One or more benchmark report.json paths to learn from.",
    )
    p.add_argument(
        "--out", "-o",
        default="learned_weights.json",
        metavar="JSON",
        help="Output path for learned_weights.json. (default: ./learned_weights.json)",
    )
    p.add_argument(
        "--min-rating",
        type=int,
        default=1,
        metavar="N",
        choices=range(1, 6),
        help="Only include samples with user_rating ≥ N. (default: 1 = all rated)",
    )
    p.add_argument(
        "--profile",
        default=None,
        metavar="NAME",
        help="Restrict fitting to a single profile (e.g. fashion_montage).",
    )
    p.add_argument(
        "--weight-lo",
        type=float,
        default=WEIGHT_LO,
        metavar="F",
        help=f"Lower bound for each weight. (default: {WEIGHT_LO})",
    )
    p.add_argument(
        "--weight-hi",
        type=float,
        default=WEIGHT_HI,
        metavar="F",
        help=f"Upper bound for each weight. (default: {WEIGHT_HI})",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the fitted weights but do not write any files.",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print each training sample.",
    )
    return p


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    report_paths = [Path(r) for r in args.reports]
    out_path = Path(args.out).expanduser().resolve()
    lo, hi = args.weight_lo, args.weight_hi

    if lo < 0 or hi > 1 or lo >= hi:
        print(f"ERROR: invalid weight bounds [{lo}, {hi}].", file=sys.stderr)
        return 2
    if (len(DIMS) * lo > 1.0) or (len(DIMS) * hi < 1.0):
        print(
            f"ERROR: bounds [{lo}, {hi}] with {len(DIMS)} dimensions cannot sum to 1.",
            file=sys.stderr,
        )
        return 2

    print(f"\nCollecting samples (min_rating={args.min_rating}"
          f"{f', profile={args.profile}' if args.profile else ''})…")

    samples = collect_samples(report_paths, min_rating=args.min_rating, profile_filter=args.profile)

    if not samples:
        print("ERROR: no rated samples found. "
              "Run `evaluate_benchmark review` to add ratings first.", file=sys.stderr)
        return 1

    print(f"  Found {len(samples)} rated sample(s) across "
          f"{len({s['profile'] for s in samples})} profile(s).")

    if args.verbose:
        for s in samples:
            dims_str = "  ".join(f"{d[:6]}={s['dimensions'].get(d, 0):.1f}" for d in DIMS)
            print(f"    [{s['profile']:<20}] rating={s['user_rating']}  {dims_str}")

    # ── Fit ────────────────────────────────────────────────────────────────
    print("\nFitting weights…")
    global_result, per_profile = fit_all(samples, lo=lo, hi=hi)

    # ── Print results ──────────────────────────────────────────────────────
    _print_results(global_result, per_profile)

    if args.dry_run:
        print("\n[dry-run] No files written.")
        return 0

    # ── Write output ───────────────────────────────────────────────────────
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = build_learned_weights_doc(
        global_result, per_profile, args.min_rating, args.profile,
        lo, hi, report_paths,
    )
    out_path.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote: {out_path}")

    summary_path = out_path.parent / "tuning_summary.md"
    md = build_tuning_summary(
        global_result, per_profile, samples, args.min_rating, args.profile,
        lo, hi, report_paths, out_path,
    )
    summary_path.write_text(md, encoding="utf-8")
    print(f"Wrote: {summary_path}")

    return 0


def _print_results(global_result: FitResult, per_profile: dict[str, FitResult]) -> None:
    print()
    print("  GLOBAL WEIGHTS")
    print("  " + "-" * 44)
    if global_result["converged"]:
        gw = global_result["weights"]
        for d in DIMS:
            bar = "█" * int(gw.get(d, 0) * 100 // 4)
            print(f"  {d:<26}  {gw.get(d, 0):.4f}  {bar}")
        print(f"\n  MSE = {global_result['mse']:.5f}  (n={global_result['n_samples']})")
    else:
        print(f"  ⚠ {global_result['note']}")

    if per_profile:
        print()
        print("  PER-PROFILE WEIGHTS")
        print("  " + "-" * 44)
        for profile, res in sorted(per_profile.items()):
            if res["converged"]:
                top = max(res["weights"], key=res["weights"].get)
                print(f"  {profile:<22}  top dim: {top}  (n={res['n_samples']})")
            else:
                print(f"  {profile:<22}  fallback  ({res['note']})")
    print()


if __name__ == "__main__":
    sys.exit(main())
