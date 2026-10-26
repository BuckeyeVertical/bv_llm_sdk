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

# TODO: write the drone's half of the system prompt: what the robot is, how
# its world works, and rules for its tools. agent/prompt.py adds the rules
# every robot shares.
#
# With the one-liner below the model does not know which way is "forward",
# lands when nobody asked, and makes up coordinates. Tell it:
#
#   1. What it is: the pilot of a real quadcopter drone.
#   2. How the world works: altitude is metres above launch; headings are
#      compass degrees (0 = north, 90 = east); "forward/left/right" are
#      relative to where the drone faces, and after a move it faces the way it
#      travelled; the camera points straight down; found objects and saved
#      places can be revisited with goto_saved_location.
#   3. Drone rules: call get_status when unsure; never invent coordinates;
#      stay hovering when done unless told to land or come home.
#
# Then measure it. Run the evals, read the failures, and add a rule for each
# mistake you see:
#   uv run --extra agent --extra vision scripts/agent_eval.py --tags quick
#
# Tip: when the model keeps doing math wrong (distances, battery), the fix is
# usually "use tool X and trust its answer", not "be careful". Read the
# docstrings of measure_route, plan_route and survey_area for ideas.
PROMPT = """\
You are a drone pilot.
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
