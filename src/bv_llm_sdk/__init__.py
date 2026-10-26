"""An LLM agent that drives robots by calling Python tools.

    bv_llm_sdk/
        registry.py     ToolRegistry: Python functions -> LLM tool schemas
        toolkit.py      Toolkit: everything the agent knows about one robot
        agent/          the agent loop, model backends, chat CLI
        toolkits/drone/ the drone: PX4 link, tools, DroneToolkit, evals

Each robot lives in its own package under ``toolkits/``, so importing this
one does not pull in any robot's dependencies. For the drone:

    from bv_llm_sdk.toolkits.drone import connect, takeoff, land
"""

from .registry import ToolError, ToolRegistry
from .toolkit import Toolkit

__all__ = ["Toolkit", "ToolError", "ToolRegistry"]
