#!/usr/bin/env python3
"""CLI entry-point for the TikTok Edit Plan Generator."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import List, Optional

# Load .env automatically if present (local use); override any stale shell vars
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass

from tiktok_engine.llm_client import LLMClient
from tiktok_engine.pipeline import EditPlanPipeline
from tiktok_engine.video_ingest import VideoIngestor


def _read_file(path: str) -> str:
    return Path(path).read_text(encoding="utf-8").strip()


def _is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tiktok-engine",
        description=(
            "Generate a TikTok edit plan.\n\n"
            "REFERENCE MODE (text files):\n"
            "  cli.py -r examples/reference_1.txt -c examples/content.txt\n\n"
            "VIDEO MODE (TikTok URLs or local files):\n"
            "  cli.py --url https://tiktok.com/@x/video/123 --footage my_clip.mp4\n"
            "  cli.py --url URL1 --url URL2 --footage https://... \n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # ── reference inputs (mutually exclusive: text files OR video URLs) ──
    ref_group = p.add_mutually_exclusive_group(required=True)
    ref_group.add_argument(
        "-r", "--reference",
        action="append",
        metavar="FILE",
        help="Path to a text file with a reference video description. Repeatable.",
    )
    ref_group.add_argument(
        "--url",
        action="append",
        metavar="URL",
        dest="urls",
        help="TikTok (or other social) URL to download and analyze as style reference. "
             "Repeatable. Mutually exclusive with -r.",
    )

    # ── footage / content ─────────────────────────────────────────────────
    p.add_argument(
        "-c", "--content",
        metavar="FILE",
        help="Path to a text file with raw content / talking points (text mode).",
    )
    p.add_argument(
        "--footage",
        metavar="FILE_OR_URL",
        help="Your own video file path or URL to analyze as the raw footage for recreation.",
    )

    # ── output ────────────────────────────────────────────────────────────
    p.add_argument(
        "-o", "--output",
        default=None,
        help="Path to write output JSON. Prints to stdout if omitted.",
    )

    # ── model / LLM ───────────────────────────────────────────────────────
    p.add_argument(
        "--model",
        default=os.getenv("TIKTOK_MODEL", "claude-opus-4-5"),
        help="LLM model name (default: claude-opus-4-5 or TIKTOK_MODEL env var).",
    )
    p.add_argument(
        "--base-url",
        default=os.getenv("TIKTOK_BASE_URL"),
        help="Optional base URL for OpenAI-compatible API.",
    )
    p.add_argument(
        "--temperature",
        type=float,
        default=0.7,
        help="LLM temperature (default: 0.7).",
    )
    p.add_argument(
        "--combined",
        action="store_true",
        help="Use a single combined prompt instead of the 5-step pipeline.",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging.",
    )
    return p


def _resolve_references(args, ingestor: VideoIngestor) -> List[str]:
    """Return a list of style-description strings from either text files or URLs."""
    if args.reference:
        return [_read_file(p) for p in args.reference]

    references = []
    for i, url in enumerate(args.urls, 1):
        print(f"\n[{i}/{len(args.urls)}] Analyzing reference: {url}")
        desc = ingestor.analyze_reference_url(url)
        references.append(desc)
        print(f"  Style extracted ({len(desc)} chars)")
    return references


def _resolve_footage(args, ingestor: VideoIngestor) -> str:
    """Return raw content string from a text file, footage URL, or local video."""
    if args.footage:
        src = args.footage
        print(f"\nAnalyzing your footage: {src}")
        if _is_url(src):
            raw = ingestor.describe_footage_url(src)
        else:
            raw = ingestor.describe_footage_file(src)
        print(f"  Footage described ({len(raw)} chars)")
        return raw

    if args.content:
        return _read_file(args.content)

    # Interactive prompt if neither was supplied
    print("\nNo footage or content provided. Please describe your video / talking points:")
    lines = []
    try:
        while True:
            line = input()
            lines.append(line)
    except EOFError:
        pass
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Validate: --url mode requires --footage or --content
    if args.urls and not args.footage and not args.content:
        parser.error(
            "--url mode requires --footage (your video) or --content (text description).\n"
            "Example: cli.py --url https://tiktok.com/... --footage my_clip.mp4"
        )

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s  %(message)s",
    )

    # ── build LLM client ─────────────────────────────────────────────────
    api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY")
    llm = LLMClient(
        api_key=api_key,
        model=args.model,
        base_url=args.base_url,
        temperature=args.temperature,
    )
    ingestor = VideoIngestor(llm)
    pipeline = EditPlanPipeline(llm)

    # ── resolve inputs ───────────────────────────────────────────────────
    if args.urls:
        print(f"\nAnalyzing {len(args.urls)} reference video(s) from URL(s)...")
    references = _resolve_references(args, ingestor)
    raw_content = _resolve_footage(args, ingestor)

    # ── run pipeline ─────────────────────────────────────────────────────
    print("\nRunning edit plan pipeline...\n")
    if args.combined:
        plan = pipeline.run_combined(references, raw_content)
    else:
        plan = pipeline.run(references, raw_content)

    # ── output ───────────────────────────────────────────────────────────
    output_json = plan.to_json(indent=2)
    if args.output:
        Path(args.output).write_text(output_json, encoding="utf-8")
        print(f"\nEdit plan written to {args.output}")
    else:
        print(output_json)


if __name__ == "__main__":
    main()
