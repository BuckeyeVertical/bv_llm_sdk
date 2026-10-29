"""Conversation messages that do not depend on the model provider.

Each backend converts them to its own format and converts its reply back.
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
    # Photo the tool wants the model to see.
    image_path: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.content.get("ok", True))

    def text(self, vision: bool) -> str:
        """Return the result as JSON for the model. Long results are cut and unseen images
        are noted.
        """
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
