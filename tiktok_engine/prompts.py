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
You are given a pre-extracted editing style from reference TikToks and raw footage \
descriptions. Create an edit plan that FAITHFULLY REPLICATES the style — same \
transitions, same pacing, same caption style, same energy.

═══════════════════════════════════════════════════════
EXTRACTED REFERENCE STYLE (treat as law):
═══════════════════════════════════════════════════════
{style_json}

═══════════════════════════════════════════════════════
FOOTAGE AVAILABLE:
═══════════════════════════════════════════════════════
{raw_content}

═══════════════════════════════════════════════════════
MANDATORY STYLE APPLICATION RULES:
═══════════════════════════════════════════════════════
1. TRANSITIONS: Every segment's "transition" field MUST use transition_type from \
   the style (e.g. if transition_type is "whip_pan_left", use "whip_pan_left" on \
   every segment unless a specific segment warrants a different cut). Allowed values: \
   cut, fade, flash_cut, whip_pan_left, whip_pan_right, swipe_left, swipe_right, \
   swipe_up, swipe_down, dissolve, zoom_transition.
2. TIMING: Every segment's duration (end - start) MUST equal avg_cut_duration. \
   No exceptions. Build cumulative timestamps starting from 0.
3. MOTION: Every segment's "motion" field MUST apply zoom_pattern \
   (e.g. "slow push in", "zoom in", "static").
4. CAPTIONS: Apply caption_style EXACTLY — ALL CAPS if specified, correct position, \
   word count per frame. Write captions as they would appear ON SCREEN, not as narration.
5. HOOK: Segment 0 must implement hook_style directly (e.g. if hook is \
   "opens mid-action", segment 0 caption should be a shocking claim or question).
6. STRUCTURE: Follow the structure arc across ALL segments.

Return ONE combined JSON object with exactly these keys:
- "style_analysis"  — copy values directly from the extracted style (do NOT change them)
- "script"          — {{"script": [spoken lines matching the structure]}}
- "timeline"        — {{"timeline": [segments, see format below]}}
- "captions"        — {{"captions": [{{"time": "0.5", "text": "ON SCREEN TEXT"}}]}}
- "editing_notes"   — {{"editing_notes": ["concrete execution note", ...]}}

Each timeline segment MUST have ALL these fields:
{{
  "start": "0.00",        ← cumulative seconds from 0
  "end": "1.50",          ← start + avg_cut_duration
  "text": "...",          ← spoken words
  "visual": "footage_03.MOV - brief description of what to show",  ← MUST start with exact filename from the ALL AVAILABLE CLIPS list above
  "caption": "...",       ← ON-SCREEN TEXT following caption_style
  "motion": "...",        ← camera motion following zoom_pattern
  "transition": "..."     ← transition INTO this segment following transition_type
}}

CRITICAL: The "visual" field MUST begin with the exact filename (e.g. "footage_07.MP4") from
the ALL AVAILABLE CLIPS list. The renderer uses this to select the right clip. Distribute
segments across MANY different clips — do not reuse the same clip more than 3 times.

Output ONLY valid JSON. No markdown fences, no commentary.
"""
