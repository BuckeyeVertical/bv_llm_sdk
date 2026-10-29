"""Tools that let an LLM fly a PX4 drone.

Use DroneToolkit with the agent. To build your own loop give tool_schemas to the model
and pass each tool call to run_tool.
"""

from .actions import *  # noqa: F403  (registers the action tools)
from .actions import __all__ as _actions
from .drone import Drone, DroneError, connect
from .telemetry import (
    get_battery,
    get_distance_to,
    get_home,
    get_messages,
    get_position,
    get_status,
    preflight_check,
)
from .toolkit import CORE, STANDARD, DroneToolkit
from .tools import DRONE_TOOLS, TOOLS, call_tool, run_tool, run_tool_blocks, tool, tool_schemas

__all__ = [
    "connect",
    "Drone",
    "DroneError",
    "DroneToolkit",
    "CORE",
    "STANDARD",
    "DRONE_TOOLS",
    "TOOLS",
    "tool",
    "tool_schemas",
    "call_tool",
    "run_tool",
    "run_tool_blocks",
    "get_status",
    "get_position",
    "get_battery",
    "get_home",
    "get_distance_to",
    "get_messages",
    "preflight_check",
    *_actions,
]
