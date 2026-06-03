"""Video ingestion — download TikTok/social URLs and extract style via Claude."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── yt-dlp helpers ────────────────────────────────────────────────────────


def _run(cmd: List[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout
    )


def fetch_metadata(url: str) -> Dict[str, Any]:
    """Pull yt-dlp metadata (no download) for a TikTok / social URL."""
    result = _run([
        "yt-dlp",
        "--dump-json",
        "--no-download",
        "--no-playlist",
        url,
    ])
    if result.returncode != 0:
        raise RuntimeError(
            f"yt-dlp metadata failed for {url!r}:\n{result.stderr.strip()}"
        )
    return json.loads(result.stdout)


def download_video(url: str, output_dir: str) -> Path:
    """Download a TikTok / social video into output_dir, return file path."""
    out_tmpl = str(Path(output_dir) / "%(id)s.%(ext)s")
    result = _run([
        "yt-dlp",
        "--no-playlist",
        "-o", out_tmpl,
        "--merge-output-format", "mp4",
        url,
    ], timeout=180)
    if result.returncode != 0:
        raise RuntimeError(
            f"yt-dlp download failed for {url!r}:\n{result.stderr.strip()}"
        )
    # find the downloaded file
    for line in result.stdout.splitlines():
        m = re.search(r'\[download\] Destination: (.+)', line)
        if m:
            return Path(m.group(1).strip())
        m = re.search(r'\[Merger\] Merging formats into "(.+)"', line)
        if m:
            return Path(m.group(1).strip())

    # fallback: find newest mp4 in output dir
    files = sorted(Path(output_dir).glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    if files:
        return files[-1]
    raise RuntimeError(f"Could not locate downloaded file for {url!r}")


def _probe_video(video_path: str) -> Optional[Dict[str, Any]]:
    """Run ffprobe for basic video stats (optional — skipped if not installed)."""
    try:
        result = _run([
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format", "-show_streams",
            video_path,
        ])
        if result.returncode == 0:
            return json.loads(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _extract_frames(video_path: str, output_dir: str, n: int = 6) -> List[str]:
    """Extract n evenly-spaced frames as scaled-down JPEGs. Returns paths."""
    frames = []
    try:
        probe = _probe_video(video_path)
        duration = 30.0
        if probe:
            duration = float(probe.get("format", {}).get("duration", 30))

        for i in range(n):
            t = duration * i / max(n - 1, 1)
            out = str(Path(output_dir) / f"frame_{i:02d}.jpg")
            result = _run([
                "ffmpeg", "-ss", str(t),
                "-i", video_path,
                "-vframes", "1",
                "-vf", "scale=540:-2",   # scale to 540px wide — plenty for Vision
                "-q:v", "6",             # moderate JPEG quality (~60-100 KB/frame)
                out, "-y",
            ])
            if result.returncode == 0:
                frames.append(out)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return frames


# ── style extraction via Claude ───────────────────────────────────────────

_STYLE_FROM_METADATA_PROMPT = """\
You are a TikTok editing expert. Analyze the following video metadata and \
transcript (if available) and produce a concise style description.

VIDEO METADATA:
{metadata}

TRANSCRIPT / SUBTITLES:
{transcript}

Return ONLY a JSON object with these keys:
{{
  "hook_style": "<curiosity | controversial | storytelling | shock | question | etc.>",
  "avg_cut_duration": "<e.g. 1.5s>",
  "caption_style": "<length, emphasis style, placement>",
  "zoom_pattern": "<e.g. zoom-in on key words, slow push, shake, etc.>",
  "structure": "<e.g. hook → problem → solution → CTA>",
  "tone": "<casual | aggressive | educational | inspirational | etc.>",
  "notes": "<any other notable style observations>"
}}"""

_STYLE_FROM_FRAMES_PROMPT = """\
You are a TikTok editing expert. Analyze these frames extracted from a TikTok \
video and describe its editing style with enough precision to REPLICATE it.

VIDEO METADATA:
{metadata}

Examine the frames carefully for:
1. TRANSITIONS: How does the video cut between scenes? Look for implied motion blur \
   (whip pan), color flashes, hard cuts, dissolves, slide directions.
2. PACING: Estimate avg_cut_duration from how much changes between sampled frames. \
   Fast chaos (<1s)? Rhythmic (1-2s)? Cinematic (3-5s)?
