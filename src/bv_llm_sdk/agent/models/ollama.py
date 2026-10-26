"""Ollama's native chat API, e.g. the server bv_llm_host runs on OSC.

Native rather than Ollama's OpenAI-compatible endpoint because only the
native API can set the context window (options.num_ctx). The tool schemas
alone are several thousand tokens, and Ollama silently drops whatever does
not fit its (small) default window, which looks like a model that ignores
its tools.
"""

from __future__ import annotations

from ..args import new_call_id, parse_arguments
from ..config import AgentConfig
from ..messages import AssistantMessage, Message, ToolCall, ToolResult, UserMessage
from .base import ModelError, image_b64


class OllamaModel:
    schema_format = "openai"  # Ollama takes OpenAI-style function schemas

    def __init__(self, config: AgentConfig):
        try:
            import httpx
        except ImportError as exc:
            raise ModelError("the agent needs its extra: uv sync --extra agent") from exc
        self.config = config
        self.url = config.base_url.rstrip("/").removesuffix("/v1")
        self.client = httpx.Client(timeout=config.timeout_s)
        self._vision: bool | None = None

    @property
    def supports_vision(self) -> bool:
        if self._vision is None and self.config.vision is not None:
            self._vision = self.config.vision
        if self._vision is None:
            try:
                info = self.client.post(f"{self.url}/api/show", json={"model": self.config.model})
                self._vision = "vision" in info.json().get("capabilities", [])
            except Exception:
                self._vision = False
        return self._vision

    def to_wire(self, system: str, transcript: list[Message]) -> list[dict]:
        messages: list[dict] = [{"role": "system", "content": system}]
        pending_images: list[dict] = []
        vision = self.supports_vision
        for msg in transcript:
            if not isinstance(msg, ToolResult) and pending_images:
                messages.extend(pending_images)
                pending_images = []
            if isinstance(msg, UserMessage):
                messages.append({"role": "user", "content": msg.text})
            elif isinstance(msg, AssistantMessage):
                out: dict = {"role": "assistant", "content": msg.text}
                if msg.tool_calls:
                    out["tool_calls"] = [
                        {"function": {"name": c.name, "arguments": c.args}} for c in msg.tool_calls
                    ]
                messages.append(out)
            elif isinstance(msg, ToolResult):
                messages.append({"role": "tool", "tool_name": msg.name, "content": msg.text(vision)})
                if msg.image_path and vision:
                    pending_images.append({
                        "role": "user",
                        "content": f"(image returned by {msg.name})",
                        "images": [image_b64(msg.image_path)],
                    })
        messages.extend(pending_images)
        return messages

    def complete(self, system: str, transcript: list[Message], tools: list[dict]) -> AssistantMessage:
        import httpx

        body: dict = {
            "model": self.config.model,
            "messages": self.to_wire(system, transcript),
            "tools": tools,
            "stream": False,
            "options": {"num_ctx": self.config.num_ctx, "temperature": self.config.temperature},
        }
        if self.config.think is not None:
            body["think"] = self.config.think
        try:
            response = self.client.post(f"{self.url}/api/chat", json=body)
        except httpx.ReadTimeout as exc:
            raise ModelError(
                f"Ollama took longer than {self.config.timeout_s:.0f}s to answer. The model "
                "is too slow on this GPU, or the server is overloaded."
            ) from exc
        except httpx.HTTPError as exc:
            raise ModelError(
                f"cannot reach Ollama at {self.url} ({exc}). Is the server running and "
                "the tunnel open? (bv_llm_host: scripts/tunnel.sh ollama)"
            ) from exc
        if response.status_code != 200:
            raise ModelError(self._explain(response))
        message = response.json().get("message", {})
        calls = []
        for raw in message.get("tool_calls") or []:
            fn = raw.get("function", {})
            try:
                args = parse_arguments(fn.get("arguments"))
            except ValueError as exc:
                # Keep the call so the agent can report the bad arguments back.
                args = {"__invalid_arguments__": str(exc)}
            calls.append(ToolCall(raw.get("id") or new_call_id(), fn.get("name", ""), args))
        return AssistantMessage(text=message.get("content") or "", tool_calls=calls)

    def _explain(self, response) -> str:
        try:
            error = response.json().get("error", response.text)
        except ValueError:
            error = response.text
        if "not found" in str(error):
            try:
                tags = self.client.get(f"{self.url}/api/tags").json().get("models", [])
                names = ", ".join(m["name"] for m in tags) or "none"
            except Exception:
                names = "unknown"
            return f"Ollama has no model {self.config.model!r}. Available: {names}."
        return f"Ollama error {response.status_code}: {error}"
