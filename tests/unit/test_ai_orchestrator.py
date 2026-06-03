"""Unit tests for AI Orchestrator service."""

import json
from unittest.mock import Mock, patch

import pytest

from app.services.ai_orchestrator import AIOrchestrator


@pytest.mark.unit
@pytest.mark.ai
class TestAIOrchestrator:
    """Test AI orchestration service."""

    @pytest.fixture
    def orchestrator(self):
        """Create AI orchestrator with mock API key."""
        return AIOrchestrator(api_key="test-key", model="claude-3-sonnet-20240229")

    def test_initialization(self, orchestrator: AIOrchestrator):
        """Test orchestrator initialization."""
        assert orchestrator.client is not None
        assert orchestrator.model == "claude-3-sonnet-20240229"

    @patch("app.services.ai_orchestrator.get_cached")
    @patch("anthropic.Anthropic")
    async def test_extract_style(self, mock_anthropic, mock_get_cached, orchestrator: AIOrchestrator, mock_claude_response):
        """Test style extraction from references."""
        # Mock cache miss
        mock_get_cached.return_value = None
        
        # Mock the API response
        mock_client = Mock()
        mock_response = Mock()
        mock_response.content = [Mock(text=json.dumps(mock_claude_response))]
        mock_client.messages.create.return_value = mock_response
        orchestrator.client = mock_client
        
        references = [
            "Hook: Why 90% of people fail. Body: explaining the problem. CTA: follow for more."
        ]
        
        with patch("app.services.ai_orchestrator.set_cached") as mock_set_cached:
            result = await orchestrator.extract_style(references)
        
        assert result is not None
        assert "hook_style" in result
        assert mock_client.messages.create.called

    @patch("anthropic.Anthropic")
    def test_generate_edit_spec(
        self,
        mock_anthropic,
        orchestrator: AIOrchestrator,
        mock_claude_response,
        mock_edit_spec,
    ):
        """Test edit spec generation."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.content = [Mock(text=json.dumps(mock_edit_spec))]
        mock_client.messages.create.return_value = mock_response
        orchestrator.client = mock_client
        
        clips = [
            {
                "asset_id": "clip-1",
                "transcript": "This is my raw footage talking about the topic.",
                "duration_sec": 10.0,
            }
        ]
        
        result = orchestrator.generate_edit_spec(
            style_json=mock_claude_response,
            clips_json=clips,
            project_id="test-project",
            goal="Make it engaging",
        )
        
        assert result is not None
        assert "project_id" in result
        assert "tracks" in result

    @patch("anthropic.Anthropic")
    def test_revise_edit_spec(self, mock_anthropic, orchestrator, mock_edit_spec, mock_claude_response):
        """Test edit spec revision."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.content = [Mock(text=json.dumps(mock_edit_spec))]
        mock_client.messages.create.return_value = mock_response
        orchestrator.client = mock_client

        result = orchestrator.revise_edit_spec(
            current_spec=mock_edit_spec,
            style_json=mock_claude_response,
            feedback="Make cuts faster",
        )
        assert result is not None
        assert "tracks" in result

    @patch("anthropic.Anthropic")
    def test_find_hook_moments(self, mock_anthropic, orchestrator):
        """Test find_hook_moments returns list."""
        mock_client = Mock()
        mock_response = Mock()
        hooks = [{"timestamp": 1.0, "text": "Why 90% fail"}]
        mock_response.content = [Mock(text=json.dumps(hooks))]
        mock_client.messages.create.return_value = mock_response
        orchestrator.client = mock_client

        segments = [{"start": 0.0, "end": 2.0, "text": "hook moment"}]
        result = orchestrator.find_hook_moments(segments)
        assert isinstance(result, list)

    @patch("anthropic.Anthropic")
    def test_rewrite_script(self, mock_anthropic, orchestrator):
        """Test rewrite_script returns dict."""
        mock_client = Mock()
        mock_response = Mock()
        script = {"script": "Here is the rewritten script"}
        mock_response.content = [Mock(text=json.dumps(script))]
        mock_client.messages.create.return_value = mock_response
        orchestrator.client = mock_client

        result = orchestrator.rewrite_script(
            raw_content="bullet 1\nbullet 2",
            style_json={"tone": "educational"},
        )
        assert result is not None

    @patch("anthropic.Anthropic")
    def test_call_json_invalid_json_raises(self, mock_anthropic, orchestrator):
        """Test that invalid JSON from API raises ValueError."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.content = [Mock(text="This is definitely not JSON!")]
        mock_client.messages.create.return_value = mock_response
        orchestrator.client = mock_client

        with pytest.raises(ValueError, match="AI returned invalid JSON"):
            orchestrator._call_json("test prompt")

    @patch("app.services.ai_orchestrator.get_cached")
    @patch("anthropic.Anthropic")
    async def test_extract_style_cache_hit(self, mock_anthropic, mock_get_cached, orchestrator):
        """Test extract_style returns cached result without calling API."""
        cached = {"hook_style": "curiosity", "tone": "educational"}
        mock_get_cached.return_value = cached

        mock_client = Mock()
        orchestrator.client = mock_client

        result = await orchestrator.extract_style(["some transcript"])
        assert result == cached
        mock_client.messages.create.assert_not_called()

    @patch("anthropic.Anthropic")
    def test_run_full_pipeline_sync(self, mock_anthropic, orchestrator, mock_claude_response, mock_edit_spec):
        """Test run_full_pipeline_sync calls run_full_pipeline."""
        from unittest.mock import AsyncMock
        mock_pipeline = AsyncMock(return_value=(mock_claude_response, mock_edit_spec))
        orchestrator.run_full_pipeline = mock_pipeline

        style, spec = orchestrator.run_full_pipeline_sync(
            reference_transcripts=["ref transcript"],
            clips=[{"asset_id": "1", "transcript": "raw"}],
            project_id="test-proj",
        )
        assert style == mock_claude_response
        assert spec == mock_edit_spec

    @patch("app.services.ai_orchestrator.track_ai_request")
    @patch("anthropic.Anthropic")
    def test_call_tracks_error_on_failure(self, mock_anthropic, mock_track, orchestrator):
        """Test _call tracks error metric on failure."""
        mock_client = Mock()
        mock_client.messages.create.side_effect = RuntimeError("API error")
        orchestrator.client = mock_client

        with patch("app.services.ai_orchestrator.anthropic_circuit") as mock_circuit:
            mock_circuit.call.side_effect = RuntimeError("API error")
            with pytest.raises(RuntimeError):
                orchestrator._call("test prompt")
        mock_track.assert_called()