3. CAPTIONS: Exact position (bottom third, center, top), font size (small/medium/large), \
   ALL CAPS or mixed, word-by-word pop or full-phrase static, color/outline style.
4. MOTION: Is the camera moving (push in, pull out, handheld shake) or static? \
   Does it zoom on specific beats?
5. HOOK: What does the very first frame establish? Shock, curiosity, question?
6. COLOR: Warm/cool tint, high contrast, desaturated, natural, heavy LUT.
7. STRUCTURE: What is the narrative arc? (hook → problem → solution → CTA)

Return ONLY a JSON object — be SPECIFIC, not generic:
{{
  "hook_style": "<e.g. 'opens mid-action with no intro', 'question on screen', 'shocking claim'>",
  "avg_cut_duration": "<e.g. '1.2s'>",
  "transition_type": "<the PRIMARY transition used: cut | fade | flash_cut | whip_pan_left | whip_pan_right | swipe_left | swipe_right | swipe_up | dissolve | zoom_transition>",
  "caption_style": "<e.g. 'ALL CAPS bottom-third, large white bold text with black outline, 3-4 words per frame'>",
  "zoom_pattern": "<e.g. 'slow push-in on every clip', 'zoom-in punch on key words', 'static'>",
  "structure": "<e.g. 'hook → demo → reaction → CTA'>",
  "tone": "<casual | aggressive | educational | inspirational | hype | comedic>",
  "color_grade": "<e.g. 'warm orange tint', 'cool desaturated', 'natural', 'high contrast'>",
  "notes": "<any other concrete style observations: b-roll ratio, talking head %, sound design, text animations>"
}}"""

_ALL_FOOTAGE_VISION_PROMPT = """\
You are helping prepare raw footage for a TikTok edit. Look at these frames \
sampled from {n_files} footage file(s) and describe what's in each.

FILE METADATA:
{metadata}

For EACH file describe:
- Subject (who/what is on screen)
- What is happening or being demonstrated
- Setting / background
- Energy level and pace
- Whether someone is speaking to camera

Return ONLY a JSON object:
{{
  "files": [
    {{
      "filename": "<name>",
      "transcript": "<best description of what is being said or shown>",
      "key_moments": ["<moment>"],
      "footage_notes": "<subject, setting, actions, energy>"
    }}
  ]
}}"""


_MULTI_REFERENCE_STYLE_PROMPT = """\
You are a TikTok editing expert. Study frames from {n} reference TikTok videos \
and synthesize ONE precise editing style guide that can be used to REPLICATE the \
editing approach on new footage.

VIDEO METADATA:
{metadata}

Analyze ALL reference frames for:
1. TRANSITIONS — What happens AT the cut? Hard cut, whip pan blur, color flash, \
   slide/swipe direction, dissolve, zoom punch? Look for motion blur between clips.
2. PACING — How long does each clip last? Is it rhythm-matched to music?
3. CAPTIONS — Exact position, size, case style, how many words per caption.
4. MOTION — Camera movement within each clip (push in, pull out, handheld, static).
5. HOOK — How does the video open? What makes you keep watching?
6. STRUCTURE — What is the full narrative arc?
7. COLOR — Visual look and feel.
8. ENERGY — What makes this style feel the way it does?

Where references use different techniques, pick the most consistent or highest-impact approach.

Return ONLY a JSON object — give SPECIFIC, CONCRETE values (not vague descriptions):
{{
  "hook_style": "<specific opening technique>",
  "avg_cut_duration": "<e.g. '1.5s'>",
  "transition_type": "<PRIMARY transition: cut | fade | flash_cut | whip_pan_left | whip_pan_right | swipe_left | swipe_right | swipe_up | swipe_down | dissolve | zoom_transition>",
  "caption_style": "<exact: position, case, word count, font size, outline style>",
  "zoom_pattern": "<exact camera motion per clip>",
  "structure": "<full arc e.g. hook → problem → proof → CTA>",
  "tone": "<casual | aggressive | educational | inspirational | hype | comedic>",
  "color_grade": "<specific color treatment>",
  "notes": "<concrete additional observations>"
}}"""


_FOOTAGE_DESCRIPTION_VISION_PROMPT = """\
You are helping prepare raw footage for a TikTok edit. Look at these video frames \
and describe what's happening in this footage.

VIDEO METADATA:
{metadata}

