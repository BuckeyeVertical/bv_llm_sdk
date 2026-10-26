"""The one interface every LLM backend implements."""

from __future__ import annotations

import base64
from typing import Literal, Protocol

from ...registry import _image_for_model
from ..config import AgentConfig
from ..messages import AssistantMessage, Message


class ModelError(RuntimeError):
    """The model could not be reached or refused the request."""


class ChatModel(Protocol):
    #: Which tool_schemas() format this backend takes.
    schema_format: Literal["openai", "anthropic"]
    #: Whether images from `look` should be sent to the model.
    supports_vision: bool

    def complete(
        self, system: str, transcript: list[Message], tools: list[dict]
    ) -> AssistantMessage:
        """One model turn: the reply text and any tool calls it makes."""
        ...


def image_b64(path: str) -> str:
    """A saved photo as base64 JPEG, shrunk for the model."""
    return base64.b64encode(_image_for_model(path)).decode()


def make_model(config: AgentConfig) -> ChatModel:
    """The backend the config asks for."""
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
