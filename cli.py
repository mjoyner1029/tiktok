#!/usr/bin/env python3
"""CLI entry-point for the TikTok Edit Plan Generator."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import List

from tiktok_engine.llm_client import LLMClient
from tiktok_engine.pipeline import EditPlanPipeline


def _read_file(path: str) -> str:
    return Path(path).read_text(encoding="utf-8").strip()


def _read_references(paths: List[str]) -> List[str]:
    refs: List[str] = []
    for p in paths:
        refs.append(_read_file(p))
    return refs


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tiktok-engine",
        description="Generate a structured TikTok edit plan from reference videos and raw content.",
    )
    p.add_argument(
        "-r",
        "--reference",
        action="append",
        required=True,
        help="Path to a text file containing a reference video transcript/description. "
        "Can be repeated for multiple references.",
    )
    p.add_argument(
        "-c",
        "--content",
        required=True,
        help="Path to a text file with the raw user content (transcript, talking points, etc.).",
    )
    p.add_argument(
        "-o",
        "--output",
        default=None,
        help="Path to write the output JSON. Prints to stdout if omitted.",
    )
    p.add_argument(
        "--model",
        default=os.getenv("TIKTOK_MODEL", "gpt-4o"),
        help="LLM model name (default: gpt-4o, or TIKTOK_MODEL env var).",
    )
    p.add_argument(
        "--base-url",
        default=os.getenv("TIKTOK_BASE_URL"),
        help="Optional base URL for an OpenAI-compatible API.",
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
        help="Use a single combined prompt instead of 5-step pipeline (faster, cheaper).",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging.",
    )
    return p


def main(argv: List[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s  %(message)s",
    )

    # --- read inputs ---
    references = _read_references(args.reference)
    raw_content = _read_file(args.content)

    # --- build client + pipeline ---
    llm = LLMClient(
        model=args.model,
        base_url=args.base_url,
        temperature=args.temperature,
    )
    pipeline = EditPlanPipeline(llm)

    # --- run ---
    if args.combined:
        plan = pipeline.run_combined(references, raw_content)
    else:
        plan = pipeline.run(references, raw_content)

    # --- output ---
    output_json = plan.to_json(indent=2)
    if args.output:
        Path(args.output).write_text(output_json, encoding="utf-8")
        logging.info("Edit plan written to %s", args.output)
    else:
        print(output_json)


if __name__ == "__main__":
    main()
