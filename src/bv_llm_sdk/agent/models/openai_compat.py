"""Backend for any OpenAI compatible chat server such as vLLM or OpenAI.

Use the Ollama backend for Ollama since it can set the context window.
"""

from __future__ import annotations

import json
import os

from ..args import new_call_id, parse_arguments
from ..config import AgentConfig
from ..messages import AssistantMessage, Message, ToolCall, ToolResult, UserMessage
from .base import ModelError, image_b64


class OpenAICompatModel:
    schema_format = "openai"

    def __init__(self, config: AgentConfig, supports_vision: bool | None = None):
        try:
            import openai
        except ImportError as exc:
            raise ModelError("the agent needs its extra: uv sync --extra agent") from exc
        self.config = config
        # These servers do not report whether a model takes images.
        self.supports_vision = bool(config.vision if supports_vision is None else supports_vision)
        self.client = openai.OpenAI(
            base_url=config.base_url,
            # Self hosted servers ignore the key but the SDK requires one.
            api_key=config.api_key or os.environ.get("OPENAI_API_KEY") or "unused",
            timeout=config.timeout_s,
        )

    def _thinking_options(self) -> dict:
        """Turn config.think into the options vLLM expects.

        True or False turns Qwen thinking on or off. A level sets the gpt-oss reasoning
        effort. None keeps the model default.
        """
        think = self.config.think
        if think is None:
            return {}
        if isinstance(think, str):
            return {"reasoning_effort": think}
        return {"extra_body": {"chat_template_kwargs": {"enable_thinking": bool(think)}}}

    def to_wire(self, system: str, transcript: list[Message]) -> list[dict]:
        messages: list[dict] = [{"role": "system", "content": system}]
        pending_images: list[dict] = []
        for msg in transcript:
            # Tool messages must come right after their assistant message so images go
            # in a user message after the results.
            if not isinstance(msg, ToolResult) and pending_images:
                messages.extend(pending_images)
                pending_images = []
            if isinstance(msg, UserMessage):
                messages.append({"role": "user", "content": msg.text})
            elif isinstance(msg, AssistantMessage):
                out: dict = {"role": "assistant", "content": msg.text or None}
                if msg.tool_calls:
                    out["tool_calls"] = [
                        {"id": c.id, "type": "function",
                         "function": {"name": c.name, "arguments": json.dumps(c.args)}}
                        for c in msg.tool_calls
                    ]
                messages.append(out)
            elif isinstance(msg, ToolResult):
                messages.append({"role": "tool", "tool_call_id": msg.call_id,
                                 "content": msg.text(self.supports_vision)})
                if msg.image_path and self.supports_vision:
                    pending_images.append({"role": "user", "content": [
                        {"type": "text", "text": f"(image returned by {msg.name})"},
                        {"type": "image_url",
                         "image_url": {"url": "data:image/jpeg;base64," + image_b64(msg.image_path)}},
                    ]})
        messages.extend(pending_images)
        return messages

    def complete(self, system: str, transcript: list[Message], tools: list[dict]) -> AssistantMessage:
        import openai

        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=self.to_wire(system, transcript),
                tools=tools,
                temperature=self.config.temperature,
                **self._thinking_options(),
            )
        except openai.APIConnectionError as exc:
            raise ModelError(f"cannot reach {self.config.base_url}: {exc}") from exc
        except openai.APIError as exc:
            raise ModelError(f"model server error: {exc}") from exc
        message = response.choices[0].message
        calls = []
        for raw in message.tool_calls or []:
            try:
                args = parse_arguments(raw.function.arguments)
            except ValueError as exc:
                args = {"__invalid_arguments__": str(exc)}
            # Some parsers leave a channel marker in the tool name. Keep only the real
            # name.
            name = raw.function.name.split("<|")[0].strip()
            calls.append(ToolCall(raw.id or new_call_id(), name, args))
        # Strip newlines that reasoning parsers leave at the start.
        return AssistantMessage(text=(message.content or "").strip(), tool_calls=calls)
