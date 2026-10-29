"""Everything the agent needs to know about one robot.

A toolkit holds the robot tools and its part of the system prompt. It also reports the
robot state and stops the robot safely. The drone is one toolkit and other robots can
add their own.
"""

from __future__ import annotations

from typing import Any

from .registry import ToolRegistry


class Toolkit:
    # Short name shown in the chat.
    name: str = "robot"
    # The robot tools.
    registry: ToolRegistry
    # The robot part of the system prompt. Says what the robot is and how its tools
    # work.
    prompt: str = ""
    # Named tool subsets. The full set always exists.
    toolsets: dict[str, list[str]] = {}
    # Tools that return an image. Not offered to text only models.
    vision_tools: tuple[str, ...] = ()
    # Chat commands that run a tool and print the result.
    commands: dict[str, str] = {}

    def __init__(self, robot: Any = None):
        # Passed as the first argument to every tool. None for offline tests.
        self.robot = robot

    @classmethod
    def connect(cls) -> Toolkit:
        """Connect to the robot with default settings."""
        return cls()

    def close(self) -> None:
        """Disconnect from the robot."""

    def __enter__(self) -> Toolkit:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def state(self) -> str | None:
        """Return one line about the robot state to send with each request. None skips it.
        """
        return None

    def stop(self) -> None:
        """Make the robot safe after the user interrupts."""
