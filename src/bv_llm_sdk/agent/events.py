"""Events that report what the agent is doing.

The CLI prints them. Other front ends can listen the same way without changing the
agent.
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
    """Default handler that does nothing."""
