"""Settings for the agent: which model, where, and how it may behave."""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from typing import Literal

Backend = Literal["ollama", "openai", "anthropic"]

DEFAULT_MODELS = {
    "ollama": "qwen3-coder:30b",  # in OSC's central model store; 7/7 on the eval
    "openai": "gpt-oss:20b",
    "anthropic": "claude-sonnet-5-5",
}
DEFAULT_BASE_URLS = {
    # bv_llm_host's tunnel (scripts/tunnel.sh ollama) lands here.
    "ollama": "http://localhost:11434",
    "openai": "http://localhost:8000/v1",
    "anthropic": None,
}


@dataclass
class AgentConfig:
    toolkit: str = "drone"  # which robot; see toolkits/__init__.py
    backend: Backend = "ollama"
    model: str | None = None  # None = DEFAULT_MODELS[backend]
    base_url: str | None = None  # None = DEFAULT_BASE_URLS[backend]
    api_key: str | None = None  # Claude/OpenAI; read from their usual env vars if unset
    toolset: str = "full"  # see toolsets.py; the drone has "core" for small models
    num_ctx: int = 32768  # Ollama context window; the full toolset alone is ~7k tokens
    think: bool | str | None = None  # Ollama: True/False, or "low"/"medium"/"high" (gpt-oss)
    vision: bool | None = None  # model can take images; None = ask Ollama / assume no elsewhere
    temperature: float = 0.2
    max_steps: int = 30  # model calls per request
    max_consecutive_errors: int = 3  # the same failing call this often ends the request
    state_summary: bool = True  # prefix each request with the drone's state
    text_tool_fallback: bool = True  # accept tool calls written as JSON text
    timeout_s: float = 300.0  # per model call

    def __post_init__(self) -> None:
        if self.backend not in DEFAULT_MODELS:
            raise ValueError(f"unknown backend {self.backend!r}; use ollama, openai or anthropic")
        self.model = self.model or DEFAULT_MODELS[self.backend]
        self.base_url = self.base_url or DEFAULT_BASE_URLS[self.backend]

    @classmethod
    def from_env(cls, **overrides) -> AgentConfig:
        """Settings from BV_AGENT_<FIELD> environment variables, then overrides.

        e.g. BV_AGENT_BACKEND=anthropic, BV_AGENT_MODEL=qwen3:32b,
        BV_AGENT_TOOLSET=core. Overrides that are None are ignored.
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
