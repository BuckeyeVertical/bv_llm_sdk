"""The drone as an agent toolkit: its tools, its world, its state."""

from __future__ import annotations

from ...toolkit import Toolkit
from . import actions  # noqa: F401  (registers the drone tools)
from .drone import Drone, connect
from .tools import DRONE_TOOLS

# Every tool schema costs prompt tokens (the full set is about 7k) and every
# extra tool is another chance for a small model to pick the wrong one. So
# small models get "core", mid-size "standard", and strong models "full".
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

PROMPT = """\
You are the pilot of a real quadcopter drone.

How the world works:
- Altitudes are metres above the launch point. Headings are compass degrees: \
0 = north, 90 = east, 180 = south, 270 = west.
- "Forward", "left" and "right" are relative to the way the drone faces; after a \
move the drone faces the way it travelled.
- The camera points straight down. The top of a camera image is the direction \
the drone is facing.
- After a move the drone hovers in place.
- Objects found by the camera, and places you save, become named places you \
can return to with goto_saved_location.

Drone rules:
- If you are unsure of the drone's state, call get_status first.
- Never invent coordinates. Use ones the user gave, saved places, or positions \
returned by tools.
- Do not do distance or route arithmetic in your head: use measure_route and \
plan_route, passing max_total_m when there is a distance limit, and trust \
their within_limit. If a limit cannot be met, say so instead of flying.
- Areas described relative to a place ("north-east of home") start from that \
place: pass start_place to search_area / survey_area.
- For standard shapes (square, rectangle, triangle, circle, star, figure eight \
...) flown flat, use fly_shape. For pictures, letters, or anything drawn \
standing up "in the sky", use draw: give the outline as (x, y) points and \
plane="vertical" (y is up) unless the user wants it flat.
- To judge whether a mission is safe, get its estimated time and battery \
verdict without flying: survey_area or search_area with plan_only=true for \
anything covering an area (never measure an area's corners: covering it takes \
many lanes), measure_route for a list of points. Trust their fits_in_battery.
- When a request is done, stay hovering where you are. Only land or return \
home if the user asked for it.
"""


class DroneToolkit(Toolkit):
    name = "drone"
    registry = DRONE_TOOLS
    prompt = PROMPT
    toolsets = {"core": CORE, "standard": STANDARD}
    # `look` hands the model an image; a text-only model can only pretend
    # to have seen it.
    vision_tools = ("look",)
    commands = {"/status": "get_status", "/places": "list_saved_locations"}

    robot: Drone | None

    @classmethod
    def connect(cls) -> DroneToolkit:
        return cls(connect())

    def close(self) -> None:
        if self.robot is not None:
            self.robot.close()

    def state(self) -> str | None:
        """One line describing the drone now, or None if telemetry is missing."""
        from .telemetry import get_status

        drone = self.robot
        if drone is None:
            return None
        try:
            s = get_status(drone)
        except Exception:
            return None
        parts = [
            "armed" if s["armed"] else "disarmed",
            s["landed_state"].lower().replace("_", " "),
            f"mode {s['flight_mode']}",
            f"altitude {s['altitude_m']:.1f} m",
            f"heading {s['heading_deg']:.0f}°",
        ]
        if "north_of_home_m" in s:
            parts.append(f"{s['north_of_home_m']:.0f} m N / {s['east_of_home_m']:.0f} m E of home")
        if "battery_percent" in s:
            parts.append(f"battery {s['battery_percent']}%")
        if drone.places:
            parts.append("saved places: " + ", ".join(drone.places))
        return "Drone state: " + ", ".join(parts)

    def stop(self) -> None:
        """Hover where it is, if it is flying."""
        from .actions._common import stop

        drone = self.robot
        if drone is not None and drone.armed and drone.landed_state != "ON_GROUND":
            stop(drone)
