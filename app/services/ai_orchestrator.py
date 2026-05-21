"""AI Orchestration Service — Claude integration for style extraction,
edit spec generation, revisions, and hook detection.

Uses Anthropic's messages API with tool use + structured outputs.
Includes caching, retry logic, and circuit breaker protection.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

import anthropic

from app.config import get_settings
from app.cache import cached, cache_key, get_cached, set_cached
from app.error_handling import retry_on_api_error, anthropic_circuit
from app.metrics import track_ai_request
from app.prompts import (
    EXTRACT_STYLE,
    FIND_HOOKS,
    GENERATE_EDIT_SPEC,
    REVISE_EDIT_SPEC,
    REWRITE_SCRIPT,
    SYSTEM,
)

logger = logging.getLogger(__name__)
settings = get_settings()


# ── helpers ──────────────────────────────────────────────────────────────────

def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_json(raw: str) -> dict | list:
    cleaned = _strip_fences(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Sometimes Claude wraps JSON in a leading sentence — try to find it
        match = re.search(r"[\[{]", cleaned)
        if match:
            candidate = cleaned[match.start():]
            return json.loads(candidate)
        raise


# ── client ───────────────────────────────────────────────────────────────────

class AIOrchestrator:
    """Thin wrapper around Anthropic's messages API tuned for the pipeline."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ):
        self.client = anthropic.Anthropic(
            api_key=api_key or settings.anthropic_api_key
        )
        self.model = model or settings.anthropic_model
        self.max_tokens = max_tokens or settings.anthropic_max_tokens
        self.temperature = temperature if temperature is not None else settings.anthropic_temperature

    # ── low-level ────────────────────────────────────────────────────────

    @retry_on_api_error(max_attempts=3)
    def _call(self, user_prompt: str, system: str = SYSTEM) -> str:
        """Send a chat message and return the assistant's text."""
        start_time = time.time()
        
        try:
            resp = anthropic_circuit.call(
                self.client.messages.create,
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                system=system,
                messages=[{"role": "user", "content": user_prompt}],
            )
            
            # Extract text from content blocks
            parts = []
            for block in resp.content:
                if hasattr(block, "text"):
                    parts.append(block.text)
            
            duration = time.time() - start_time
            track_ai_request("anthropic", self.model, "success", duration)
            
            return "\n".join(parts)
        
        except Exception as exc:
            duration = time.time() - start_time
            track_ai_request("anthropic", self.model, "error", duration)
            logger.error(f"Claude API call failed: {exc}")
            raise

    def _call_json(self, user_prompt: str, system: str = SYSTEM) -> Any:
        raw = self._call(user_prompt, system)
        try:
            return _parse_json(raw)
        except json.JSONDecodeError as exc:
            logger.error("Claude returned invalid JSON:\n%s", raw[:2000])
            raise ValueError("AI returned invalid JSON") from exc

    # ── pipeline steps ───────────────────────────────────────────────────

    async def extract_style(self, reference_transcripts: List[str], visual_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Step 1: Analyse reference videos and extract style profile.

        Args:
            reference_transcripts: Transcript text from each reference video.
            visual_data: Aggregated visual style data from FFmpeg analysis
                (cut timestamps, avg_cut_duration_sec, color_grade, etc.).

        Cached by hash of reference transcripts for 1 hour.
        """
        # Check cache first
        cache_id = cache_key("style", *reference_transcripts)
        cached_result = await get_cached(cache_id)
        if cached_result:
            logger.info("Style profile cache hit")
            return cached_result

        refs_block = "\n\n".join(
            f"--- Reference {i} ---\n{t}"
            for i, t in enumerate(reference_transcripts, 1)
        )

        visual_block = json.dumps(visual_data, indent=2) if visual_data else "Not available — no video file provided for analysis."
        prompt = EXTRACT_STYLE.format(references=refs_block, visual_data=visual_block)
        result = self._call_json(prompt)

        # Cache for 1 hour
        await set_cached(cache_id, result, ttl=3600)

        logger.info("Style extracted: hook=%s, tone=%s", result.get("hook_style"), result.get("tone"))
        return result

    def generate_edit_spec(
        self,
        style_json: Dict[str, Any],
        clips_json: List[Dict[str, Any]],
        project_id: str,
        goal: str = "",
        max_duration: float = 60.0,
    ) -> Dict[str, Any]:
        """Step 2 — Produce a render-contract–compliant edit spec."""
        prompt = GENERATE_EDIT_SPEC.format(
            style_json=json.dumps(style_json, indent=2),
            clips_json=json.dumps(clips_json, indent=2),
            project_id=project_id,
            goal=goal or "Create an engaging TikTok in this style",
            avg_cut=style_json.get("avg_cut_duration_sec", 1.5),
            caption_max_words=style_json.get("caption_max_words", 4),
            max_duration=max_duration,
        )
        result = self._call_json(prompt)
        logger.info(
            "Edit spec generated: %d video clips, %d captions, duration=%.1fs",
            len(result.get("tracks", {}).get("video", [])),
            len(result.get("tracks", {}).get("text", [])),
            result.get("output", {}).get("duration_sec", 0),
        )
        return result

    def revise_edit_spec(
        self,
        current_spec: Dict[str, Any],
        style_json: Dict[str, Any],
        feedback: str,
    ) -> Dict[str, Any]:
        """Revise an existing edit spec based on user feedback."""
        prompt = REVISE_EDIT_SPEC.format(
            current_spec=json.dumps(current_spec, indent=2),
            style_json=json.dumps(style_json, indent=2),
            feedback=feedback,
        )
        return self._call_json(prompt)

    def find_hook_moments(
        self, segments: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Identify the strongest hook moments from transcript segments."""
        prompt = FIND_HOOKS.format(segments_json=json.dumps(segments, indent=2))
        result = self._call_json(prompt)
        if isinstance(result, list):
            return result
        return result.get("hooks", result.get("candidates", []))

    def rewrite_script(
        self,
        raw_content: str,
        style_json: Dict[str, Any],
        target_duration: float = 30.0,
    ) -> Dict[str, Any]:
        """Rewrite thin / bullet-point content into a spoken TikTok script."""
        prompt = REWRITE_SCRIPT.format(
            style_json=json.dumps(style_json, indent=2),
            raw_content=raw_content,
            target_duration=target_duration,
        )
        return self._call_json(prompt)

    # ── convenience: full pipeline in one call ───────────────────────────

    async def run_full_pipeline(
        self,
        reference_transcripts: List[str],
        clips: List[Dict[str, Any]],
        project_id: str,
        goal: str = "",
        max_duration: float = 60.0,
        visual_data: Optional[Dict[str, Any]] = None,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """Run style extraction → edit spec generation.

        Returns (style_profile, edit_spec).
        """
        style = await self.extract_style(reference_transcripts, visual_data=visual_data)
        spec = self.generate_edit_spec(
            style_json=style,
            clips_json=clips,
            project_id=project_id,
            goal=goal,
            max_duration=max_duration,
        )
        return style, spec

    def run_full_pipeline_sync(
        self,
        reference_transcripts: List[str],
        clips: List[Dict[str, Any]],
        project_id: str,
        goal: str = "",
        max_duration: float = 60.0,
        visual_data: Optional[Dict[str, Any]] = None,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """Synchronous wrapper for Celery tasks.

        Returns (style_profile, edit_spec).
        """
        import asyncio
        return asyncio.run(self.run_full_pipeline(
            reference_transcripts, clips, project_id, goal, max_duration, visual_data=visual_data
        ))
