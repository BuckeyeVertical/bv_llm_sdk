"""Claude, through the Anthropic Messages API. A strong baseline to compare
self-hosted models against. Needs ANTHROPIC_API_KEY (or config.api_key).
"""

from __future__ import annotations

from ..args import parse_arguments
from ..config import AgentConfig
from ..messages import AssistantMessage, Message, ToolCall, ToolResult, UserMessage
from .base import ModelError, image_b64


class AnthropicModel:
    schema_format = "anthropic"
    supports_vision = True

    def __init__(self, config: AgentConfig):
        try:
            import anthropic
        except ImportError as exc:
            raise ModelError("the Claude backend needs its extra: uv sync --extra claude") from exc
        self.config = config
        kwargs = {"timeout": config.timeout_s}
        if config.api_key:
            kwargs["api_key"] = config.api_key
        if config.base_url:
            kwargs["base_url"] = config.base_url
        self.client = anthropic.Anthropic(**kwargs)

    def to_wire(self, transcript: list[Message]) -> list[dict]:
        messages: list[dict] = []

        def add(role: str, blocks: list[dict]) -> None:
            # The API wants user/assistant to alternate; merge neighbours.
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"].extend(blocks)
            else:
                messages.append({"role": role, "content": blocks})

        for msg in transcript:
            if isinstance(msg, UserMessage):
                add("user", [{"type": "text", "text": msg.text}])
            elif isinstance(msg, AssistantMessage):
                blocks: list[dict] = []
                if msg.text:
                    blocks.append({"type": "text", "text": msg.text})
                blocks += [
                    {"type": "tool_use", "id": c.id, "name": c.name, "input": c.args}
                    for c in msg.tool_calls
                ]
                add("assistant", blocks or [{"type": "text", "text": "(no reply)"}])
            elif isinstance(msg, ToolResult):
                content: list[dict] = [{"type": "text", "text": msg.text(vision=True)}]
                if msg.image_path:
                    content.append({"type": "image", "source": {
                        "type": "base64", "media_type": "image/jpeg",
                        "data": image_b64(msg.image_path)}})
                add("user", [{"type": "tool_result", "tool_use_id": msg.call_id,
                              "content": content, "is_error": not msg.ok}])
        return messages

    def complete(self, system: str, transcript: list[Message], tools: list[dict]) -> AssistantMessage:
        import anthropic

        try:
            response = self.client.messages.create(
                model=self.config.model,
                max_tokens=4096,
                system=system,
                tools=tools,
                messages=self.to_wire(transcript),
                temperature=self.config.temperature,
            )
        except anthropic.APIConnectionError as exc:
            raise ModelError(f"cannot reach the Anthropic API: {exc}") from exc
        except anthropic.APIError as exc:
            raise ModelError(f"Anthropic API error: {exc}") from exc
        text = "".join(b.text for b in response.content if b.type == "text")
        calls = [
            ToolCall(b.id, b.name, parse_arguments(b.input))
            for b in response.content if b.type == "tool_use"
        ]
        return AssistantMessage(text=text, tool_calls=calls)
