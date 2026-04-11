"""Data models for the TikTok edit plan pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Optional
import json


# ── Step 1: Style Analysis ──────────────────────────────────────────────────

@dataclass
class StyleAnalysis:
    hook_style: str = ""
    avg_cut_duration: str = ""
    caption_style: str = ""
    zoom_pattern: str = ""
    structure: str = ""
    tone: str = ""


# ── Step 2: Script ──────────────────────────────────────────────────────────

@dataclass
class Script:
    script: List[str] = field(default_factory=list)


# ── Step 3: Timeline Segment ────────────────────────────────────────────────

@dataclass
class TimelineSegment:
    start: str = "0.00"
    end: str = "0.00"
    text: str = ""
    visual: str = ""
    caption: str = ""
    motion: str = ""


@dataclass
class Timeline:
    timeline: List[TimelineSegment] = field(default_factory=list)


# ── Step 4: Caption ─────────────────────────────────────────────────────────

@dataclass
class CaptionChunk:
    time: str = "0.0"
    text: str = ""


@dataclass
class Captions:
    captions: List[CaptionChunk] = field(default_factory=list)


# ── Step 5: Editing Notes ───────────────────────────────────────────────────

@dataclass
class EditingNotes:
    editing_notes: List[str] = field(default_factory=list)


# ── Combined Output ─────────────────────────────────────────────────────────

@dataclass
class EditPlan:
    style_analysis: StyleAnalysis = field(default_factory=StyleAnalysis)
    script: Script = field(default_factory=Script)
    timeline: Timeline = field(default_factory=Timeline)
    captions: Captions = field(default_factory=Captions)
    editing_notes: EditingNotes = field(default_factory=EditingNotes)

    def to_dict(self) -> dict:
        return {
            "style_analysis": asdict(self.style_analysis),
            "script": asdict(self.script),
            "timeline": asdict(self.timeline),
            "captions": asdict(self.captions),
            "editing_notes": asdict(self.editing_notes),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
