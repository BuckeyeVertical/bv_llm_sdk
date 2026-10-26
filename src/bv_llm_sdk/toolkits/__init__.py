"""The robots the agent can drive, by name (``bv-agent --toolkit drone``).

To add a robot, write a ``Toolkit`` subclass (see ``bv_llm_sdk/toolkit.py``)
in a module here and list it below.
"""

from __future__ import annotations

import importlib

from ..toolkit import Toolkit

# name -> "module:Class", imported only when chosen, so one robot's
# dependencies are not needed to drive another.
TOOLKITS = {
    "drone": "bv_llm_sdk.toolkits.drone:DroneToolkit",
}


def toolkit_class(name: str) -> type[Toolkit]:
    if name not in TOOLKITS:
        raise ValueError(f"unknown toolkit {name!r}; known: {', '.join(TOOLKITS)}")
    module, cls = TOOLKITS[name].split(":")
    return getattr(importlib.import_module(module), cls)
