"""System and step prompts for the OpenAI / LLM calls."""

SYSTEM_PROMPT = """\
You are an expert TikTok video editor, content strategist, and short-form \
storytelling engine. You analyze reference TikTok videos and transform raw \
user content into highly engaging TikTok edit plans that replicate the STYLE \
(not exact content) of the references.

RULES:
- DO NOT copy exact wording from reference videos.
- DO replicate structure, pacing, and style.
- Optimize for high retention and engagement.
- Output MUST be clean JSON only (no markdown fences, no extra text).
"""

# ── Step 1 ──────────────────────────────────────────────────────────────────

STYLE_ANALYSIS_PROMPT = """\
Analyze the following reference TikTok video descriptions / transcripts and \
extract the editing style.

REFERENCE VIDEOS:
{references}

Return ONLY a JSON object with these keys:
{{
  "hook_style": "<curiosity | controversial | storytelling | shock | etc.>",
  "avg_cut_duration": "<e.g. 1.5s>",
  "caption_style": "<length, emphasis style, placement>",
  "zoom_pattern": "<e.g. zoom-in on key words, slow push, shake, etc.>",
  "structure": "<e.g. hook → problem → solution → CTA>",
  "tone": "<casual | aggressive | educational | inspirational | etc.>"
}}
"""

# ── Step 2 ──────────────────────────────────────────────────────────────────

SCRIPT_TRANSFORM_PROMPT = """\
Rewrite the following raw user content into a TikTok script using the \
style described below.

STYLE:
{style_json}

RAW CONTENT:
{raw_content}

Rules:
- First line MUST be a strong hook.
- Keep sentences short and punchy.
- Remove filler words.
- Maximize retention and curiosity.
- Break into natural spoken segments.

Return ONLY a JSON object:
{{
  "script": [
    "line 1 (hook)",
    "line 2",
    "..."
  ]
}}
"""

# ── Step 3 ──────────────────────────────────────────────────────────────────

TIMELINE_PROMPT = """\
Convert the following TikTok script into a shot-by-shot edit timeline.

STYLE:
{style_json}

SCRIPT:
{script_json}

Each segment must include: start, end, text, visual, caption, motion.

Return ONLY a JSON object:
{{
  "timeline": [
    {{
      "start": "0.00",
      "end": "2.00",
      "text": "hook line",
      "visual": "talking head clip 1",
      "caption": "THIS IS CRAZY",
      "motion": "zoom in"
    }}
  ]
}}
"""

# ── Step 4 ──────────────────────────────────────────────────────────────────

CAPTION_STRATEGY_PROMPT = """\
Generate captions optimized for TikTok retention from the following timeline.

TIMELINE:
{timeline_json}

Rules:
- 2–5 words per caption chunk.
- Emphasize key words in ALL CAPS.
- Sync captions to speech beats.
- Avoid full sentences.

Return ONLY a JSON object:
{{
  "captions": [
    {{"time": "0.5", "text": "THIS IS CRAZY"}},
    {{"time": "1.8", "text": "NO ONE TALKS ABOUT THIS"}}
  ]
}}
"""

# ── Step 5 ──────────────────────────────────────────────────────────────────

EDITING_NOTES_PROMPT = """\
Provide execution-ready editing instructions for the following TikTok edit plan.

STYLE:
{style_json}

TIMELINE:
{timeline_json}

Include guidance on:
- Cut frequency
- Where to remove pauses
- Where to add emphasis
- Where to insert zooms
- Energy pacing guidance

Return ONLY a JSON object:
{{
  "editing_notes": [
    "Cut every 1–2 seconds",
    "Remove all dead air",
    "Add zoom on key phrases",
    "Keep energy high throughout"
  ]
}}
"""

# ── Combined single-shot prompt (alternative) ──────────────────────────────

COMBINED_PROMPT = """\
Analyze the reference videos, transform the raw content, and produce a \
complete TikTok edit plan.

REFERENCE VIDEOS (style only):
{references}

RAW USER CONTENT:
{raw_content}

Return ONE combined JSON object with these top-level keys:
- "style_analysis"  (hook_style, avg_cut_duration, caption_style, zoom_pattern, structure, tone)
- "script"          (script: list of lines)
- "timeline"        (timeline: list of segments with start, end, text, visual, caption, motion)
- "captions"        (captions: list of {{time, text}})
- "editing_notes"   (editing_notes: list of strings)

Output ONLY valid JSON. No markdown fences, no commentary.
"""
