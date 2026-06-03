#!/usr/bin/env python3
"""Visual QA benchmark — compare render outputs across all ranking profiles.

Usage
-----
    python scripts/benchmark_render_styles.py \\
        --reference  path/to/reference.mp4 \\
        --footage    ./clips \\
        --out        ./benchmarks

What it does
------------
For each of the six ranking profiles (fashion_montage, music_video,
talking_head, product_showcase, travel_reel, vlog):

1. Runs the full pipeline (reference analysis → footage analysis →
   edit planning → preview render) with the profile forced into the
   fingerprint.
2. Saves a 480p preview MP4 under <out>/<profile>/preview.mp4.
3. Extracts 6 evenly-spaced key frames and saves a side-by-side PNG
   contact sheet: <out>/<profile>/contact_sheet.png.
4. Writes a cross-profile combined sheet: <out>/combined_sheet.png.
5. Saves a JSON report: <out>/report.json  containing per-profile:
   - selected clips (asset_id, source_in/out, duration)
   - score breakdowns from selection_metadata
   - render_style dict
   - output paths

Exit codes: 0 = all profiles rendered, non-zero = at least one failed.
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# ── ensure repo root is on the path ──────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_REPO_ROOT / ".env", override=False)
except ImportError:
    pass

logger = logging.getLogger("benchmark")

PROFILES = [
    "fashion_montage",
    "music_video",
    "talking_head",
    "product_showcase",
    "travel_reel",
    "vlog",
]

# Number of key frames extracted per profile for the contact sheet
FRAMES_PER_PROFILE = 6


# ═══════════════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
        level=level,
    )


def _collect_footage(footage_dir: Path) -> list[Path]:
    extensions = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
    clips = sorted(
        p for p in footage_dir.rglob("*") if p.suffix.lower() in extensions
    )
    if not clips:
        raise FileNotFoundError(f"No video files found in {footage_dir}")
    return clips


def _ffmpeg_extract_frame(video_path: Path, out_path: Path, timestamp: float) -> bool:
    """Extract a single JPEG frame at *timestamp* seconds. Returns True on success."""
    from app.config import get_settings
    ffmpeg = get_settings().ffmpeg_binary
    cmd = [
        ffmpeg, "-y",
        "-ss", f"{timestamp:.3f}",
        "-i", str(video_path),
        "-frames:v", "1",
        "-q:v", "3",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=30)
    return result.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0


def _get_video_duration(video_path: Path) -> float:
    """Return duration in seconds using ffprobe."""
    from app.config import get_settings
    ffprobe = get_settings().ffprobe_binary
    cmd = [
        ffprobe, "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return 0.0
    data = json.loads(result.stdout)
    return float(data.get("format", {}).get("duration", 0.0))


def _extract_key_frames(
    video_path: Path,
    out_dir: Path,
    n: int = FRAMES_PER_PROFILE,
) -> list[Path]:
    """Extract *n* evenly-spaced frames. Returns paths to successfully extracted frames."""
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = _get_video_duration(video_path)
    if duration <= 0:
        return []

    # Sample from 5 % to 95 % of the video to avoid black-frame bookends
    start_frac, end_frac = 0.05, 0.95
    step = (end_frac - start_frac) / max(n - 1, 1)
    timestamps = [start_frac * duration + i * step * duration for i in range(n)]

    frames: list[Path] = []
    for idx, ts in enumerate(timestamps):
        out = out_dir / f"frame_{idx:02d}.jpg"
        if _ffmpeg_extract_frame(video_path, out, ts):
            frames.append(out)
    return frames


def _build_contact_sheet(
    frames: list[Path],
    out_path: Path,
    title: str,
    thumb_w: int = 320,
    thumb_h: int = 569,  # 9:16 thumbnail
) -> Path:
    """Arrange *frames* side-by-side with a title bar. Writes a PNG."""
    from PIL import Image, ImageDraw, ImageFont

    n = len(frames)
    if n == 0:
        # Write a blank placeholder
        img = Image.new("RGB", (thumb_w, thumb_h + 40), color=(30, 30, 30))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(out_path))
        return out_path

    title_bar_h = 44
    sheet_w = thumb_w * n
    sheet_h = thumb_h + title_bar_h
    sheet = Image.new("RGB", (sheet_w, sheet_h), color=(15, 15, 15))

    # Title bar
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 22)
    except (OSError, AttributeError):
        font = ImageFont.load_default()
    draw.text((12, 10), title.upper(), fill=(220, 220, 220), font=font)

    for i, fpath in enumerate(frames):
        try:
            thumb = Image.open(str(fpath)).convert("RGB")
            thumb = thumb.resize((thumb_w, thumb_h), Image.LANCZOS)
            sheet.paste(thumb, (i * thumb_w, title_bar_h))
        except Exception:
            # Leave blank slot on failure
            pass

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(str(out_path))
    return out_path


def _build_combined_sheet(
    profile_sheets: dict[str, Path],
    out_path: Path,
) -> Path:
    """Stack all per-profile contact sheets vertically into one comparison image."""
    from PIL import Image

    images: list[Image.Image] = []
    for profile in PROFILES:
        if profile in profile_sheets and profile_sheets[profile].exists():
            try:
                images.append(Image.open(str(profile_sheets[profile])).convert("RGB"))
            except Exception:
                pass

    if not images:
        return out_path

    max_w = max(img.width for img in images)
    total_h = sum(img.height for img in images)
    combined = Image.new("RGB", (max_w, total_h), color=(10, 10, 10))

    y = 0
    for img in images:
        combined.paste(img, (0, y))
        y += img.height

    out_path.parent.mkdir(parents=True, exist_ok=True)
    combined.save(str(out_path))
    return out_path


# ═══════════════════════════════════════════════════════════════════════════
#  PIPELINE
# ═══════════════════════════════════════════════════════════════════════════

def _analyze_reference(
    reference_mp4: Path,
    llm,
    embedding_service=None,
) -> dict[str, Any]:
    """Run ReferenceAnalyzer.analyze_file() once and return the fingerprint.

    When *embedding_service* is provided, the CLIP reference embedding is
    computed while the video file is on disk and stored in the fingerprint
    under ``_ref_embedding``.
    """
    from app.services.reference_analyzer import ReferenceAnalyzer
    logger.info("Analyzing reference: %s", reference_mp4.name)
    return ReferenceAnalyzer(llm).analyze_file(
        str(reference_mp4), embedding_service=embedding_service
    )


def _analyze_footage(
    footage_paths: list[Path],
    embedding_service=None,
) -> list[dict[str, Any]]:
    """Run FootageAnalyzer once over all clips.

    When *embedding_service* is provided, every usable segment is enriched
    with a ``_embedding`` key for CLIP-based semantic_fit scoring.
    """
    from app.services.footage_analyzer import FootageAnalyzer
    logger.info("Analyzing %d footage clip(s)…", len(footage_paths))
    index = FootageAnalyzer().analyze_all(footage_paths)
    if embedding_service is not None:
        embedding_service.enrich_footage_index(index)
        embedded = sum(
            1
            for clip in index
            for seg in (clip.get("usable_segments") or clip.get("moments") or [])
            if "_embedding" in seg
        )
        logger.info("Footage embeddings: %d segment(s) enriched", embedded)
    return index


def _run_profile(
    profile: str,
    base_fingerprint: dict[str, Any],
    footage_index: list[dict[str, Any]],
    llm,
    out_dir: Path,
    content_hint: str,
    no_captions: bool,
) -> dict[str, Any]:
    """
    Run the edit + render pipeline for one profile.
    Returns a result dict with keys: profile, success, output_path,
    contact_sheet_path, timeline_clips, render_style, error, duration_sec.
    """
    from app.services.edit_planner import EditPlanner
    from app.services.reference_analyzer import infer_style_profile
    from app.services.style_renderer import StyleRenderer
    from app.services.render_engine import RenderEngine
    from app.config import get_settings

    s = get_settings()
    t0 = time.monotonic()

    # -- 1. Clone fingerprint and override profile --------------------------
    fp = copy.deepcopy(base_fingerprint)
    fp["ranking_profile"] = profile
    # Re-derive inferred fields (faces_central, subject_focus, style_tags …)
    fp.update(infer_style_profile(fp))

    profile_dir = out_dir / profile
    profile_dir.mkdir(parents=True, exist_ok=True)

    try:
        # -- 2. Plan timeline ----------------------------------------------
        max_shots = min(40, max(15, fp.get("num_cuts", 14) + 1))
        planner = EditPlanner(llm) if not no_captions else _NoCaptionPlanner(llm)
        timeline = planner.plan(
            fp, footage_index,
            content_hint=content_hint,
            max_shots=max_shots,
            project_id=f"bench_{profile}",
            width=s.export_width,
            height=s.export_height,
            fps=s.export_fps,
        )

        # -- 3. Apply render_style from StyleRenderer ----------------------
        timeline.render_style = StyleRenderer.from_fingerprint(fp)

        # -- 4. Render preview ---------------------------------------------
        with tempfile.TemporaryDirectory(prefix=f"bench_{profile}_") as tmp:
            asset_map = {
                e["asset_id"]: e.get("file_path", e.get("path", ""))
                for e in footage_index
            }
            engine = RenderEngine(
                asset_resolver=lambda aid, _m=asset_map: _m.get(aid, aid),
                work_dir=Path(tmp),
            )
            result = engine.render_timeline(timeline, preview=True)
            preview_src = Path(result["output_path"])
            preview_dst = profile_dir / "preview.mp4"
            import shutil
            shutil.copy2(str(preview_src), str(preview_dst))

        # -- 5. Extract key frames + build contact sheet -------------------
        frames_dir = profile_dir / "frames"
        frames = _extract_key_frames(preview_dst, frames_dir)
        sheet_path = profile_dir / "contact_sheet.png"
        _build_contact_sheet(frames, sheet_path, title=profile.replace("_", " "))

        # -- 6. Collect clip metadata + new evaluation metrics for the report
        clips_meta = []
        cut_points: list[float] = []
        clip_intensities: list[float] = []
        for clip in timeline.clips:
            sm = clip.selection_metadata or {}
            sb = (sm.get("score_breakdown") or {}) if isinstance(sm, dict) else {}
            clips_meta.append({
                "asset_id":    clip.asset_id,
                "source_in":   clip.source_in,
                "source_out":  clip.source_out,
                "duration":    round(clip.source_out - clip.source_in, 3),
                "timeline_in": clip.timeline_in,
                "total_score":     sm.get("total_score") if isinstance(sm, dict) else None,
                "selected_rank":   sm.get("selected_rank") if isinstance(sm, dict) else None,
                "score_breakdown": sb,
                "reason":          sm.get("reason", "") if isinstance(sm, dict) else "",
                "ranking_profile": sm.get("ranking_profile") if isinstance(sm, dict) else None,
            })
            cut_points.append(clip.timeline_in)
            intensity = sb.get("intensity", 0.5) if isinstance(sb, dict) else 0.5
            clip_intensities.append(float(intensity))

        # Beat alignment score
        beat_pts = fp.get("beat_grid") or fp.get("beat_points") or []
        try:
            from app.services.reference_analyzer import _beat_alignment
            beat_alignment_score = _beat_alignment(cut_points, beat_pts, tolerance=0.15)
        except Exception:
            beat_alignment_score = 0.0

        # Pacing curve / escalation score
        try:
            from app.services.music_analysis import compute_pacing_curve, escalation_score
            clip_durs = [c.timeline_out - c.timeline_in for c in timeline.clips]
            pacing = compute_pacing_curve(clip_durs, clip_intensities)
            pacing_curve_score = escalation_score(pacing)
        except Exception:
            pacing = []
            pacing_curve_score = 0.0

        # Energy match score — correlation between clip intensities and fingerprint energy_curve
        try:
            energy_curve = fp.get("energy_curve", [])
            n_clips = len(clip_intensities)
            if n_clips >= 2 and len(energy_curve) >= 2:
                import statistics
                # Down-/up-sample energy_curve to match n_clips
                step = len(energy_curve) / n_clips
                sampled_energy = [
                    energy_curve[min(int(i * step), len(energy_curve) - 1)]
                    for i in range(n_clips)
                ]
                # Pearson-r proxy
                mean_i = statistics.mean(clip_intensities)
                mean_e = statistics.mean(sampled_energy)
                cov = sum(
                    (ci - mean_i) * (ce - mean_e)
                    for ci, ce in zip(clip_intensities, sampled_energy)
                )
                std_i = statistics.stdev(clip_intensities) or 1.0
                std_e = statistics.stdev(sampled_energy) or 1.0
                pearson_r = cov / ((n_clips - 1) * std_i * std_e)
                energy_match_score = max(0.0, min(1.0, (pearson_r + 1) / 2))
            else:
                energy_match_score = 0.0
        except Exception:
            energy_match_score = 0.0

        elapsed = round(time.monotonic() - t0, 2)
        logger.info("[%s] done in %.1fs → %s", profile, elapsed, preview_dst.name)

        return {
            "profile":              profile,
            "success":              True,
            "duration_sec":         elapsed,
            "output_path":          str(preview_dst),
            "contact_sheet_path":   str(sheet_path),
            "render_style":         timeline.render_style,
            "timeline_clips":       clips_meta,
            "num_clips":            len(clips_meta),
            "total_duration_sec":   timeline.duration_sec,
            "beat_alignment_score": round(beat_alignment_score, 4),
            "pacing_curve_score":   round(pacing_curve_score, 4),
            "energy_match_score":   round(energy_match_score, 4),
            "pacing_curve":         pacing,
            "error":                None,
        }

    except Exception as exc:
        elapsed = round(time.monotonic() - t0, 2)
        logger.error("[%s] FAILED after %.1fs: %s", profile, elapsed, exc, exc_info=True)
        return {
            "profile":              profile,
            "success":              False,
            "duration_sec":         elapsed,
            "output_path":          None,
            "contact_sheet_path":   None,
            "render_style":         None,
            "timeline_clips":       [],
            "num_clips":            0,
            "total_duration_sec":   0.0,
            "beat_alignment_score": 0.0,
            "pacing_curve_score":   0.0,
            "energy_match_score":   0.0,
            "pacing_curve":         [],
            "error":                str(exc),
        }


class _NoCaptionPlanner:
    """Thin wrapper around EditPlanner that skips the LLM caption call.

    Useful for `--no-captions` mode where no Anthropic key is available
    and you only care about clip selection and render style differences.
    """

    def __init__(self, llm):
        self._llm = llm

    def plan(self, fingerprint, footage_index, **kwargs):
        from app.services.edit_planner import EditPlanner
        from unittest.mock import patch

        dummy_captions = {
            "hook_index": 0,
            "shots": [{"caption": "", "moment_type": "broll"}
                      for _ in range(kwargs.get("max_shots", 20))],
        }

        planner = EditPlanner(self._llm)
        with patch.object(planner, "_get_captions", return_value=dummy_captions):
            return planner.plan(fingerprint, footage_index, **kwargs)


# ═══════════════════════════════════════════════════════════════════════════
#  REPORT
# ═══════════════════════════════════════════════════════════════════════════

def _write_report(
    results: list[dict[str, Any]],
    base_fingerprint: dict[str, Any],
    out_dir: Path,
    combined_sheet: Path,
    reference_mp4: Path,
    footage_paths: list[Path],
) -> Path:
    report = {
        "benchmark_version": "1.0",
        "reference_mp4":     str(reference_mp4),
        "footage_clips":     [str(p) for p in footage_paths],
        "output_dir":        str(out_dir),
        "combined_sheet":    str(combined_sheet),
        "base_fingerprint":  {
            k: base_fingerprint[k]
            for k in (
                "avg_shot_duration", "num_cuts", "pace", "energy_level",
                "tempo_bpm", "motion_style", "color_grade",
            )
            if k in base_fingerprint
        },
        "profiles": results,
        "summary": {
            "total_profiles":     len(results),
            "succeeded":          sum(1 for r in results if r["success"]),
            "failed":             sum(1 for r in results if not r["success"]),
            "total_duration_sec": round(sum(r["duration_sec"] for r in results), 2),
        },
    }

    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report_path


def _print_summary(results: list[dict[str, Any]], report_path: Path) -> None:
    print()
    print("=" * 60)
    print("  BENCHMARK SUMMARY")
    print("=" * 60)
    for r in results:
        status = "OK " if r["success"] else "ERR"
        clips = r["num_clips"]
        dur = r["total_duration_sec"]
        elapsed = r["duration_sec"]
        zs = (r.get("render_style") or {}).get("zoom_style", {}).get("type", "—")
        ts = (r.get("render_style") or {}).get("transition_style", {}).get("type", "—")
        grain = (r.get("render_style") or {}).get("grain", {}).get("enabled", False)
        vig = (r.get("render_style") or {}).get("vignette", {}).get("enabled", False)
        print(
            f"  [{status}] {r['profile']:<20}  {clips:>3} clips  "
            f"{dur:>5.1f}s edit  "
            f"zoom={zs}  trans={ts}  "
            f"grain={'Y' if grain else 'n'}  vig={'Y' if vig else 'n'}  "
            f"({elapsed:.0f}s)"
        )
        if not r["success"]:
            print(f"        ERROR: {r['error']}")
    print("=" * 60)
    print(f"  Report saved to: {report_path}")
    print()


# ═══════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="benchmark_render_styles",
        description=(
            "Run the full pipeline for every ranking profile and save\n"
            "preview MP4s, contact sheets, and a JSON report."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--reference", "-r",
        required=True,
        metavar="MP4",
        help="Path to the reference MP4 to analyze for style fingerprint.",
    )
    p.add_argument(
        "--footage", "-f",
        required=True,
        metavar="DIR",
        help="Folder containing footage clips (.mp4 / .mov / …).",
    )
    p.add_argument(
        "--out", "-o",
        default="./benchmarks",
        metavar="DIR",
        help="Output directory for previews, sheets, and report. (default: ./benchmarks)",
    )
    p.add_argument(
        "--profiles",
        nargs="+",
        default=PROFILES,
        choices=PROFILES,
        metavar="PROFILE",
        help=(
            "Which profiles to benchmark. Defaults to all six. "
            f"Choices: {', '.join(PROFILES)}"
        ),
    )
    p.add_argument(
        "--content-hint",
        default="",
        metavar="TEXT",
        help="Optional brief content description passed to the LLM caption generator.",
    )
    p.add_argument(
        "--no-captions",
        action="store_true",
        help=(
            "Skip the LLM caption call. "
            "Useful when no Anthropic key is set — clip selection and render "
            "style differences are still visible."
        ),
    )
    p.add_argument(
        "--frames", "-n",
        type=int,
        default=FRAMES_PER_PROFILE,
        metavar="N",
        help=f"Key frames per profile in contact sheet. (default: {FRAMES_PER_PROFILE})",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging.",
    )
    return p


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    _setup_logging(args.verbose)

    reference_mp4 = Path(args.reference).expanduser().resolve()
    footage_dir = Path(args.footage).expanduser().resolve()
    out_dir = Path(args.out).expanduser().resolve()

    # ── Validate inputs ────────────────────────────────────────────────────
    if not reference_mp4.is_file():
        logger.error("Reference file not found: %s", reference_mp4)
        return 2
    if not footage_dir.is_dir():
        logger.error("Footage directory not found: %s", footage_dir)
        return 2

    try:
        footage_paths = _collect_footage(footage_dir)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Output directory: %s", out_dir)
    logger.info("Reference: %s", reference_mp4)
    logger.info("Footage: %d clips in %s", len(footage_paths), footage_dir)

    # ── Build LLM client ────────────────────────────────────────────────────
    from tiktok_engine.llm_client import LLMClient
    from app.config import get_settings
    s = get_settings()
    api_key = s.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY") or None
    if not api_key and not args.no_captions:
        logger.warning(
            "ANTHROPIC_API_KEY not set. Captions will be empty. "
            "Pass --no-captions to suppress this warning."
        )
    llm = LLMClient(api_key=api_key, model=s.anthropic_model)

    # ── One-time analysis ───────────────────────────────────────────────────
    # Create a shared EmbeddingService for the benchmark run.  All profiles
    # share the same reference embedding; segment embeddings are cached per
    # (asset_id, start, end) so they are only computed once.
    from app.services.embeddings import EmbeddingService, CLIP_AVAILABLE
    bench_project_id = "benchmark"
    emb_svc: EmbeddingService | None
    if CLIP_AVAILABLE:
        emb_svc = EmbeddingService(bench_project_id, storage_root=str(out_dir / ".cache"))
        logger.info("CLIP embeddings enabled — cache: %s", out_dir / ".cache")
    else:
        emb_svc = None
        logger.info("CLIP not available — using proxy semantic_fit")

    base_fingerprint = _analyze_reference(reference_mp4, llm, embedding_service=emb_svc)
    footage_index = _analyze_footage(footage_paths, embedding_service=emb_svc)

    logger.info(
        "Reference fingerprint: %d cuts, avg=%.2fs, pace=%s, profile=%s",
        base_fingerprint.get("num_cuts", 0),
        base_fingerprint.get("avg_shot_duration", 0.0),
        base_fingerprint.get("pace", "—"),
        base_fingerprint.get("ranking_profile", "—"),
    )

    # ── Run each profile ───────────────────────────────────────────────────
    global FRAMES_PER_PROFILE
    FRAMES_PER_PROFILE = args.frames

    results: list[dict[str, Any]] = []
    for profile in args.profiles:
        logger.info("─── Profile: %s ───", profile)
        result = _run_profile(
            profile=profile,
            base_fingerprint=base_fingerprint,
            footage_index=footage_index,
            llm=llm,
            out_dir=out_dir,
            content_hint=args.content_hint,
            no_captions=args.no_captions,
        )
        results.append(result)

    # ── Combined contact sheet ─────────────────────────────────────────────
    profile_sheets: dict[str, Path] = {}
    for r in results:
        if r["success"] and r["contact_sheet_path"]:
            profile_sheets[r["profile"]] = Path(r["contact_sheet_path"])

    combined_sheet = out_dir / "combined_sheet.png"
    if profile_sheets:
        try:
            _build_combined_sheet(profile_sheets, combined_sheet)
            logger.info("Combined sheet: %s", combined_sheet)
        except Exception as exc:
            logger.warning("Could not build combined sheet: %s", exc)

    # ── JSON report ────────────────────────────────────────────────────────
    report_path = _write_report(
        results, base_fingerprint, out_dir, combined_sheet, reference_mp4, footage_paths
    )
    logger.info("Report: %s", report_path)

    _print_summary(results, report_path)

    failures = sum(1 for r in results if not r["success"])
    return failures  # 0 = all succeeded


if __name__ == "__main__":
    sys.exit(main())
