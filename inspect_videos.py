#!/usr/bin/env python3
"""
inspect_videos.py — Analyze reference and footage videos and output structured JSON.

This script lets you see exactly what the pipeline "understands" about your videos
before running the full edit plan generation.

USAGE:

  # Analyze a TikTok reference URL → reference_fingerprint.json
  python inspect_videos.py --ref https://www.tiktok.com/@user/video/123

  # Analyze local footage files → footage_index.json
  python inspect_videos.py --footage my_clip.mp4 another_clip.mov

  # Do both at once
  python inspect_videos.py --ref https://... --footage clip1.mp4 clip2.mp4

  # Output to specific files instead of stdout
  python inspect_videos.py --ref https://... --ref-out ref.json
  python inspect_videos.py --footage clip.mp4 --footage-out footage.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Load .env automatically if present
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="inspect_videos",
        description=(
            "Analyze reference TikTok URLs and/or footage files → output JSON.\n\n"
            "Reference fingerprint contains:\n"
            "  cut timestamps, shot durations, avg shot length, dominant transition,\n"
            "  all transition types, color grade, caption style, hook style,\n"
            "  motion pattern, energy level, tone\n\n"
            "Footage index contains:\n"
            "  per-clip moments with sharpness scores, speech segments, scene changes"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    p.add_argument(
        "--ref", "--reference",
        metavar="URL_OR_FILE",
        action="append",
        dest="references",
        help="TikTok/social URL or local video file to analyze as style reference. "
             "Repeatable for multiple references.",
    )
    p.add_argument(
        "--footage",
        metavar="FILE",
        action="append",
        dest="footage",
        help="Local video file to analyze as footage. Repeatable.",
    )
    p.add_argument(
        "--ref-out",
        metavar="FILE",
        default=None,
        help="Write reference fingerprint JSON to this file (default: print to stdout).",
    )
    p.add_argument(
        "--footage-out",
        metavar="FILE",
        default=None,
        help="Write footage index JSON to this file (default: print to stdout).",
    )
    p.add_argument(
        "--model",
        default=os.getenv("TIKTOK_MODEL", "claude-opus-4-5"),
        help="Claude model for Vision analysis (default: claude-opus-4-5).",
    )
    p.add_argument(
        "--no-vision",
        action="store_true",
        help="Skip Claude Vision calls — only FFmpeg-based analysis (faster, no API cost).",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging.",
    )
    return p


def _analyze_references(urls_or_paths: list[str], llm, no_vision: bool) -> dict:
    """Analyze reference URLs/files → merged reference fingerprint dict."""
    from app.services.reference_analyzer import ReferenceAnalyzer

    analyzer = ReferenceAnalyzer(llm)
    urls = [s for s in urls_or_paths if s.startswith("http://") or s.startswith("https://")]
    local_files = [s for s in urls_or_paths if not s.startswith("http")]

    fingerprints = []

    # Process URLs
    if urls:
        fp = analyzer.analyze_urls(urls)
        if urls_or_paths == urls:  # only URLs, no locals
            return fp
        fingerprints.append(fp)

    # Process local video files
    for path in local_files:
        if not Path(path).exists():
            print(f"  WARNING: file not found: {path}", file=sys.stderr)
            continue
        print(f"  Analyzing local reference: {path}", file=sys.stderr)
        fp = analyzer._analyze_file(path)
        fingerprints.append(fp)

    if not fingerprints:
        return analyzer._default_fingerprint()
    if len(fingerprints) == 1:
        return fingerprints[0]
    return analyzer._merge(fingerprints)


def _analyze_footage(files: list[str]) -> list[dict]:
    """Analyze footage files → footage index (no LLM needed)."""
    from app.services.footage_analyzer import FootageAnalyzer

    paths = []
    for f in files:
        p = Path(f)
        if not p.exists():
            print(f"  WARNING: file not found: {f}", file=sys.stderr)
        else:
            paths.append(p)

    if not paths:
        print("  No valid footage files found.", file=sys.stderr)
        return []

    analyzer = FootageAnalyzer()
    return analyzer.analyze_all(paths)


def _print_or_write(data: dict | list, out_file: str | None, label: str) -> None:
    """Pretty-print JSON to stdout or write to file."""
    text = json.dumps(data, indent=2, ensure_ascii=False)
    if out_file:
        Path(out_file).write_text(text, encoding="utf-8")
        print(f"\n{label} written to {out_file}", file=sys.stderr)
    else:
        print(f"\n{'='*60}")
        print(f"  {label}")
        print(f"{'='*60}")
        print(text)


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.references and not args.footage:
        parser.error("Provide at least one --ref URL/file or --footage file.")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s  %(message)s",
    )

    # Build LLM client (needed for reference Vision analysis)
    llm = None
    if args.references and not args.no_vision:
        from app.services.llm_client import LLMClient
        api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            print(
                "WARNING: No ANTHROPIC_API_KEY found. "
                "Vision analysis will be skipped (only FFmpeg metrics).\n"
                "Set ANTHROPIC_API_KEY in .env or use --no-vision to suppress this warning.",
                file=sys.stderr,
            )
            args.no_vision = True
        else:
            llm = LLMClient(api_key=api_key, model=args.model)

    # ── Analyze references ──────────────────────────────────────────────
    if args.references:
        print(f"\nAnalyzing {len(args.references)} reference(s)...", file=sys.stderr)

        if args.no_vision or llm is None:
            # FFmpeg-only: use ReferenceAnalyzer with a no-op LLM
            class _NoOpLLM:
                def chat_with_images(self, *a, **kw): return "{}"
                def chat_json(self, *a, **kw): return {}
                def chat(self, *a, **kw): return ""
            llm_for_ref = _NoOpLLM()
        else:
            llm_for_ref = llm

        fingerprint = _analyze_references(args.references, llm_for_ref, args.no_vision)

        # Annotate with human-readable summary
        fingerprint["_summary"] = {
            "total_cuts": fingerprint.get("num_cuts", 0),
            "avg_shot_seconds": fingerprint.get("avg_shot_duration", 0),
            "dominant_transition": fingerprint.get("dominant_transition", "unknown"),
            "all_transitions": fingerprint.get("transitions", []),
            "energy": fingerprint.get("energy_level", "unknown"),
            "hook": fingerprint.get("hook_style", ""),
            "tone": fingerprint.get("tone", ""),
            "color_grade": fingerprint.get("color_grade", {}),
            "caption_style": fingerprint.get("caption_style", {}),
        }

        _print_or_write(fingerprint, args.ref_out, "REFERENCE FINGERPRINT")

    # ── Analyze footage ─────────────────────────────────────────────────
    if args.footage:
        print(f"\nAnalyzing {len(args.footage)} footage file(s)...", file=sys.stderr)
        footage_index = _analyze_footage(args.footage)

        # Add per-clip summary
        for entry in footage_index:
            entry["_summary"] = {
                "clip": entry.get("asset_id", ""),
                "duration_sec": entry.get("duration", 0),
                "total_moments": len(entry.get("moments", [])),
                "sharp_moments": sum(
                    1 for m in entry.get("moments", []) if m.get("type") == "sharp_detail"
                ),
                "has_speech": entry.get("has_speech", False),
                "speech_segments": entry.get("speech_segments", []),
                "scene_change_count": len(entry.get("scene_changes", [])),
                "top_moments": sorted(
                    entry.get("moments", []),
                    key=lambda m: m.get("score", 0),
                    reverse=True,
                )[:5],
            }

        _print_or_write(footage_index, args.footage_out, "FOOTAGE INDEX")


if __name__ == "__main__":
    main()
