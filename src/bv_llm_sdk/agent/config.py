"""Agent settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from typing import Literal

Backend = Literal["ollama", "openai", "anthropic"]

DEFAULT_MODELS = {
    "ollama": "qwen3-coder:30b",  # Stored on OSC. Passed all 7 eval scenarios.
    "openai": "gpt-oss:20b",
    "anthropic": "claude-sonnet-5-5",
}
DEFAULT_BASE_URLS = {
    # The bv_llm_host tunnel forwards to this address.
    "ollama": "http://localhost:11434",
    "openai": "http://localhost:8000/v1",
    "anthropic": None,
}


@dataclass
class AgentConfig:
    toolkit: str = "drone"  # Which robot toolkit to use.
    backend: Backend = "ollama"
    model: str | None = None  # None uses the default model for the backend.
    base_url: str | None = None  # None uses the default URL for the backend.
    # For Claude and OpenAI. Read from the usual environment variable if unset.
    api_key: str | None = None
    toolset: str = "full"  # Which tools to offer. The drone has a core set for small models.
    num_ctx: int = 32768  # Ollama context window. The full toolset alone is about 7k tokens.
    # Thinking setting. True or False or a reasoning level for gpt-oss.
    think: bool | str | None = None
    # Whether the model takes images. None asks Ollama and assumes no for other backends.
    vision: bool | None = None
    temperature: float = 0.2
    max_steps: int = 30  # Model calls per request.
    max_consecutive_errors: int = 3  # End the request after this many identical failed calls.
    state_summary: bool = True  # Start each request with the robot state.
    text_tool_fallback: bool = True  # Accept tool calls written as text.
    timeout_s: float = 300.0  # Seconds per model call.

    def __post_init__(self) -> None:
        if self.backend not in DEFAULT_MODELS:
            raise ValueError(f"unknown backend {self.backend!r}; use ollama, openai or anthropic")
        self.model = self.model or DEFAULT_MODELS[self.backend]
        self.base_url = self.base_url or DEFAULT_BASE_URLS[self.backend]

    @classmethod
    def from_env(cls, **overrides) -> AgentConfig:
        """Load settings from BV_AGENT environment variables. Overrides that are not None
        are applied last.
        """
        values: dict = {}
        for f in fields(cls):
            raw = os.environ.get(f"BV_AGENT_{f.name.upper()}")
            if raw is not None:
                values[f.name] = _parse(raw, f.type)
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**values)


def _parse(raw: str, type_name: str):
    low = raw.strip().lower()
    if "bool" in type_name and low in ("true", "false", "1", "0", "yes", "no"):
        return low in ("true", "1", "yes")
    if type_name == "int":
        return int(raw)
    if type_name == "float":
        return float(raw)
    return raw
