#!/usr/bin/env python3
"""Visualize beat grid, cut points, intensity, and pacing curve for a timeline.

Usage
-----
    python scripts/plot_timeline_rhythm.py \\
        --timeline  path/to/timeline.json \\
        --fingerprint path/to/fingerprint.json \\
        --out       timeline_rhythm.png

The output PNG contains six stacked panels:

1. Beat markers      — vertical lines at every beat timestamp
2. Cut markers       — vertical lines at each clip boundary
3. Clip intensity    — bar chart; one bar per clip, height = intensity score
4. Pacing curve      — smoothed clip-intensity line (sliding window average)
5. Climax regions    — highlighted phrase boundary zones
6. Caption density   — histogram of caption start times

Gracefully degrades if matplotlib is not installed (prints a message and exits 0).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ── Ensure repo root is importable ───────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _check_matplotlib() -> bool:
    try:
        import matplotlib  # noqa: F401
        return True
    except ImportError:
        return False


def _load_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _clip_intensities(clips: list[dict]) -> list[float]:
    intensities: list[float] = []
    for c in clips:
        sm = c.get("selection_metadata") or {}
        if isinstance(sm, dict):
            sb = sm.get("score_breakdown") or {}
            intensities.append(float(sb.get("intensity", 0.5) if isinstance(sb, dict) else 0.5))
        else:
            intensities.append(0.5)
    return intensities


def plot_timeline_rhythm(
    timeline: dict,
    fingerprint: dict,
    out_path: str,
) -> None:
    """Generate the timeline-rhythm visualization PNG."""
    import matplotlib
    matplotlib.use("Agg")   # non-interactive backend
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import numpy as np

    # ── Extract data ──────────────────────────────────────────────────────
    tracks = timeline.get("tracks", {})
    video_clips = tracks.get("video", [])
    text_caps   = tracks.get("text", [])

    # Also accept flat list of clips stored directly on timeline
    if not video_clips and "clips" in timeline:
        video_clips = timeline["clips"]
    if not text_caps and "captions" in timeline:
        text_caps = timeline["captions"]

    # Convert EditTimeline-schema clips to canonical dicts if needed
    clip_list: list[dict] = []
    for c in video_clips:
        if "timeline_in" in c:
            clip_list.append({
                "start": c.get("timeline_in", c.get("start", 0)),
                "end":   c.get("timeline_out", c.get("end", 0)),
                "selection_metadata": c.get("selection_metadata"),
            })
        else:
            clip_list.append(c)

    cap_list: list[dict] = []
    for cap in text_caps:
        cap_list.append({
            "start": cap.get("start", 0),
        })

    clip_starts = [c["start"] for c in clip_list]
    clip_ends   = [c["end"]   for c in clip_list]
    cap_starts  = [c["start"] for c in cap_list]

    beat_grid         = fingerprint.get("beat_grid") or fingerprint.get("beat_points") or []
    downbeats         = fingerprint.get("downbeats", [])
    phrase_boundaries = fingerprint.get("phrase_boundaries", [])
    energy_curve      = fingerprint.get("energy_curve", [])

    # Pacing curve from pacing_metadata (set by EditPlanner) or from scratch
    pacing_meta  = timeline.get("pacing_metadata", {})
    pacing_curve = pacing_meta.get("pacing_curve", [])
    if not pacing_curve:
        try:
            from app.services.music_analysis import compute_pacing_curve
            intensities = _clip_intensities(clip_list)
            durs = [c["end"] - c["start"] for c in clip_list]
            pacing_curve = compute_pacing_curve(durs, intensities)
        except Exception:
            pacing_curve = []

    total_dur = clip_ends[-1] if clip_ends else 60.0

    # ── Layout ────────────────────────────────────────────────────────────
    fig, axes = plt.subplots(
        6, 1,
        figsize=(18, 14),
        gridspec_kw={"height_ratios": [1, 1, 2, 2, 1, 1.5]},
        sharex=True,
    )
    fig.patch.set_facecolor("#0d0d0d")
    for ax in axes:
        ax.set_facecolor("#1a1a1a")
        ax.tick_params(colors="#aaaaaa", labelsize=8)
        for spine in ax.spines.values():
            spine.set_edgecolor("#333333")

    t_max = max(total_dur, beat_grid[-1] if beat_grid else total_dur, 1.0)

    # ── Panel 1: Beat markers ─────────────────────────────────────────────
    ax1 = axes[0]
    ax1.set_xlim(0, t_max)
    ax1.set_ylim(0, 1)
    ax1.set_ylabel("Beats", color="#aaaaaa", fontsize=8)
    for bt in beat_grid:
        ax1.axvline(bt, color="#4466ff", lw=0.4, alpha=0.6)
    for db in downbeats:
        ax1.axvline(db, color="#6699ff", lw=0.8, alpha=0.9)
    ax1.set_yticks([])
    ax1.set_title("Beat Grid  (blue=beats, bright=downbeats)", color="#cccccc", fontsize=9, loc="left", pad=3)

    # ── Panel 2: Cut markers ──────────────────────────────────────────────
    ax2 = axes[1]
    ax2.set_ylim(0, 1)
    ax2.set_ylabel("Cuts", color="#aaaaaa", fontsize=8)
    for cs in clip_starts:
        ax2.axvline(cs, color="#ff8844", lw=1.0, alpha=0.9)
    ax2.set_yticks([])
    ax2.set_title("Cut Markers", color="#cccccc", fontsize=9, loc="left", pad=3)

    # ── Panel 3: Clip intensity (bar chart) ───────────────────────────────
    ax3 = axes[2]
    ax3.set_ylim(0, 1.1)
    ax3.set_ylabel("Intensity", color="#aaaaaa", fontsize=8)
    if clip_list:
        intensities = _clip_intensities(clip_list)
        for c, iv in zip(clip_list, intensities):
            width = c["end"] - c["start"]
            ax3.bar(c["start"], iv, width=width, align="edge",
                    color="#44cc88", alpha=0.75, edgecolor="#22aa66", linewidth=0.5)
    ax3.set_title("Clip Intensity (per-clip energy)", color="#cccccc", fontsize=9, loc="left", pad=3)

    # ── Panel 4: Pacing curve ─────────────────────────────────────────────
    ax4 = axes[3]
    ax4.set_ylim(0, 1.1)
    ax4.set_ylabel("Pacing", color="#aaaaaa", fontsize=8)
    if pacing_curve and clip_list:
        # Map curve values to clip midpoints
        midpoints = [(c["start"] + c["end"]) / 2 for c in clip_list[: len(pacing_curve)]]
        ax4.plot(midpoints, pacing_curve[:len(midpoints)], color="#ffcc44", lw=1.5)
        ax4.fill_between(midpoints, pacing_curve[:len(midpoints)], alpha=0.2, color="#ffcc44")
    elif energy_curve and beat_grid:
        ts = beat_grid[: len(energy_curve)]
        ax4.plot(ts, energy_curve[: len(ts)], color="#ffcc44", lw=1.2, linestyle="--")
    ax4.set_title("Pacing Curve (smoothed intensity)", color="#cccccc", fontsize=9, loc="left", pad=3)

    # ── Panel 5: Climax regions (phrase boundaries) ───────────────────────
    ax5 = axes[4]
    ax5.set_ylim(0, 1)
    ax5.set_yticks([])
    ax5.set_ylabel("Phrases", color="#aaaaaa", fontsize=8)
    phrase_half_w = 0.5   # seconds; highlight ±0.5s around each boundary
    for pb in phrase_boundaries:
        ax5.axvspan(pb - phrase_half_w, pb + phrase_half_w,
                    color="#cc44ff", alpha=0.35, zorder=2)
        ax5.axvline(pb, color="#cc44ff", lw=1.2, alpha=0.9, zorder=3)
    ax5.set_title("Phrase Boundaries (climax regions)", color="#cccccc", fontsize=9, loc="left", pad=3)

    # ── Panel 6: Caption density ──────────────────────────────────────────
    ax6 = axes[5]
    ax6.set_ylabel("Captions", color="#aaaaaa", fontsize=8)
    ax6.set_xlabel("Timeline (seconds)", color="#aaaaaa", fontsize=8)
    if cap_starts:
        bins = max(10, int(t_max / 2))
        ax6.hist(cap_starts, bins=bins, range=(0, t_max),
                 color="#ff6688", alpha=0.75, edgecolor="#cc4466", linewidth=0.5)
    ax6.set_title("Caption Density", color="#cccccc", fontsize=9, loc="left", pad=3)

    # ── Legend / footer ───────────────────────────────────────────────────
    legend_items = [
        mpatches.Patch(color="#4466ff", label="Beat"),
        mpatches.Patch(color="#6699ff", label="Downbeat"),
        mpatches.Patch(color="#ff8844", label="Cut"),
        mpatches.Patch(color="#44cc88", label="Clip intensity"),
        mpatches.Patch(color="#ffcc44", label="Pacing curve"),
        mpatches.Patch(color="#cc44ff", label="Phrase boundary"),
        mpatches.Patch(color="#ff6688", label="Caption"),
    ]
    bpm = fingerprint.get("tempo_bpm", "?")
    escalation = pacing_meta.get("escalation_score", "?")
    beat_count  = len(beat_grid)
    fig.legend(
        handles=legend_items,
        loc="lower center",
        ncol=7,
        framealpha=0.2,
        facecolor="#1a1a1a",
        edgecolor="#333333",
        labelcolor="#cccccc",
        fontsize=8,
    )
    fig.suptitle(
        f"Timeline Rhythm  |  BPM={bpm}  Beats={beat_count}  "
        f"Clips={len(clip_list)}  EscalationScore={escalation}",
        color="#dddddd",
        fontsize=10,
        y=0.99,
    )

    plt.tight_layout(rect=[0, 0.05, 1, 0.98])
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"Saved: {out_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Plot beat-aware timeline rhythm visualization."
    )
    parser.add_argument(
        "--timeline", required=True,
        help="Path to a timeline JSON file (EditTimeline.to_render_spec() output or raw EditTimeline model_dump).",
    )
    parser.add_argument(
        "--fingerprint", required=True,
        help="Path to a reference fingerprint JSON file.",
    )
    parser.add_argument(
        "--out", default="timeline_rhythm.png",
        help="Output PNG path (default: timeline_rhythm.png).",
    )
    args = parser.parse_args(argv)

    if not _check_matplotlib():
        print(
            "matplotlib is not installed.  Install it with:\n"
            "    pip install matplotlib\n"
            "Skipping visualization.",
            file=sys.stderr,
        )
        return 0

    timeline    = _load_json(args.timeline)
    fingerprint = _load_json(args.fingerprint)
    plot_timeline_rhythm(timeline, fingerprint, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
