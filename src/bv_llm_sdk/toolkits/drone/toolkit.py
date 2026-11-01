"""The drone toolkit for the agent."""

from __future__ import annotations

from ...toolkit import Toolkit
from . import actions  # noqa: F401  (registers the drone tools)
from .drone import Drone, connect
from .tools import DRONE_TOOLS

# Tool subsets by model size. Each tool costs prompt tokens and gives small models
# another way to pick wrong.
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

# TODO: write the drone's half of the system prompt. agent/prompt.py adds the
# rules every robot shares. Cover:
#
#   - What it is: the pilot of a real quadcopter drone.
#   - How its world works: altitude, headings, the camera, and saved places.
#   - Drone rules: when to check status, not inventing coordinates, and when to land.
#
# Then run the evals and add a rule for each mistake you see:
#   uv run scripts/agent_eval.py --tags quick
PROMPT = """\
You are a drone pilot.
"""


class DroneToolkit(Toolkit):
    name = "drone"
    registry = DRONE_TOOLS
    prompt = PROMPT
    toolsets = {"core": CORE, "standard": STANDARD}
    # Text only models cannot see images so they do not get look.
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
        """Return one line about the drone state or None if telemetry is missing."""
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
        """Hover in place if flying."""
        from .actions._common import stop

        drone = self.robot
        if drone is not None and drone.armed and drone.landed_state != "ON_GROUND":
            stop(drone)
