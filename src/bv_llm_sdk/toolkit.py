"""What the agent needs to know about one robot.

The agent loop never touches a robot directly. It gets a ``Toolkit``: the
robot's tools, the part of the system prompt that describes its world, a
line describing its state now, and a way to stop it safely. The drone is one
toolkit (``toolkits/drone/``); a robot arm would be another:

    arm_tools = ToolRegistry()

    @arm_tools.tool
    def move_to(arm: Arm, x_m: float, y_m: float, z_m: float) -> dict:
        \"\"\"Move the gripper to a point in the arm's base frame. ...\"\"\"

    class ArmToolkit(Toolkit):
        name = "arm"
        registry = arm_tools
        prompt = "You control a six-axis robot arm on a table. ..."

        def stop(self) -> None:
            self.robot.halt()

    agent = Agent(ArmToolkit(arm), make_model(config), config)
"""

from __future__ import annotations

from typing import Any

from .registry import ToolRegistry


class Toolkit:
    #: Short name, shown in the chat ("drone> ...").
    name: str = "robot"
    #: The robot's tools.
    registry: ToolRegistry
    #: The robot's half of the system prompt: what it is, how its world
    #: works, and rules specific to its tools. The agent adds the rest.
    prompt: str = ""
    #: Named tool subsets, e.g. {"core": [...]}. "full" (every tool) always exists.
    toolsets: dict[str, list[str]] = {}
    #: Tools that hand the model an image. Not offered to text-only models.
    vision_tools: tuple[str, ...] = ()
    #: Chat commands that run a tool and print the result, e.g. {"/status": "get_status"}.
    commands: dict[str, str] = {}

    def __init__(self, robot: Any = None):
        #: Passed as the first argument to every tool. None for offline tests.
        self.robot = robot

    @classmethod
    def connect(cls) -> Toolkit:
        """Connect to the robot with default settings (used by bv-agent)."""
        return cls()

    def close(self) -> None:
        """Disconnect from the robot."""

    def __enter__(self) -> Toolkit:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def state(self) -> str | None:
        """One line describing the robot now, sent with each request. None to skip."""
        return None

    def stop(self) -> None:
        """Make the robot safe after the user interrupts (hover, halt...)."""
