"""Tests for tiktok_engine package: LLMClient, EditPlanPipeline, models."""

from __future__ import annotations

import json
from dataclasses import asdict
from unittest.mock import patch, Mock, MagicMock
import pytest

# ---------------------------------------------------------------------------
# tiktok_engine.models
# ---------------------------------------------------------------------------

from tiktok_engine.models import (
    StyleAnalysis,
    Script,
    TimelineSegment,
    Timeline,
    CaptionChunk,
    Captions,
    EditingNotes,
    EditPlan,
)


class TestDataclasses:
    def test_style_analysis_defaults(self):
        sa = StyleAnalysis()
        assert sa.hook_style == ""
        assert sa.avg_cut_duration == ""

    def test_style_analysis_values(self):
        sa = StyleAnalysis(hook_style="curiosity", tone="educational")
        assert sa.hook_style == "curiosity"
        assert sa.tone == "educational"

    def test_script_default(self):
        s = Script()
        assert s.script == []

    def test_script_with_values(self):
        s = Script(script=["Line 1", "Line 2"])
        assert len(s.script) == 2

    def test_timeline_segment_defaults(self):
        seg = TimelineSegment()
        assert seg.start == "0.00"
        assert seg.end == "0.00"

    def test_timeline_segment_values(self):
        seg = TimelineSegment(start="1.0", end="3.5", text="intro", visual="close-up")
        assert seg.start == "1.0"
        assert seg.end == "3.5"

    def test_timeline_default(self):
        tl = Timeline()
        assert tl.timeline == []

    def test_caption_chunk_defaults(self):
        cc = CaptionChunk()
        assert cc.time == "0.0"
        assert cc.text == ""

    def test_captions_default(self):
        c = Captions()
        assert c.captions == []

    def test_editing_notes_default(self):
        en = EditingNotes()
        assert en.editing_notes == []

    def test_edit_plan_to_dict(self):
        plan = EditPlan(
            style_analysis=StyleAnalysis(hook_style="curiosity"),
            script=Script(script=["Line 1"]),
        )
        d = plan.to_dict()
        assert "style_analysis" in d
        assert "script" in d
        assert "timeline" in d
        assert "captions" in d
        assert "editing_notes" in d
        assert d["style_analysis"]["hook_style"] == "curiosity"
        assert d["script"]["script"] == ["Line 1"]

    def test_edit_plan_to_json(self):
        plan = EditPlan()
        json_str = plan.to_json()
        data = json.loads(json_str)
        assert "style_analysis" in data

    def test_edit_plan_to_json_indent(self):
        plan = EditPlan()
        json_str = plan.to_json(indent=4)
        assert "\n" in json_str  # indented JSON has newlines

    def test_edit_plan_nested_dataclass_serialization(self):
        plan = EditPlan(
            timeline=Timeline(timeline=[
                TimelineSegment(start="0.0", end="2.0", text="hook"),
            ]),
            captions=Captions(captions=[
                CaptionChunk(time="0.5", text="WATCH THIS"),
            ]),
        )
        d = plan.to_dict()
        assert d["timeline"]["timeline"][0]["text"] == "hook"
        assert d["captions"]["captions"][0]["text"] == "WATCH THIS"


# ---------------------------------------------------------------------------
# tiktok_engine.llm_client
# ---------------------------------------------------------------------------

from tiktok_engine.llm_client import LLMClient, _strip_markdown_fences


class TestStripMarkdownFences:
    def test_strips_json_fences(self):
        text = "```json\n{\"key\": \"value\"}\n```"
        result = _strip_markdown_fences(text)
        assert result == '{"key": "value"}'

    def test_strips_plain_fences(self):
        text = "```\n{\"key\": \"value\"}\n```"
        result = _strip_markdown_fences(text)
        assert result == '{"key": "value"}'

    def test_no_fences(self):
        text = '{"key": "value"}'
        result = _strip_markdown_fences(text)
        assert result == text

    def test_strips_whitespace(self):
        text = "  \n{\"key\": \"value\"}  \n"
        result = _strip_markdown_fences(text)
        assert result == '{"key": "value"}'


