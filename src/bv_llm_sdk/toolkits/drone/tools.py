"""The drone's tool registry.

Decorate a drone tool with ``@tool`` from here; ``call_tool``,
``tool_schemas`` and ``run_tool`` act on every tool registered this way.
"""

from __future__ import annotations

from ...registry import ToolRegistry

DRONE_TOOLS = ToolRegistry()
TOOLS = DRONE_TOOLS.tools
tool = DRONE_TOOLS.tool
tool_schemas = DRONE_TOOLS.schemas
call_tool = DRONE_TOOLS.call
run_tool = DRONE_TOOLS.run
run_tool_blocks = DRONE_TOOLS.run_blocks
