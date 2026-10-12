"""Named subsets of the tools to offer a model.

Every tool schema costs prompt tokens (the full set is about 7k) and every
extra tool is another chance for a small model to pick the wrong one. So
small models get "core", mid-size "standard", and strong models "full".
"""

from __future__ import annotations

from ..registry import TOOLS

CORE = [
    "get_status", "takeoff", "land", "move", "move_relative", "turn", "set_heading",
    "set_altitude", "goto_location", "return_to_launch", "hold", "wait",
]

STANDARD = CORE + [
    "get_position", "get_battery", "preflight_check", "set_speed", "orbit", "fly_shape",
    "fly_waypoints", "draw", "measure_route", "plan_route", "face_location", "fly_home", "land_at",
    "save_location", "list_saved_locations", "goto_saved_location",
    "detect_objects", "search_for_object", "search_area", "fly_to_object", "take_photo",
]


def toolset(name: str) -> list[str]:
    """Tool names for a toolset: "core", "standard", "full", or a comma list."""
    if name == "core":
        return list(CORE)
    if name == "standard":
        return list(STANDARD)
    if name == "full":
        return list(TOOLS)
    names = [n.strip() for n in name.split(",") if n.strip()]
    unknown = [n for n in names if n not in TOOLS]
    if unknown or not names:
        raise ValueError(
            f"unknown toolset or tools {unknown or name!r}. Use core, standard, full, "
            "or a comma-separated list of tool names."
        )
    return names


TOOLSETS = ("core", "standard", "full")
