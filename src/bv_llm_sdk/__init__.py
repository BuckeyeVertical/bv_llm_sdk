"""Tools that let an LLM fly a PX4 drone.

    from bv_llm_sdk import connect, takeoff, move, land

    with connect() as drone:
        takeoff(drone, 10)
        move(drone, north_m=20)
        land(drone)

For an agent, hand ``tool_schemas()`` to the model and pass each tool call it
makes to ``run_tool(drone, name, args)``.
"""

from .actions import *  # noqa: F403  (registers the action tools)
from .actions import __all__ as _actions
from .drone import Drone, DroneError, connect
from .registry import TOOLS, call_tool, run_tool, run_tool_blocks, tool, tool_schemas
from .telemetry import (
    get_battery,
    get_distance_to,
    get_home,
    get_messages,
    get_position,
    get_status,
    preflight_check,
)

__all__ = [
    "connect",
    "Drone",
    "DroneError",
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
