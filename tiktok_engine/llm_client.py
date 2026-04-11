"""LLM client abstraction — currently supports OpenAI-compatible APIs."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional

try:
    import openai
except ImportError:
    openai = None  # type: ignore[assignment]


def _strip_markdown_fences(text: str) -> str:
    """Remove ```json ... ``` wrappers that LLMs sometimes add."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


class LLMClient:
    """Thin wrapper around an OpenAI-compatible chat completion API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-4o",
        base_url: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ):
        if openai is None:
            raise ImportError(
                "openai package is required. Install with: pip install openai"
            )
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

        client_kwargs: Dict[str, Any] = {"api_key": self.api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        self.client = openai.OpenAI(**client_kwargs)

    def chat(self, system: str, user: str) -> str:
        """Send a chat completion request and return the assistant text."""
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""

    def chat_json(self, system: str, user: str) -> Dict[str, Any]:
        """Send a chat request and parse the response as JSON."""
        raw = self.chat(system, user)
        cleaned = _strip_markdown_fences(raw)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"LLM returned invalid JSON.\n--- RAW ---\n{raw}\n--- END ---"
            ) from exc
