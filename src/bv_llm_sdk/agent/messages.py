"""The conversation, in a form no LLM provider owns.

The agent loop only ever builds these. Each backend in ``models/`` turns the
list into its own wire format and its reply back into an
``AssistantMessage``, so the loop, the tools and the tests never see
provider details.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Union

# Tool results longer than this are cut before they reach the model.
MAX_RESULT_CHARS = 4000


@dataclass
class UserMessage:
    text: str


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class AssistantMessage:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class ToolResult:
    call_id: str
    name: str
    content: dict[str, Any]
    # A photo the tool wants the model to see (from `look`).
    image_path: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.content.get("ok", True))

    def text(self, vision: bool) -> str:
        """The result as JSON for the model, trimmed, noting unseen images."""
        content = dict(self.content)
        if self.image_path and not vision:
            content["note"] = (
                f"image saved at {self.image_path}; this model cannot view images"
            )
        out = json.dumps(content)
        if len(out) > MAX_RESULT_CHARS:
            out = out[:MAX_RESULT_CHARS] + '... (truncated)"'
        return out


Message = Union[UserMessage, AssistantMessage, ToolResult]
