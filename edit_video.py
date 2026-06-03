#!/usr/bin/env python3
"""
edit_video.py — Full end-to-end TikTok-style video editor.

Takes one or more TikTok reference URLs + your own footage files and
produces a finished, rendered MP4 that replicates the reference editing style.

This script combines inspect_videos.py + make_video.py into a single command:

  python edit_video.py \\
    --ref  https://www.tiktok.com/@creator/video/123 \\
    --footage  my_clip.mp4  another_clip.mov \\
    --hint "a week in aspen" \\
    --out  renders/output.mp4

Steps performed automatically:
  1. Download + analyze reference TikTok URL(s) → editing fingerprint
  2. Analyze your footage files → scored footage index
  3. Plan the edit (shot selection, timing, captions) via EditPlanner
  4. Render to a finished MP4 via FFmpeg

Intermediate JSON files (ref_fingerprint.json, footage_index.json,
edit_timeline.json) are saved to the working directory so you can inspect
or reuse them with make_video.py.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from pathlib import Path

# ── Load .env automatically ───────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass


# ─────────────────────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="edit_video",
        description=(
            "Full end-to-end TikTok-style video editor.\n\n"
            "EXAMPLE:\n"
            "  python edit_video.py \\\n"
            "    --ref  https://www.tiktok.com/@user/video/123 \\\n"
            "    --footage  clip1.mp4 clip2.mov \\\n"
            "    --hint 'a week in aspen' \\\n"
            "    --out  renders/output.mp4\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    p.add_argument(
        "--ref", "--reference",
        metavar="URL_OR_FILE",
        action="append",
        dest="references",
        required=True,
        help=(
            "TikTok (or other social) URL to download and analyze as style reference. "
            "Also accepts a local video file path. Repeatable for multiple references."
        ),
    )
    p.add_argument(
        "--footage",
        metavar="FILE",
        action="append",
        dest="footage",
        required=True,
        help="Your own video file to edit. Repeatable for multiple clips.",
    )
    p.add_argument(
        "--hint",
        default="",
        metavar="TEXT",
        help="Content direction for captions (e.g. 'a week in aspen').",
    )
    p.add_argument(
        "--duration",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Target output duration in seconds (default: matches reference length).",
    )
    p.add_argument(
        "--out",
        default=None,
        metavar="FILE",
        help="Output MP4 path (default: renders/output_<timestamp>.mp4).",
    )
    p.add_argument(
        "--preview",
        action="store_true",
        help="Render at 480p for a fast draft (much quicker, lower quality).",
    )
    p.add_argument(
        "--no-captions",
        action="store_true",
        help="Skip the LLM caption-generation call — render cuts-only.",
    )
    p.add_argument(
        "--no-vision",
        action="store_true",
        help="Skip Claude Vision when analyzing references (faster, no API cost).",
    )
    p.add_argument(
        "--model",
        default=os.getenv("TIKTOK_MODEL", "claude-opus-4-5"),
        help="Claude model for Vision + caption generation (default: claude-opus-4-5).",
    )
    p.add_argument(
        "--fingerprint-out",
        default="ref_fingerprint.json",
        metavar="FILE",
        help="Where to save the reference fingerprint JSON (default: ref_fingerprint.json).",
    )
    p.add_argument(
        "--footage-index-out",
        default="footage_index.json",
        metavar="FILE",
        help="Where to save the footage index JSON (default: footage_index.json).",
    )
    p.add_argument(
        "--timeline-out",
        default="edit_timeline.json",
        metavar="FILE",
        help="Where to save the edit timeline JSON (default: edit_timeline.json).",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging.",
    )
    return p


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 1: Analyze reference URL(s) → fingerprint
# ─────────────────────────────────────────────────────────────────────────────

def _analyze_references(refs: list[str], llm, no_vision: bool) -> dict:
    """Download and analyze one or more reference URLs/files → merged fingerprint."""
    from tiktok_engine.reference_analyzer import ReferenceAnalyzer

    analyzer = ReferenceAnalyzer(llm)
    urls = [r for r in refs if r.startswith("http://") or r.startswith("https://")]
    local_files = [r for r in refs if not r.startswith("http")]

    fingerprints = []

    if urls:
        print(f"\n[Step 1/4] Analyzing {len(urls)} reference URL(s) ...")
        fp = analyzer.analyze_urls(urls)
        fingerprints.append(fp)

    for path in local_files:
        if not Path(path).exists():
            print(f"  WARNING: reference file not found: {path}", file=sys.stderr)
            continue
        print(f"  Analyzing local reference file: {path}")
        fp = analyzer._analyze_file(path)
        fingerprints.append(fp)

    if not fingerprints:
        print("  WARNING: No references analyzed — using defaults.", file=sys.stderr)
        return analyzer._default_fingerprint()
    if len(fingerprints) == 1:
        return fingerprints[0]
    return analyzer._merge(fingerprints)


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 2: Analyze footage file(s) → footage index
# ─────────────────────────────────────────────────────────────────────────────

def _analyze_footage(footage_files: list[str]) -> list[dict]:
    """Analyze local footage files → footage index list."""
    from tiktok_engine.footage_analyzer import FootageAnalyzer

    paths = []
    for f in footage_files:
        p = Path(f)
        if not p.exists():
            print(f"  WARNING: footage file not found: {f}", file=sys.stderr)
        else:
            paths.append(p)

    if not paths:
        raise SystemExit("ERROR: No valid footage files found.")

    print(f"\n[Step 2/4] Analyzing {len(paths)} footage file(s) ...")
    analyzer = FootageAnalyzer()
    index = analyzer.analyze_all(paths)
    total_moments = sum(len(e.get("moments", [])) for e in index)
    print(f"  Found {total_moments} scoreable moments across {len(index)} clip(s).")
    return index


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 3: Plan edit → EditTimeline
# ─────────────────────────────────────────────────────────────────────────────

def _plan_edit(fingerprint: dict, footage_index: list[dict], hint: str,
               duration: float | None, llm) -> object:
    """Run EditPlanner to produce an EditTimeline."""
    from app.services.edit_planner import EditPlanner

    print(f"\n[Step 3/4] Planning edit ...")
    print(f"  Reference: {fingerprint.get('num_cuts', '?')} cuts, "
          f"avg shot {fingerprint.get('avg_shot_duration', '?'):.2f}s, "
          f"dominant transition: {fingerprint.get('dominant_transition', '?')}")

    # Strip _summary sentinel if present (added by inspect_videos.py)
    clean_footage = [dict(e) for e in footage_index]
    for entry in clean_footage:
        entry.pop("_summary", None)

    planner = EditPlanner(llm)
    timeline = planner.plan(
        fingerprint=fingerprint,
        footage_index=clean_footage,
        content_hint=hint,
        target_duration_sec=duration,
    )
    print(f"  Planned {len(timeline.clips)} shots, "
          f"{timeline.duration_sec:.1f}s total, "
          f"{len(timeline.captions)} captions.")
    return timeline


# ─────────────────────────────────────────────────────────────────────────────
#  FORMATTING: TikTok-style caption + transition polish
# ─────────────────────────────────────────────────────────────────────────────

def _apply_tiktok_style(timeline, fingerprint: dict):
    """
    Apply visual polish that matches the reference TikTok aesthetic:
      - Captions formatted as ++ ALL CAPS TEXT ++
      - Impact font (heavy, no stroke — characteristic of TikTok viral text)
      - Transition distribution that mirrors the reference fingerprint
    """
    from app.services.timeline_schema import TransitionEvent

    # ── Captions: ++ TEXT ++ in ALL CAPS ──────────────────────────────────
    for cap in timeline.captions:
        text = cap.text.strip().upper()
        if not text.startswith("++"):
            text = f"++ {text} ++"
        cap.text = text

    # ── Typography: Impact font (matches viral TikTok heavy-sans style) ───
    if timeline.render_style is None:
        timeline.render_style = {}
    cp = timeline.render_style.setdefault("caption_preset", {})
    cp.setdefault("font_family", "Impact")
    cp.setdefault("font_weight", "normal")   # Impact is already heavy by design
    cp.setdefault("stroke_width", 0.0)       # no stroke — clean editorial look
    cp.setdefault("tracking", 2.0)
    cp.setdefault("font_size", 80)

    for cap in timeline.captions:
        cap.font_family  = cp["font_family"]
        cap.font_weight  = cp["font_weight"]
        cap.font_size    = cp["font_size"]
        cap.stroke_width = cp["stroke_width"]
        cap.tracking     = cp["tracking"]

    # ── Transitions: mirror the reference fingerprint distribution ─────────
    # Default: 65% hard_cut, 25% whip_pan, 10% flash — typical fast TikTok
    transitions = fingerprint.get("transitions") or ["hard_cut"]
    dom = fingerprint.get("dominant_transition", "hard_cut")

    # Build a weighted transition pool from the fingerprint
    # The dominant transition gets 65% weight; remaining types share 35%.
    non_dom = [t for t in transitions if t != dom]
    pool: list[str] = ([dom] * 13) + (non_dom * 7 if non_dom else [])
    pool = pool or ["hard_cut"]

    rng = random.Random(42)
    _TRANS_DUR = {
        "cut": 0.0, "hard_cut": 0.0, "flash_cut": 0.0, "flash": 0.0,
        "whip_pan_left": 0.12, "whip_pan_right": 0.12, "whip_pan": 0.12,
        "swipe_left": 0.22, "swipe_right": 0.22, "swipe_up": 0.22, "swipe_down": 0.22,
        "fade": 0.45, "dissolve": 0.45, "zoom_transition": 0.25,
    }
    for clip in timeline.clips:
        t_type = rng.choice(pool)
        clip.transition_out = TransitionEvent(
            type=t_type,
            duration=_TRANS_DUR.get(t_type, 0.0),
        )

    return timeline


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 4: Render → MP4
# ─────────────────────────────────────────────────────────────────────────────

def _render(timeline, footage_index: list[dict], out_path: str,
            preview: bool) -> dict:
    """Render the EditTimeline to a finished MP4."""
    from app.services.render_engine import RenderEngine

    # Build asset_id → file path resolver from footage_index
    asset_map: dict[str, str] = {
        e["asset_id"]: e["file_path"]
        for e in footage_index
        if e.get("file_path")
    }

    def asset_resolver(asset_id: str) -> str:
        return asset_map.get(asset_id, asset_id)

    # Inject source_path into clips that don't already have one
    for clip in timeline.clips:
        if not clip.source_path:
            clip.source_path = asset_resolver(clip.asset_id)

    print(f"\n[Step 4/4] Rendering → {out_path}  (this takes a minute or two) ...")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    engine = RenderEngine(asset_resolver=asset_resolver)
    result = engine.render_timeline(timeline, preview=preview)

    # Move to desired path if the engine chose its own name
    final = result.get("output_path", out_path)
    if final != out_path and Path(final).exists():
        import shutil
        shutil.move(final, out_path)
        result["output_path"] = out_path

    return result


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s  %(message)s",
    )

    # ── Validate footage files exist early ───────────────────────────────────
    for f in (args.footage or []):
        if not f.startswith("http") and not Path(f).exists():
            parser.error(f"Footage file not found: {f}")

    # ── Build LLM client ─────────────────────────────────────────────────────
    api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY")

    if not api_key:
        print(
            "WARNING: No ANTHROPIC_API_KEY found. "
            "Skipping Vision analysis and caption generation.\n"
            "Set ANTHROPIC_API_KEY in .env to enable AI-powered captions.",
            file=sys.stderr,
        )
        args.no_captions = True
        args.no_vision = True

    class _NoOpLLM:
        def chat_json(self, *a, **kw): return {}
        def chat(self, *a, **kw): return ""
        def chat_with_images(self, *a, **kw): return "{}"

    # Build the real LLM client once (if we have an API key and need it at all)
    _real_llm = None
    if api_key and (not args.no_captions or not args.no_vision):
        from tiktok_engine.llm_client import LLMClient
        _real_llm = LLMClient(api_key=api_key, model=args.model)

    # llm       → used by EditPlanner for caption generation
    # llm_for_ref → used by ReferenceAnalyzer for Vision analysis
    llm         = _NoOpLLM() if args.no_captions else (_real_llm or _NoOpLLM())
    llm_for_ref = _NoOpLLM() if args.no_vision   else (_real_llm or _NoOpLLM())

    # ── Determine output path ────────────────────────────────────────────────
    if args.out:
        out_path = args.out
    else:
        Path("renders").mkdir(exist_ok=True)
        out_path = f"renders/output_{int(time.time())}.mp4"

    # ── Step 1: Reference analysis ───────────────────────────────────────────
    fingerprint = _analyze_references(args.references, llm_for_ref, args.no_vision)

    # Save fingerprint for inspection / reuse
    fp_path = Path(args.fingerprint_out)
    fp_path.write_text(json.dumps(fingerprint, indent=2), encoding="utf-8")
    print(f"  Fingerprint saved → {fp_path}")

    # ── Step 2: Footage analysis ─────────────────────────────────────────────
    footage_index = _analyze_footage(args.footage)

    # Save footage index for inspection / reuse
    fi_path = Path(args.footage_index_out)
    fi_path.write_text(json.dumps(footage_index, indent=2), encoding="utf-8")
    print(f"  Footage index saved → {fi_path}")

    # ── Step 3: Plan edit ────────────────────────────────────────────────────
    timeline = _plan_edit(fingerprint, footage_index, args.hint, args.duration, llm)

    # Apply TikTok-style visual polish
    timeline = _apply_tiktok_style(timeline, fingerprint)

    # Save timeline for inspection
    tl_path = Path(args.timeline_out)
    tl_path.write_text(
        json.dumps(timeline.model_dump(), indent=2, default=str),
        encoding="utf-8",
    )
    print(f"  Timeline saved → {tl_path}")

    # ── Step 4: Render ───────────────────────────────────────────────────────
    try:
        result = _render(timeline, footage_index, out_path, args.preview)
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # ── Done ─────────────────────────────────────────────────────────────────
    video = result.get("output_path", out_path)
    thumb = result.get("thumbnail_path", "")

    print(f"\n✓ Done!")
    print(f"  Video     → {video}")
    if thumb:
        print(f"  Thumbnail → {thumb}")
    print(f"\n  Inspect intermediates:")
    print(f"    Reference fingerprint → {fp_path}")
    print(f"    Footage index         → {fi_path}")
    print(f"    Edit timeline         → {tl_path}")


if __name__ == "__main__":
    main()
