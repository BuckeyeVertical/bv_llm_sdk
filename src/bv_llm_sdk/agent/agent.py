"""The agent loop: a request in, tool calls out, until the job is done.

    agent = Agent(drone, make_model(config), config, on_event=print)
    agent.run("take off to 10 m and fly a square")
    agent.run("now land")          # same conversation, it remembers

Each run() appends to one conversation. The model is called, its tool calls
are run one at a time in order (each tool blocks until the drone is done),
their results go back to the model, and this repeats until the model answers
without calling a tool. Limits stop a confused model from looping forever.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from ..drone import Drone
from ..registry import call_tool, tool_schemas
from .args import coerce, extract_text_tool_calls
from .config import AgentConfig
from .events import AgentEvent, EventHandler, ignore
from .messages import AssistantMessage, Message, ToolCall, ToolResult, UserMessage
from .models.base import ChatModel
from .prompt import SYSTEM_PROMPT, state_summary
from .toolsets import toolset

Executor = Callable[[Drone, str, dict], dict]


class Agent:
    def __init__(
        self,
        drone: Drone | None,
        model: ChatModel,
        config: AgentConfig | None = None,
        on_event: EventHandler = ignore,
        executor: Executor = call_tool,
        system_prompt: str = SYSTEM_PROMPT,
    ):
        self.drone = drone
        self.model = model
        self.config = config or AgentConfig()
        self.on_event = on_event
        self.executor = executor
        self.system_prompt = system_prompt
        self.tool_names = toolset(self.config.toolset)
        if not model.supports_vision and self.config.toolset in ("standard", "full"):
            # `look` hands the model an image; a text-only model can only
            # pretend to have seen it.
            self.tool_names = [n for n in self.tool_names if n != "look"]
        self.schemas = tool_schemas(model.schema_format, self.tool_names)
        # The JSON schema of each tool's input, for argument coercion.
        self._input_schemas = {
            name: tool_schemas("anthropic", [name])[0]["input_schema"] for name in self.tool_names
        }
        self.transcript: list[Message] = []

    def reset(self) -> None:
        """Forget the conversation."""
        self.transcript = []

    def run(self, text: str) -> str:
        """Carry out one request and return the model's final reply."""
        self._emit("user", text=text)
        prefix = state_summary(self.drone) if self.config.state_summary and self.drone else None
        self.transcript.append(UserMessage(f"[{prefix}]\n{text}" if prefix else text))
        failures: dict[str, int] = {}
        try:
            for _ in range(self.config.max_steps):
                reply = self.model.complete(self.system_prompt, self.transcript, self.schemas)
                if not reply.tool_calls and self.config.text_tool_fallback:
                    calls = extract_text_tool_calls(reply.text, set(self.tool_names))
                    if calls:
                        reply = AssistantMessage(text="", tool_calls=calls)
                self.transcript.append(reply)
                if reply.text:
                    self._emit("assistant_text", text=reply.text)
                if not reply.tool_calls:
                    self._emit("done", text=reply.text)
                    return reply.text

                stuck = None
                for call in reply.tool_calls:
                    result = self._execute(call)
                    self.transcript.append(result)
                    if result.ok:
                        failures.clear()
                        continue
                    key = call.name + json.dumps(call.args, sort_keys=True, default=str)
                    failures[key] = failures.get(key, 0) + 1
                    if failures[key] >= self.config.max_consecutive_errors:
                        stuck = result
                if stuck is not None:
                    return self._finish(
                        f"I stopped: {stuck.name} kept failing with "
                        f"\"{stuck.content.get('error')}\"."
                    )
            return self._finish(
                f"I stopped after {self.config.max_steps} steps without finishing. "
                "The request may need to be broken up."
            )
        except KeyboardInterrupt:
            self._close_open_calls("interrupted by the user")
            self._hold()
            return self._finish("Interrupted. The drone is holding position.", kind="error")

    # --- internals ----------------------------------------------------------

    def _execute(self, call: ToolCall) -> ToolResult:
        self._emit("tool_call", id=call.id, name=call.name, args=call.args)
        start = time.time()
        if call.name not in self.tool_names:
            content: dict[str, Any] = {
                "ok": False,
                "error": f"there is no tool {call.name!r}. Available: {', '.join(self.tool_names)}",
            }
        elif "__invalid_arguments__" in call.args:
            content = {"ok": False, "error": call.args["__invalid_arguments__"]}
        else:
            args = coerce(call.args, self._input_schemas[call.name])
            content = self.executor(self.drone, call.name, args)
        image = content.pop("image_for_model", None)
        result = ToolResult(call.id, call.name, content, image)
        self._emit("tool_result", id=call.id, name=call.name, result=content,
                   seconds=round(time.time() - start, 1))
        return result

    def _close_open_calls(self, reason: str) -> None:
        """Give every unanswered tool call a result, so the transcript stays valid."""
        answered = {m.call_id for m in self.transcript if isinstance(m, ToolResult)}
        for msg in reversed(self.transcript):
            if isinstance(msg, AssistantMessage):
                for call in msg.tool_calls:
                    if call.id not in answered:
                        self.transcript.append(
                            ToolResult(call.id, call.name, {"ok": False, "error": reason})
                        )
                break

    def _hold(self) -> None:
        """Stop the drone where it is, if it is flying. Never raises."""
        if self.drone is None:
            return
        try:
            from ..actions._common import stop

            if self.drone.armed and self.drone.landed_state != "ON_GROUND":
                stop(self.drone)
        except Exception as exc:  # the user interrupted; do not make it worse
            self._emit("error", text=f"could not hold position: {exc}")

    def _finish(self, text: str, kind: str = "done") -> str:
        self.transcript.append(AssistantMessage(text=text))
        self._emit(kind, text=text)
        return text

    def _emit(self, kind: str, **data: Any) -> None:
        self.on_event(AgentEvent(kind, data))  # type: ignore[arg-type]
