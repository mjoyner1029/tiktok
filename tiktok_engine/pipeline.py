"""Pipeline that orchestrates the 5-step TikTok edit-plan generation."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .llm_client import LLMClient
from .models import (
    CaptionChunk,
    Captions,
    EditingNotes,
    EditPlan,
    Script,
    StyleAnalysis,
    Timeline,
    TimelineSegment,
)
from .prompts import (
    CAPTION_STRATEGY_PROMPT,
    COMBINED_PROMPT,
    EDITING_NOTES_PROMPT,
    SCRIPT_TRANSFORM_PROMPT,
    STYLE_ANALYSIS_PROMPT,
    SYSTEM_PROMPT,
    TIMELINE_PROMPT,
)

logger = logging.getLogger(__name__)


class EditPlanPipeline:
    """Generate a full TikTok edit plan from reference videos + raw content."""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _format_references(references: List[str]) -> str:
        parts = []
        for i, ref in enumerate(references, 1):
            parts.append(f"--- Reference {i} ---\n{ref}")
        return "\n\n".join(parts)

    # ── individual steps ─────────────────────────────────────────────────

    def step1_style_analysis(self, references: List[str]) -> StyleAnalysis:
        logger.info("Step 1 / 5 — Style Analysis")
        prompt = STYLE_ANALYSIS_PROMPT.format(
            references=self._format_references(references)
        )
        data = self.llm.chat_json(SYSTEM_PROMPT, prompt)
        return StyleAnalysis(**{k: data.get(k, "") for k in StyleAnalysis.__dataclass_fields__})

    def step2_script(self, style: StyleAnalysis, raw_content: str) -> Script:
        logger.info("Step 2 / 5 — Script Transformation")
        from dataclasses import asdict

        prompt = SCRIPT_TRANSFORM_PROMPT.format(
            style_json=json.dumps(asdict(style), indent=2),
            raw_content=raw_content,
        )
        data = self.llm.chat_json(SYSTEM_PROMPT, prompt)
        return Script(script=data.get("script", []))

    def step3_timeline(self, style: StyleAnalysis, script: Script) -> Timeline:
        logger.info("Step 3 / 5 — Edit Timeline")
        from dataclasses import asdict

        prompt = TIMELINE_PROMPT.format(
            style_json=json.dumps(asdict(style), indent=2),
            script_json=json.dumps(asdict(script), indent=2),
        )
        data = self.llm.chat_json(SYSTEM_PROMPT, prompt)
        segments = [TimelineSegment(**seg) for seg in data.get("timeline", [])]
        return Timeline(timeline=segments)

    def step4_captions(self, timeline: Timeline) -> Captions:
        logger.info("Step 4 / 5 — Caption Strategy")
        from dataclasses import asdict

        prompt = CAPTION_STRATEGY_PROMPT.format(
            timeline_json=json.dumps(asdict(timeline), indent=2),
        )
        data = self.llm.chat_json(SYSTEM_PROMPT, prompt)
        chunks = [CaptionChunk(**c) for c in data.get("captions", [])]
        return Captions(captions=chunks)

    def step5_editing_notes(
        self, style: StyleAnalysis, timeline: Timeline
    ) -> EditingNotes:
        logger.info("Step 5 / 5 — Editing Notes")
        from dataclasses import asdict

        prompt = EDITING_NOTES_PROMPT.format(
            style_json=json.dumps(asdict(style), indent=2),
            timeline_json=json.dumps(asdict(timeline), indent=2),
        )
        data = self.llm.chat_json(SYSTEM_PROMPT, prompt)
        return EditingNotes(editing_notes=data.get("editing_notes", []))

    # ── full pipeline (step-by-step) ─────────────────────────────────────

    def run(self, references: List[str], raw_content: str) -> EditPlan:
        """Execute the full 5-step pipeline sequentially."""
        style = self.step1_style_analysis(references)
        script = self.step2_script(style, raw_content)
        timeline = self.step3_timeline(style, script)
        captions = self.step4_captions(timeline)
        notes = self.step5_editing_notes(style, timeline)

        return EditPlan(
            style_analysis=style,
            script=script,
            timeline=timeline,
            captions=captions,
            editing_notes=notes,
        )

    # ── single-shot mode (one prompt, faster + cheaper) ──────────────────

    def run_combined(self, references: List[str], raw_content: str) -> EditPlan:
        """Execute in a single LLM call using the combined prompt."""
        logger.info("Running combined single-shot generation")
        prompt = COMBINED_PROMPT.format(
            references=self._format_references(references),
            raw_content=raw_content,
        )
        data = self.llm.chat_json(SYSTEM_PROMPT, prompt)
        return self._parse_combined(data)

    @staticmethod
    def _parse_combined(data: Dict[str, Any]) -> EditPlan:
        sa = data.get("style_analysis", {})
        style = StyleAnalysis(**{k: sa.get(k, "") for k in StyleAnalysis.__dataclass_fields__})

        sc = data.get("script", {})
        script = Script(script=sc.get("script", sc) if isinstance(sc, dict) else sc)

        tl = data.get("timeline", {})
        raw_segs = tl.get("timeline", tl) if isinstance(tl, dict) else tl
        segments = [
            TimelineSegment(**seg) if isinstance(seg, dict) else seg
            for seg in (raw_segs if isinstance(raw_segs, list) else [])
        ]
        timeline = Timeline(timeline=segments)

        ca = data.get("captions", {})
        raw_caps = ca.get("captions", ca) if isinstance(ca, dict) else ca
        caps = [
            CaptionChunk(**c) if isinstance(c, dict) else c
            for c in (raw_caps if isinstance(raw_caps, list) else [])
        ]
        captions = Captions(captions=caps)

        en = data.get("editing_notes", {})
        raw_notes = en.get("editing_notes", en) if isinstance(en, dict) else en
        notes = EditingNotes(
            editing_notes=raw_notes if isinstance(raw_notes, list) else []
        )

        return EditPlan(
            style_analysis=style,
            script=script,
            timeline=timeline,
            captions=captions,
            editing_notes=notes,
        )