Analyze each frame carefully:
- WHO is in the video (person, hands, objects, environment)
- WHAT is being done, demonstrated, or discussed
- WHERE the scene takes place (setting, background)
- KEY MOMENTS visible across the frames
- Energy level and pace (energetic, calm, intense, casual)
- Whether there is speech (person talking to camera, demonstrating, etc.)

Return ONLY a JSON object:
{{
  "duration_sec": 0,
  "transcript": "<best description of what is being said or shown>",
  "key_moments": ["<moment description>", "..."],
  "footage_notes": "<detailed: subject, actions, setting, energy, camera work>"
}}"""


_FOOTAGE_DESCRIPTION_PROMPT = """\
You are helping prepare raw footage for a TikTok edit. Analyze the following \
video metadata and transcript to produce a structured description of the \
available footage.

VIDEO METADATA:
{metadata}

TRANSCRIPT:
{transcript}

Return ONLY a JSON object:
{{
  "duration_sec": <number>,
  "transcript": "<full spoken content>",
  "key_moments": ["<timestamp> - <what happens>", ...],
  "footage_notes": "<scene descriptions, camera angles, quality notes>"
}}"""


def _build_metadata_summary(meta: Dict[str, Any]) -> str:
    fields = [
        ("title", meta.get("title", "")),
        ("uploader", meta.get("uploader", "")),
        ("duration", f"{meta.get('duration', 0)}s"),
        ("view_count", meta.get("view_count", "")),
        ("like_count", meta.get("like_count", "")),
        ("description", (meta.get("description") or "")[:300]),
        ("tags", ", ".join((meta.get("tags") or [])[:10])),
    ]
    return "\n".join(f"{k}: {v}" for k, v in fields if v)


def _build_transcript(meta: Dict[str, Any]) -> str:
    # yt-dlp may embed subtitles in metadata
    subs = meta.get("subtitles") or meta.get("automatic_captions") or {}
    # try English first
    for lang in ("en", "en-orig", list(subs.keys())[0] if subs else None):
        if not lang or lang not in subs:
            continue
        entries = subs[lang]
        if isinstance(entries, list):
            texts = []
            for e in entries:
                if isinstance(e, dict) and e.get("data"):
                    texts.append(e["data"])
                elif isinstance(e, str):
                    texts.append(e)
            if texts:
                return " ".join(texts)[:2000]
    return meta.get("description", "")[:500] or "(no transcript available)"


class VideoIngestor:
    """Download social videos, extract style, and describe footage using Claude."""

    def __init__(self, llm: Any):
        self.llm = llm

    def _extract_style_dict(self, url: str) -> tuple:
        """Download video, run Claude Vision on frames, return (style_dict, title)."""
        from tiktok_engine.prompts import SYSTEM_PROMPT
        from tiktok_engine.llm_client import _strip_markdown_fences

        meta = fetch_metadata(url)
        meta_summary = _build_metadata_summary(meta)
        title = meta.get("title", url)

        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                video_path = str(download_video(url, tmpdir))
            except Exception as e:
                logger.warning("Download failed (%s), falling back to metadata-only", e)
                transcript = _build_transcript(meta)
                prompt = _STYLE_FROM_METADATA_PROMPT.format(
                    metadata=meta_summary, transcript=transcript
                )
                raw = self.llm.chat(SYSTEM_PROMPT, prompt)
                cleaned = _strip_markdown_fences(raw)
                try:
                    return json.loads(cleaned), title
                except Exception:
                    return {"notes": raw}, title

            frames = _extract_frames(video_path, tmpdir, n=5)

            if frames:
                logger.info("Analyzing %d frames with Claude Vision...", len(frames))
                prompt = _STYLE_FROM_FRAMES_PROMPT.format(metadata=meta_summary)
                raw = self.llm.chat_with_images(SYSTEM_PROMPT, prompt, frames)
            else:
                transcript = _build_transcript(meta)
                prompt = _STYLE_FROM_METADATA_PROMPT.format(
                    metadata=meta_summary, transcript=transcript
                )
                raw = self.llm.chat(SYSTEM_PROMPT, prompt)

        from tiktok_engine.llm_client import _strip_markdown_fences
        cleaned = _strip_markdown_fences(raw)
        try:
            return json.loads(cleaned), title
        except Exception:
            return {"notes": raw}, title

    def analyze_all_reference_urls_dict(self, urls: list) -> dict:
        """Download all reference URLs, extract frames, analyze in ONE Vision call.

        With multiple references the frames are combined into a single request so
        we make exactly one Vision API call regardless of how many references there are.
        """
        import os as _os
        if not urls:
            return {}
        if len(urls) == 1:
            return self.analyze_reference_url_dict(urls[0])

        from tiktok_engine.prompts import SYSTEM_PROMPT
        from tiktok_engine.llm_client import _strip_markdown_fences

        FRAMES_PER_REF = 8  # 8 frames × 4 refs = 32 frames, ~3KB each → ~96KB total, safe
        meta_parts = []

        with tempfile.TemporaryDirectory() as tmpdir:
            all_frames: list = []
            for idx, url in enumerate(urls):
                ref_dir = _os.path.join(tmpdir, f"ref_{idx:02d}")
                _os.makedirs(ref_dir, exist_ok=True)
                try:
                    meta = fetch_metadata(url)
                    summary = _build_metadata_summary(meta)
                    meta_parts.append(
                        f"Reference {idx + 1} — {meta.get('title', '')[:60]}:\n{summary[:150]}"
                    )
                    try:
                        video_path = str(download_video(url, ref_dir))
                        frames = _extract_frames(video_path, ref_dir, n=FRAMES_PER_REF)
                        all_frames.extend(frames)
                        logger.info("Reference %d: %d frames extracted", idx + 1, len(frames))
                    except Exception as exc:
                        logger.warning("Reference %d download failed: %s", idx + 1, exc)
                except Exception as exc:
                    logger.warning("Reference %d metadata failed: %s", idx + 1, exc)
                    meta_parts.append(f"Reference {idx + 1}: {url}")

            meta_text = "\n\n".join(meta_parts) or "(metadata unavailable)"

            if all_frames:
                prompt = _MULTI_REFERENCE_STYLE_PROMPT.format(
                    n=len(urls), metadata=meta_text
                )
                raw = self.llm.chat_with_images(SYSTEM_PROMPT, prompt, all_frames)
            else:
                prompt = (
                    f"Synthesize a TikTok editing style from {len(urls)} reference videos:\n"
                    f"{meta_text}\n\n"
                    "Return JSON: {hook_style, avg_cut_duration, caption_style, "
                    "zoom_pattern, structure, tone, notes}"
                )
                raw = self.llm.chat(SYSTEM_PROMPT, prompt)

        cleaned = _strip_markdown_fences(raw)
        try:
            return json.loads(cleaned)
        except Exception:
            return {"notes": raw}

    def analyze_reference_url(self, url: str) -> str:
        """Download a TikTok/social URL, extract frames, and return a style description string."""
        logger.info("Analyzing reference URL: %s", url)
        style_dict, title = self._extract_style_dict(url)
        return self._format_style(style_dict, title)

    def analyze_reference_url_dict(self, url: str) -> dict:
        """Like analyze_reference_url but returns the raw style dict for direct use."""
        logger.info("Analyzing reference URL (dict): %s", url)
        style_dict, _ = self._extract_style_dict(url)
        return style_dict

    def _format_style(self, style_data, title: str) -> str:
        """Convert style dict (or raw JSON string) into a human-readable description."""
        if isinstance(style_data, str):
            from tiktok_engine.llm_client import _strip_markdown_fences
            cleaned = _strip_markdown_fences(style_data)
            try:
                style_data = json.loads(cleaned)
            except Exception:
                return f"Reference: {title}\n{style_data}"
        parts = [f"Reference TikTok: {str(title)[:80]}"]
        for k, v in style_data.items():
            parts.append(f"- {k.replace('_', ' ').title()}: {v}")
        return "\n".join(parts)

    def analyze_reference_file(self, video_path: str) -> str:
        """Analyze a local video file and return a style-description string."""
        logger.info("Analyzing local reference video: %s", video_path)
        probe = _probe_video(video_path)
        meta_summary = ""
        if probe:
            fmt = probe.get("format", {})
            meta_summary = (
                f"duration: {float(fmt.get('duration', 0)):.1f}s\n"
                f"format: {fmt.get('format_name', '')}\n"
                f"size: {int(fmt.get('size', 0)) // 1024}KB"
            )

        from tiktok_engine.prompts import SYSTEM_PROMPT

        # NOTE: chat_with_images must be called INSIDE the with block (frames live there)
        with tempfile.TemporaryDirectory() as tmpdir:
            frames = _extract_frames(video_path, tmpdir, n=8)
            if frames:
                prompt = _STYLE_FROM_FRAMES_PROMPT.format(metadata=meta_summary)
                raw = self.llm.chat_with_images(SYSTEM_PROMPT, prompt, frames)
            else:
                prompt = _STYLE_FROM_METADATA_PROMPT.format(
                    metadata=meta_summary or "(local file)",
                    transcript="(no transcript)",
                )
                raw = self.llm.chat(SYSTEM_PROMPT, prompt)

        return self._format_style(raw, Path(video_path).name)

    def describe_footage_url(self, url: str) -> str:
        """Download user footage from URL and return a content description."""
        logger.info("Fetching metadata for footage: %s", url)
        meta = fetch_metadata(url)
        meta_summary = _build_metadata_summary(meta)
        transcript = _build_transcript(meta)

        from tiktok_engine.prompts import SYSTEM_PROMPT
        prompt = _FOOTAGE_DESCRIPTION_PROMPT.format(
            metadata=meta_summary,
            transcript=transcript,
        )
        return self.llm.chat(SYSTEM_PROMPT, prompt)

    def describe_all_footage_files(self, video_paths: list) -> str:
        """Describe multiple footage files in ONE Claude Vision call (faster than per-file calls)."""
        if not video_paths:
            return "No footage provided."

        from tiktok_engine.prompts import SYSTEM_PROMPT
        from tiktok_engine.llm_client import _strip_markdown_fences

        FRAMES_PER_FILE = 2  # 2 frames × N files keeps the combined request small
        meta_parts = []
        for idx, vp in enumerate(video_paths):
            probe = _probe_video(vp)
            name = Path(vp).name
            dur = ""
            if probe:
                d = float(probe.get("format", {}).get("duration", 0))
                dur = f" ({d:.1f}s)"
            meta_parts.append(f"File {idx + 1}: {name}{dur}")

        meta = "\n".join(meta_parts)

        # Extract frames inside the with-block so they exist when chat_with_images is called
        with tempfile.TemporaryDirectory() as tmpdir:
            all_frames: list = []
            for vp in video_paths:
                frames = _extract_frames(vp, tmpdir, n=FRAMES_PER_FILE)
                all_frames.extend(frames)

            if not all_frames:
                prompt = f"Describe these footage files for TikTok editing:\n{meta}"
                return self.llm.chat(SYSTEM_PROMPT, prompt)

            prompt = _ALL_FOOTAGE_VISION_PROMPT.format(
                n_files=len(video_paths), metadata=meta
            )
            raw = self.llm.chat_with_images(SYSTEM_PROMPT, prompt, all_frames)

        cleaned = _strip_markdown_fences(raw)
        try:
            data = json.loads(cleaned)
            files = data.get("files", [])
            parts = []
            for f in files:
                parts.append(
                    f"File: {f.get('filename', '')}\n"
                    f"Content: {f.get('transcript', '')}\n"
                    f"Key moments: {', '.join(f.get('key_moments', []))}\n"
                    f"Notes: {f.get('footage_notes', '')}"
                )
            return "\n\n".join(parts) if parts else raw
        except Exception:
            return raw

    def describe_footage_file(self, video_path: str) -> str:
        """Describe user footage from a local file — uses Claude Vision when frames available."""
        logger.info("Describing local footage: %s", video_path)
        probe = _probe_video(video_path)
        meta_summary = ""
        if probe:
            fmt = probe.get("format", {})
            duration = float(fmt.get('duration', 0))
            meta_summary = f"duration: {duration:.1f}s\nformat: {fmt.get('format_name', '')}"

        from tiktok_engine.prompts import SYSTEM_PROMPT

        # Extract frames and describe visually — must call chat_with_images INSIDE with block
        with tempfile.TemporaryDirectory() as tmpdir:
            frames = _extract_frames(video_path, tmpdir, n=6)
            if frames:
                logger.info("Describing footage with Claude Vision (%d frames)...", len(frames))
                prompt = _FOOTAGE_DESCRIPTION_VISION_PROMPT.format(
                    metadata=meta_summary or f"Local file: {Path(video_path).name}"
                )
                return self.llm.chat_with_images(SYSTEM_PROMPT, prompt, frames)

        # Fallback: text-only description
        prompt = _FOOTAGE_DESCRIPTION_PROMPT.format(
            metadata=meta_summary or f"Local file: {Path(video_path).name}",
            transcript="(no transcript available for local file)",
        )
        return self.llm.chat(SYSTEM_PROMPT, prompt)
