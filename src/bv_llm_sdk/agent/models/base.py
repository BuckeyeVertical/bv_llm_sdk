"""The interface every model backend implements."""

from __future__ import annotations

import base64
from typing import Literal, Protocol

from ...registry import _image_for_model
from ..config import AgentConfig
from ..messages import AssistantMessage, Message


class ModelError(RuntimeError):
    """The model could not be reached or refused the request."""


class ChatModel(Protocol):
    # Which schema format this backend uses.
    schema_format: Literal["openai", "anthropic"]
    # Whether to send images to the model.
    supports_vision: bool

    def complete(
        self, system: str, transcript: list[Message], tools: list[dict]
    ) -> AssistantMessage:
        """Run one model turn and return the reply text and tool calls."""
        ...


def image_b64(path: str) -> str:
    """Return a saved photo as base64 JPEG resized for the model."""
    return base64.b64encode(_image_for_model(path)).decode()


def make_model(config: AgentConfig) -> ChatModel:
    """Create the backend named in the config."""
    if config.backend == "ollama":
        from .ollama import OllamaModel

        return OllamaModel(config)
    if config.backend == "openai":
        from .openai_compat import OpenAICompatModel

        return OpenAICompatModel(config)
    if config.backend == "anthropic":
        from .anthropic import AnthropicModel

        return AnthropicModel(config)
    raise ModelError(f"unknown backend {config.backend!r}")
