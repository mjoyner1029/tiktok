#!/usr/bin/env python3
"""
make_video.py — Plan + render a TikTok edit from ref_fingerprint.json + footage_index.json.

Usage:
    python3 make_video.py --hint "a week in aspen"
    python3 make_video.py --hint "a week in aspen" --duration 15 --out renders/aspen.mp4
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass

# ── arg parse ──────────────────────────────────────────────────────────────

def _args():
    p = argparse.ArgumentParser()
    p.add_argument("--hint",         default="a week in aspen",
                   help="Content direction for captions (default: 'a week in aspen')")
    p.add_argument("--fingerprint",  default="ref_fingerprint.json")
    p.add_argument("--footage",      default="footage_index.json")
    p.add_argument("--out",          default=None,
                   help="Output MP4 path (default: renders/aspen_<timestamp>.mp4)")
    p.add_argument("--duration",     type=float, default=None,
                   help="Target video duration in seconds (default: match reference ~18s)")
    p.add_argument("--preview",      action="store_true",
                   help="Render at 480p for fast preview")
    p.add_argument("--no-captions",  action="store_true",
                   help="Skip Claude caption call — render cuts-only")
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args()


def main():
    args = _args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s  %(message)s",
    )

    # ── Load JSONs ────────────────────────────────────────────────────────
    fp_path = Path(args.fingerprint)
    fi_path = Path(args.footage)

    if not fp_path.exists():
        sys.exit(f"ERROR: fingerprint not found: {fp_path}  (run inspect_videos.py --ref first)")
    if not fi_path.exists():
        sys.exit(f"ERROR: footage index not found: {fi_path}  (run inspect_videos.py --footage first)")

    fingerprint   = json.loads(fp_path.read_text())
    footage_index = json.loads(fi_path.read_text())

    # Strip _summary keys — planner doesn't expect them
    for entry in footage_index:
        entry.pop("_summary", None)

    print(f"\nLoaded fingerprint: {fingerprint['num_cuts']} cuts, "
          f"avg shot {fingerprint['avg_shot_duration']:.2f}s, "
          f"dominant transition: {fingerprint['dominant_transition']}")
    print(f"Loaded footage index: {len(footage_index)} clips")

    # ── Build asset resolver  asset_id → file path ────────────────────────
    asset_map: dict[str, str] = {
        entry["asset_id"]: entry["file_path"]
        for entry in footage_index
        if entry.get("file_path")
    }
    asset_resolver = lambda aid: asset_map.get(aid, aid)

    # ── LLM client ────────────────────────────────────────────────────────
    api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key and not args.no_captions:
        print("WARNING: No API key found — running without captions (cuts only)")
        args.no_captions = True

    class _NoOpLLM:
        def chat_json(self, *a, **kw): return {}
        def chat(self, *a, **kw): return ""

    if args.no_captions:
        llm = _NoOpLLM()
    else:
        from app.services.llm_client import LLMClient
        llm = LLMClient(
            api_key=api_key,
            model=os.getenv("TIKTOK_MODEL", "claude-opus-4-5"),
        )

    # ── Plan ──────────────────────────────────────────────────────────────
    print(f"\nPlanning edit — hint: '{args.hint}' ...")

    from app.services.edit_planner import EditPlanner
    planner = EditPlanner(llm)

    timeline = planner.plan(
        fingerprint=fingerprint,
        footage_index=footage_index,
        content_hint=args.hint,
        target_duration_sec=args.duration,
    )

    # ── Inject source_path ────────────────────────────────────────────────
    for clip in timeline.clips:
        if not clip.source_path:
            clip.source_path = asset_resolver(clip.asset_id)

    # ── Caption format: ++ TEXT ++ ────────────────────────────────────────
    for cap in timeline.captions:
        text = cap.text.strip()
        # Ensure ALL CAPS
        text = text.upper()
        # Add ++ decorators if not already present
        if not text.startswith("++"):
            text = f"++ {text} ++"
        cap.text = text

    # ── Override font to Impact (matches reference heavy-sans style) ──────
    # Set both the profile-level preset AND per-clip fields so the inline
    # \fn override tags in the ASS file also use Impact.
    if timeline.render_style is None:
        timeline.render_style = {}
    cp = timeline.render_style.setdefault("caption_preset", {})
    cp["font_family"] = "Impact"
    cp["font_weight"] = "normal"   # Impact is already heavy by design
    cp["stroke_width"] = 0.0       # reference has no stroke
    cp["tracking"] = 2.0           # Impact needs wider tracking for readability
    cp["font_size"] = 80           # large, reference-matching

    for cap in timeline.captions:
        cap.font_family  = "Impact"
        cap.font_weight  = "normal"
        cap.font_size    = 80
        cap.stroke_width = 0.0
        cap.tracking     = 2.0

    # ── Transition distribution: 65% hard_cut, 25% whip_pan, 10% flash ───
    # Mirrors the reference fingerprint (dominant=hard_cut, with whip+flash)
    from app.services.timeline_schema import TransitionEvent
    import random as _rng
    _rng.seed(42)
    for i, clip in enumerate(timeline.clips):
        r = _rng.random()
        if r < 0.10:
            t_type, t_dur = "flash_cut", 0.0
        elif r < 0.35:
            t_type, t_dur = "whip_pan_right", 0.12
        else:
            t_type, t_dur = "cut", 0.0
        clip.transition_out = TransitionEvent(type=t_type, duration=t_dur)

    print(f"  Timeline: {len(timeline.clips)} shots, "
          f"{timeline.duration_sec:.1f}s total, "
          f"{len(timeline.captions)} captions")

    # Save timeline for inspection
    timeline_path = Path("edit_timeline.json")
    timeline_path.write_text(
        json.dumps(timeline.model_dump(), indent=2, default=str), encoding="utf-8"
    )
    print(f"  Timeline saved → {timeline_path}")

    # ── Render ────────────────────────────────────────────────────────────
    import time
    ts = int(time.time())
    if args.out:
        out_path = args.out
    else:
        Path("renders").mkdir(exist_ok=True)
        out_path = f"renders/aspen_{ts}.mp4"

    print(f"\nRendering → {out_path}  (this takes a few minutes) ...")

    from app.services.render_engine import RenderEngine
    engine = RenderEngine(asset_resolver=asset_resolver)

    try:
        result = engine.render_timeline(timeline, preview=args.preview)
    except Exception as _exc:
        import traceback, sys
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        sys.exit(1)

    final = result.get("output_path", out_path)
    thumb = result.get("thumbnail_path", "")

    # Move to desired output path if render engine chose its own name
    if final != out_path and Path(final).exists():
        import shutil
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(final, out_path)
        final = out_path

    print(f"\n✓ Done!")
    print(f"  Video     → {final}")
    if thumb:
        print(f"  Thumbnail → {thumb}")


if __name__ == "__main__":
    main()
