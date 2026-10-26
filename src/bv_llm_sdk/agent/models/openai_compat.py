"""Any OpenAI-compatible chat server: vLLM (e.g. on OSC), OpenAI, others.

For Ollama prefer the native backend (models/ollama.py), which can set the
context window.
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
        # OpenAI-compatible servers do not say whether a model takes images.
        self.supports_vision = bool(config.vision if supports_vision is None else supports_vision)
        self.client = openai.OpenAI(
            base_url=config.base_url,
            # Self-hosted servers ignore the key but the SDK insists on one.
            api_key=config.api_key or os.environ.get("OPENAI_API_KEY") or "unused",
            timeout=config.timeout_s,
        )

    def _thinking_options(self) -> dict:
        """Map config.think onto what vLLM understands.

        True/False switches Qwen-style thinking through the chat template;
        "low"/"medium"/"high" sets gpt-oss's reasoning effort. None leaves the
        model's default.
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
            # Tool messages must directly follow their assistant message, so
            # images go in a user message after the whole run of results.
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
            # vLLM's gpt-oss parser sometimes leaks a channel marker into the
            # name ("arm<|channel|>commentary"); keep the real name.
            name = raw.function.name.split("<|")[0].strip()
            calls.append(ToolCall(raw.id or new_call_id(), name, args))
        # Reasoning parsers can leave the newlines that followed the hidden
        # thinking at the start of the reply.
        return AssistantMessage(text=(message.content or "").strip(), tool_calls=calls)
