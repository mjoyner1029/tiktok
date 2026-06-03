"""LLM client abstraction — supports OpenAI-compatible APIs and Anthropic Claude."""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import openai
except ImportError:
    openai = None  # type: ignore[assignment]

try:
    import anthropic as anthropic_sdk
except ImportError:
    anthropic_sdk = None  # type: ignore[assignment]


def _strip_markdown_fences(text: str) -> str:
    """Remove ```json ... ``` wrappers that LLMs sometimes add."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _is_claude_model(model: str) -> bool:
    return model.startswith("claude")


def _encode_image(path: str) -> str:
    return base64.standard_b64encode(Path(path).read_bytes()).decode("utf-8")


class LLMClient:
    """Thin wrapper around OpenAI-compatible APIs and Anthropic Claude."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-4o",
        base_url: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._provider = "anthropic" if _is_claude_model(model) else "openai"

        if self._provider == "anthropic":
            if anthropic_sdk is None:
                raise ImportError(
                    "anthropic package is required. Install with: pip install anthropic"
                )
            self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY") or None
            self.client = anthropic_sdk.Anthropic(api_key=self.api_key)
        else:
            if openai is None:
                raise ImportError(
                    "openai package is required. Install with: pip install openai"
                )
            self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
            client_kwargs: Dict[str, Any] = {"api_key": self.api_key}
            if base_url:
                client_kwargs["base_url"] = base_url
            self.client = openai.OpenAI(**client_kwargs)

    def chat(self, system: str, user: str, max_tokens: Optional[int] = None) -> str:
        """Send a chat completion request and return the assistant text."""
        mt = max_tokens or self.max_tokens
        if self._provider == "anthropic":
            response = self.client.messages.create(
                model=self.model,
                max_tokens=mt,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return response.content[0].text
        else:
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                max_tokens=mt,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            return response.choices[0].message.content or ""

    def chat_with_images(self, system: str, user_text: str, image_paths: List[str]) -> str:
        """Send a message with images (vision). Returns the assistant text."""
        if self._provider == "anthropic":
            content: List[Any] = []
            for path in image_paths:
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": _encode_image(path),
                    },
                })
            content.append({"type": "text", "text": user_text})
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": content}],
            )
            return response.content[0].text
        else:
            content_parts: List[Any] = [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{_encode_image(p)}"
                    },
                }
                for p in image_paths
            ]
            content_parts.append({"type": "text", "text": user_text})
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": content_parts},
                ],
            )
            return response.choices[0].message.content or ""

    def chat_json(self, system: str, user: str, max_tokens: Optional[int] = None) -> Dict[str, Any]:
        """Send a chat request and parse the response as JSON."""
        raw = self.chat(system, user, max_tokens=max_tokens)
        cleaned = _strip_markdown_fences(raw)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"LLM returned invalid JSON.\n--- RAW ---\n{raw}\n--- END ---"
            ) from exc
