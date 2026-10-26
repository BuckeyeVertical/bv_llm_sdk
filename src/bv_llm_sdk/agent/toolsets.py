"""Choosing which of a toolkit's tools to offer a model.

Every tool schema costs prompt tokens and every extra tool is another chance
for a small model to pick the wrong one, so toolkits name smaller subsets
(the drone has "core" and "standard"). "full" is always every tool.
"""

from __future__ import annotations

from ..toolkit import Toolkit


def toolset(name: str, toolkit: Toolkit) -> list[str]:
    """Tool names for a toolset: one the toolkit names, "full", or a comma list."""
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
    """Whether ``name`` is a named toolset rather than a hand-picked list."""
    return name == "full" or name in toolkit.toolsets