class TestLLMClient:
    def _make_client(self):
        return LLMClient(api_key="test-key")

    def _mock_response(self, text: str):
        response = Mock()
        response.choices = [Mock()]
        response.choices[0].message = Mock()
        response.choices[0].message.content = text
        return response

    def test_init_defaults(self):
        client = LLMClient(api_key="test-key")
        assert client.api_key == "test-key"

    def test_init_custom_params(self):
        client = LLMClient(
            api_key="test-key",
            model="gpt-4",
            temperature=0.5,
            max_tokens=1000,
        )
        assert client.model == "gpt-4"
        assert client.temperature == 0.5
        assert client.max_tokens == 1000

    def test_chat_returns_text(self):
        client = self._make_client()
        mock_response = self._mock_response("Hello, I'm an AI!")

        client.client = Mock()
        client.client.chat.completions.create.return_value = mock_response
        result = client.chat(system="You are helpful", user="Hello")
        assert result == "Hello, I'm an AI!"

    def test_chat_json_returns_dict(self):
        client = self._make_client()
        json_response = '{"hook_style": "curiosity", "tone": "educational"}'
        mock_response = self._mock_response(json_response)

        client.client = Mock()
        client.client.chat.completions.create.return_value = mock_response
        result = client.chat_json(system="You are helpful", user="Analyze this")
        assert result["hook_style"] == "curiosity"
        assert result["tone"] == "educational"

    def test_chat_json_strips_fences(self):
        client = self._make_client()
        json_with_fences = '```json\n{"hook_style": "shock"}\n```'
        mock_response = self._mock_response(json_with_fences)

        client.client = Mock()
        client.client.chat.completions.create.return_value = mock_response
        result = client.chat_json(system="sys", user="user")
        assert result["hook_style"] == "shock"

    def test_chat_json_invalid_json_raises(self):
        client = self._make_client()
        mock_response = self._mock_response("This is not JSON")

        client.client = Mock()
        client.client.chat.completions.create.return_value = mock_response
        with pytest.raises((json.JSONDecodeError, ValueError, Exception)):
            client.chat_json(system="sys", user="user")


# ---------------------------------------------------------------------------
# tiktok_engine.pipeline
# ---------------------------------------------------------------------------

from tiktok_engine.pipeline import EditPlanPipeline


