"""The robots the agent can control by name.

To add a robot write a Toolkit subclass in a module here and list it below.
"""

from __future__ import annotations

import importlib

from ..toolkit import Toolkit

# Each toolkit is imported only when chosen. One robot does not need the dependencies of
# another.
TOOLKITS = {
    "drone": "bv_llm_sdk.toolkits.drone:DroneToolkit",
}


def toolkit_class(name: str) -> type[Toolkit]:
    if name not in TOOLKITS:
        raise ValueError(f"unknown toolkit {name!r}; known: {', '.join(TOOLKITS)}")
    module, cls = TOOLKITS[name].split(":")
    return getattr(importlib.import_module(module), cls)
