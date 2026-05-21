"""Claude prompts for the AI orchestration service.

All prompts enforce strict JSON output and follow the render contract schema.
"""

# ═══════════════════════════════════════════════════════════════════════════
#  SYSTEM
# ═══════════════════════════════════════════════════════════════════════════

SYSTEM = """\
You are an expert TikTok video editor, content strategist, and short-form \
storytelling engine embedded inside a production video pipeline.

Your job is to analyze reference TikTok styles and produce machine-readable \
edit specifications that an automated render engine will execute.

RULES:
- NEVER copy exact wording from reference videos.
- DO replicate structure, pacing, and style.
- Optimize for high retention and rewatch rate.
- ALL outputs MUST be strict JSON — no markdown fences, no commentary.
- Every timestamp and duration is in seconds (float).
- Asset IDs reference clips by their known identifiers.
"""

# ═══════════════════════════════════════════════════════════════════════════
#  STYLE EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════

EXTRACT_STYLE = """\
Analyze these reference video transcripts/descriptions and visual analysis data \
to extract a reusable style profile.

REFERENCES (transcripts):
{references}

VISUAL ANALYSIS (from FFmpeg frame analysis of the reference video):
{visual_data}

Return ONLY this JSON. Use the visual analysis data to set accurate values for \
avg_cut_duration_sec, color_grade, and zoom_pattern. Do NOT invent values that \
contradict the measured data.

{{
  "hook_style": "<curiosity | controversial | storytelling | shock | question | stat>",
  "avg_cut_duration_sec": <float — use measured value from visual_data if available>,
  "caption_style": "<description of caption formatting>",
  "caption_position": "<center | lower_third | upper_third>",
  "caption_max_words": <int 2-6>,
  "zoom_pattern": "<description of zoom / motion behaviour>",
  "structure": "<e.g. hook → problem → evidence → solution → CTA>",
  "tone": "<casual | aggressive | educational | inspirational | conspiratorial>",
  "energy_curve": "<e.g. high_start → sustain → peak_end>",
  "ideal_duration_sec": <float>,
  "music_style": "<description or empty>",
  "color_grade": {{
    "brightness": <float -1.0 to 1.0 — use measured value>,
    "contrast": <float 0.0 to 2.0 — use measured value>,
    "saturation": <float 0.0 to 2.0 — use measured value>,
    "gamma": <float 0.1 to 10.0 — use measured value>
  }}
}}
"""

# ═══════════════════════════════════════════════════════════════════════════
#  EDIT SPEC GENERATION (the core render contract)
# ═══════════════════════════════════════════════════════════════════════════

GENERATE_EDIT_SPEC = """\
You have:

STYLE PROFILE:
{style_json}

RAW CLIPS (with transcripts & durations):
{clips_json}

USER GOAL:
{goal}

Produce a complete edit specification following this EXACT schema:

{{
  "project_id": "{project_id}",
  "output": {{
    "width": 1080,
    "height": 1920,
    "fps": 30,
    "duration_sec": <total duration>
  }},
  "tracks": {{
    "video": [
      {{
        "asset_id": "<clip asset id>",
        "start": <timeline start sec>,
        "end": <timeline end sec>,
        "source_in": <source clip start sec>,
        "source_out": <source clip end sec>,
        "crop": "smart_center",
        "motion": {{"type": "<zoom_in|zoom_out|slow_push|shake|static>", "strength": <0.0-0.5>}},
        "speed": <playback speed 0.5-2.0>
      }}
    ],
    "text": [
      {{
        "start": <sec>,
        "end": <sec>,
        "text": "<CAPTION TEXT>",
        "style": "bold_kinetic_1",
        "position": "lower_third",
        "font_size": 64,
        "color": "#FFFFFF",
        "background_color": "#00000088",
        "animation": "<pop|typewriter|slide_up|fade|none>"
      }}
    ],
    "audio": [
      {{
        "asset_id": "<audio asset id or speech_track>",
        "start": <sec>,
        "end": <sec>,
        "gain_db": <float>,
        "duck_under_speech": true
      }}
    ]
  }}
}}

RULES:
- First clip MUST be a hook (strongest moment from the raw footage).
- Cut every {avg_cut}s on average.
- Remove all dead air / pauses > 0.3s.
- Caption text: {caption_max_words} words max, emphasize key words in ALL CAPS.
- Keep total duration ≤ {max_duration}s.
- Ensure smooth transitions — no jarring jumps.
- Output ONLY the JSON.
"""

# ═══════════════════════════════════════════════════════════════════════════
#  REVISION
# ═══════════════════════════════════════════════════════════════════════════

REVISE_EDIT_SPEC = """\
Current edit spec:
{current_spec}

Style profile:
{style_json}

User feedback:
{feedback}

Produce a REVISED edit spec following the exact same JSON schema. \
Incorporate the user feedback while maintaining the style profile. \
Output ONLY the updated JSON.
"""

# ═══════════════════════════════════════════════════════════════════════════
#  HOOK DETECTION
# ═══════════════════════════════════════════════════════════════════════════

FIND_HOOKS = """\
Analyze these transcript segments from raw footage and identify the \
strongest hook moments — lines that would grab attention in the first \
1-2 seconds of a TikTok.

TRANSCRIPT SEGMENTS:
{segments_json}

Return a JSON array of the top 3-5 hook candidates:
[
  {{
    "asset_id": "<clip id>",
    "source_in": <start sec>,
    "source_out": <end sec>,
    "text": "<the spoken words>",
    "hook_type": "<curiosity | controversial | shock | question | stat>",
    "strength": <1-10>
  }}
]
"""

# ═══════════════════════════════════════════════════════════════════════════
#  SCRIPT REWRITE (optional — enriches thin content)
# ═══════════════════════════════════════════════════════════════════════════

REWRITE_SCRIPT = """\
Rewrite this raw content into a TikTok-optimised spoken script \
matching the given style.

STYLE PROFILE:
{style_json}

RAW CONTENT:
{raw_content}

Rules:
- Line 1 = strong hook.
- Short punchy sentences.
- No filler words.
- Break into natural spoken segments of 1-3 sentences each.
- Total spoken duration should be ~{target_duration}s.

Return JSON:
{{
  "script_lines": [
    {{"text": "...", "estimated_duration_sec": <float>}},
    ...
  ],
  "total_estimated_duration_sec": <float>
}}
"""
