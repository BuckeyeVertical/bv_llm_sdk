"""What the agent is doing, as a stream of events.

The CLI prints them; a voice front end (text-to-speech of assistant_text)
or a web UI can subscribe the same way without touching the agent loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

EventKind = Literal["user", "assistant_text", "tool_call", "tool_result", "error", "done"]


@dataclass
class AgentEvent:
    kind: EventKind
    data: dict[str, Any] = field(default_factory=dict)


EventHandler = Callable[[AgentEvent], None]


def ignore(_event: AgentEvent) -> None:
    """The default handler: do nothing."""
