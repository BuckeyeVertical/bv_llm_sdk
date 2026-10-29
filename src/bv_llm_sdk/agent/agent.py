"""The agent loop.

Each request is added to one conversation. The model is called and any tools it asks for
are run in order. The results go back to the model and this repeats until the model
replies without a tool call. Limits stop the loop if the model gets stuck.

Nothing here is robot specific. That all comes from the toolkit.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from ..toolkit import Toolkit
from .args import coerce, extract_text_tool_calls
from .config import AgentConfig
from .events import AgentEvent, EventHandler, ignore
from .messages import AssistantMessage, Message, ToolCall, ToolResult, UserMessage
from .models.base import ChatModel
from .prompt import system_prompt as build_system_prompt
from .toolsets import is_named, toolset

# Runs a tool and returns its result. Defaults to the toolkit registry.
Executor = Callable[[Any, str, dict], dict]


class Agent:
    def __init__(
        self,
        toolkit: Toolkit,
        model: ChatModel,
        config: AgentConfig | None = None,
        on_event: EventHandler = ignore,
        executor: Executor | None = None,
        system_prompt: str | None = None,
    ):
        self.toolkit = toolkit
        self.model = model
        self.config = config or AgentConfig()
        self.on_event = on_event
        self.executor = executor or toolkit.registry.call
        self.system_prompt = system_prompt or build_system_prompt(toolkit)
        self.tool_names = toolset(self.config.toolset, toolkit)
        if not model.supports_vision and is_named(self.config.toolset, toolkit):
            # Text only models cannot see images so they do not get image tools.
            self.tool_names = [n for n in self.tool_names if n not in toolkit.vision_tools]
        self.schemas = toolkit.registry.schemas(model.schema_format, self.tool_names)
        # Input schema for each tool. Used to fix argument types.
        self._input_schemas = {
            s["name"]: s["input_schema"]
            for s in toolkit.registry.schemas("anthropic", self.tool_names)
        }
        self.transcript: list[Message] = []

    def reset(self) -> None:
        """Forget the conversation."""
        self.transcript = []

    def run(self, text: str) -> str:
        """Run one request and return the final reply."""
        self._emit("user", text=text)
        try:
            reply = self._loop(text)
        except KeyboardInterrupt:
            self._close_open_calls("interrupted by the user")
            self._stop_robot()
            reply = self._finish(f"Interrupted. The {self.toolkit.name} has been stopped.")
            self._emit("error", text=reply)
            return reply
        self._emit("done", text=reply)
        return reply

    def _loop(self, text: str) -> str:
        """The agent loop. Returns the text to show the user.

        TODO: write this. It is the whole idea of an agent: let the model ask
        for tools, run them, show it the results, and repeat until it answers
        in plain text. Everything you need is a helper below or on self:

            self.transcript          the conversation, a list you append to
            self._user_message(text) the user's request as a message
            self._ask_model()        one model turn -> AssistantMessage
                                       (.text, .tool_calls)
            self._execute(call)      run one ToolCall -> ToolResult
            self._is_stuck(call, result, failures)
                                     True when the same call keeps failing
            self._finish(text)       record a message of your own and return it
            self.config.max_steps    how many model turns one request may take

        Steps:
          1. Append the user's message to the transcript.
          2. Up to max_steps times:
               a. Ask the model, and append its reply to the transcript.
               b. No tool calls in the reply? The job is done: return its text.
               c. Otherwise execute each tool call in order and append each
                  result. (The model sees the results on its next turn.)
               d. If _is_stuck(...) says a call keeps failing, give up with
                  _finish(f'I stopped: {call.name} kept failing with
                  "{result.content.get("error")}".')
          3. Out of steps? Give up with _finish(f"I stopped after
             {max_steps} steps without finishing. The request may need to
             be broken up.")

        Nothing here is drone-specific: the same loop drives any toolkit.
        Check it with: uv run pytest tests/test_agent.py
        """
        failures: dict[str, int] = {}  # Failure counts for this request.

        # TODO: your loop here.
        raise NotImplementedError("write the agent loop in Agent._loop (agent/agent.py)")

    # Helpers.

    def _user_message(self, text: str) -> UserMessage:
        """Build the user message with the robot state at the start."""
        prefix = self.toolkit.state() if self.config.state_summary else None
        return UserMessage(f"[{prefix}]\n{text}" if prefix else text)

    def _ask_model(self) -> AssistantMessage:
        """Send the conversation and tools to the model and return its reply."""
        reply = self.model.complete(self.system_prompt, self.transcript, self.schemas)
        if not reply.tool_calls and self.config.text_tool_fallback:
            # Some models write tool calls as text. Turn those into real calls.
            calls = extract_text_tool_calls(reply.text, set(self.tool_names))
            if calls:
                reply = AssistantMessage(text="", tool_calls=calls)
        if reply.text:
            self._emit("assistant_text", text=reply.text)
        return reply

    def _is_stuck(self, call: ToolCall, result: ToolResult, failures: dict[str, int]) -> bool:
        """Count repeated failing calls and return True once one has failed too many times.

        Use one failures dict for the whole request. Any success resets the count.
        """
        if result.ok:
            failures.clear()
            return False
        key = call.name + json.dumps(call.args, sort_keys=True, default=str)
        failures[key] = failures.get(key, 0) + 1
        return failures[key] >= self.config.max_consecutive_errors

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
            schema = self._input_schemas[call.name]
            # Some tool parsers send junk arguments to tools that take none. Drop keys
            # that cannot be parameters.
            args = {k: v for k, v in call.args.items() if k} if schema["properties"] else {}
            args = coerce(args, schema)
            content = self.executor(self.toolkit.robot, call.name, args)
        image = content.pop("image_for_model", None)
        result = ToolResult(call.id, call.name, content, image)
        self._emit("tool_result", id=call.id, name=call.name, result=content,
                   seconds=round(time.time() - start, 1))
        return result

    def _close_open_calls(self, reason: str) -> None:
        """Give every unanswered tool call a result so the transcript stays valid."""
        answered = {m.call_id for m in self.transcript if isinstance(m, ToolResult)}
        for msg in reversed(self.transcript):
            if isinstance(msg, AssistantMessage):
                for call in msg.tool_calls:
                    if call.id not in answered:
                        self.transcript.append(
                            ToolResult(call.id, call.name, {"ok": False, "error": reason})
                        )
                break

    def _stop_robot(self) -> None:
        """Make the robot safe. Never raises."""
        try:
            self.toolkit.stop()
        except Exception as exc:  # Report the error instead of raising.
            self._emit("error", text=f"could not stop the {self.toolkit.name}: {exc}")

    def _finish(self, text: str) -> str:
        """End the request with a message from the agent."""
        # Providers reject a transcript with unanswered tool calls.
        self._close_open_calls("not run: the request was stopped")
        self.transcript.append(AssistantMessage(text=text))
        return text

    def _emit(self, kind: str, **data: Any) -> None:
        self.on_event(AgentEvent(kind, data))  # type: ignore[arg-type]