class TestEditPlanPipeline:
    def _make_pipeline(self):
        mock_client = Mock(spec=LLMClient)
        return EditPlanPipeline(llm=mock_client), mock_client

    def test_step1_style_analysis(self):
        pipeline, mock_client = self._make_pipeline()
        mock_client.chat_json.return_value = {
            "hook_style": "curiosity",
            "avg_cut_duration": "1.5s",
            "caption_style": "bold caps",
            "zoom_pattern": "zoom in",
            "structure": "hook → CTA",
            "tone": "educational",
        }
        refs = ["Reference transcript content here."]
        result = pipeline.step1_style_analysis(refs)
        assert isinstance(result, StyleAnalysis)
        assert result.hook_style == "curiosity"
        mock_client.chat_json.assert_called_once()

    def test_step2_script(self):
        pipeline, mock_client = self._make_pipeline()
        mock_client.chat_json.return_value = {
            "script": ["Opening hook!", "Main content.", "Call to action."],
        }
        style = StyleAnalysis(hook_style="curiosity")
        result = pipeline.step2_script(style=style, raw_content="raw footage transcript")
        assert isinstance(result, Script)
        assert len(result.script) == 3

    def test_step3_timeline(self):
        pipeline, mock_client = self._make_pipeline()
        mock_client.chat_json.return_value = {
            "timeline": [
                {"start": "0.0", "end": "2.5", "text": "Hook", "visual": "close-up", "caption": "HOOK", "motion": "zoom_in"},
                {"start": "2.5", "end": "5.0", "text": "Content", "visual": "medium", "caption": "CONTENT", "motion": "static"},
            ],
        }
        style = StyleAnalysis()
        script = Script(script=["Hook!", "Content."])
        result = pipeline.step3_timeline(style=style, script=script)
        assert isinstance(result, Timeline)
        assert len(result.timeline) == 2

    def test_step4_captions(self):
        pipeline, mock_client = self._make_pipeline()
        mock_client.chat_json.return_value = {
            "captions": [
                {"time": "0.5", "text": "WATCH THIS"},
                {"time": "2.0", "text": "AND THIS"},
            ],
        }
        style = StyleAnalysis()
        timeline = Timeline()
        result = pipeline.step4_captions(timeline=timeline)
        assert isinstance(result, Captions)
        assert len(result.captions) == 2

    def test_step5_editing_notes(self):
        pipeline, mock_client = self._make_pipeline()
        mock_client.chat_json.return_value = {
            "editing_notes": ["Cut quickly", "Add transition"],
        }
        plan = EditPlan()
        # step5_editing_notes takes style and timeline, not plan
        style = StyleAnalysis()
        timeline = Timeline()
        result = pipeline.step5_editing_notes(style=style, timeline=timeline)
        assert isinstance(result, EditingNotes)
        assert len(result.editing_notes) == 2

    def test_run_calls_all_steps(self):
        pipeline, mock_client = self._make_pipeline()
        mock_client.chat_json.side_effect = [
            # step1
            {"hook_style": "curiosity", "avg_cut_duration": "1.5s", "caption_style": "bold",
             "zoom_pattern": "zoom", "structure": "h→c", "tone": "edu"},
            # step2
            {"script": ["Line 1"]},
            # step3
            {"timeline": [{"start": "0.0", "end": "1.0", "text": "x", "visual": "y", "caption": "c", "motion": "static"}]},
            # step4
            {"captions": [{"time": "0.0", "text": "x"}]},
            # step5
            {"editing_notes": ["note 1"]},
        ]
        refs = ["Reference transcript"]
        raw_content = "Raw clip transcripts"
        result = pipeline.run(refs, raw_content)
        assert isinstance(result, EditPlan)
        assert mock_client.chat_json.call_count == 5

    def test_run_combined(self):
        pipeline, mock_client = self._make_pipeline()
        combined_response = {
            "style_analysis": {
                "hook_style": "shock", "avg_cut_duration": "1.0s",
                "caption_style": "caps", "zoom_pattern": "zoom in",
                "structure": "h→c", "tone": "casual",
            },
            "script": {"script": ["line 1", "line 2"]},
            "timeline": {"timeline": [
                {"start": "0.0", "end": "2.0", "text": "hook", "visual": "face", "caption": "HOOK", "motion": "zoom_in"},
            ]},
            "captions": {"captions": [{"time": "0.5", "text": "HOOK"}]},
            "editing_notes": {"editing_notes": ["cut fast"]},
        }
        mock_client.chat_json.return_value = combined_response
        result = pipeline.run_combined(["ref transcript"], "raw content")
        assert isinstance(result, EditPlan)
        assert result.style_analysis.hook_style == "shock"
        mock_client.chat_json.assert_called_once()

    def test_parse_combined_extracts_fields(self):
        data = {
            "style_analysis": {
                "hook_style": "question", "avg_cut_duration": "2.0s",
                "caption_style": "minimal", "zoom_pattern": "pan",
                "structure": "h→s→c", "tone": "professional",
            },
            "script": {"script": ["a", "b"]},
            "timeline": {"timeline": []},
            "captions": {"captions": []},
            "editing_notes": {"editing_notes": []},
        }
        result = EditPlanPipeline._parse_combined(data)
        assert isinstance(result, EditPlan)
        assert result.style_analysis.hook_style == "question"
        assert result.script.script == ["a", "b"]
