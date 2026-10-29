"""Pick which toolkit tools to offer a model.

Fewer tools means a shorter prompt and fewer wrong picks. The full set always has every
tool.
"""

from __future__ import annotations

from ..toolkit import Toolkit


def toolset(name: str, toolkit: Toolkit) -> list[str]:
    """Return the tool names for a named toolset or the full set or a comma separated list.
    """
    tools = toolkit.registry.tools
    if name == "full":
        return list(tools)
    if name in toolkit.toolsets:
        return list(toolkit.toolsets[name])
    names = [n.strip() for n in name.split(",") if n.strip()]
    unknown = [n for n in names if n not in tools]
    if unknown or not names:
        known = ", ".join([*toolkit.toolsets, "full"])
        raise ValueError(
            f"unknown toolset or tools {unknown or name!r}. Use {known}, "
            "or a comma-separated list of tool names."
        )
    return names


def is_named(name: str, toolkit: Toolkit) -> bool:
    """Whether the name is a named toolset instead of a custom list."""
    return name == "full" or name in toolkit.toolsets
