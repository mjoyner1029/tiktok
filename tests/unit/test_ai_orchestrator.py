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
