"""What the model is told: the system prompt and a live state line."""

from __future__ import annotations

from ..drone import Drone

# TODO: write the system prompt. It is sent before every conversation and is
# the only thing the model knows about its job besides the tool descriptions.
#
# The one-liner below runs, but the model will fumble: it does not know which
# way is "forward", lands when nobody asked, makes up coordinates, and quits
# halfway through multi-step requests. Fix that by telling it:
#
#   1. Who it is and what it does (a drone pilot that acts by calling tools,
#      then reports back).
#   2. How the world works: altitude is metres above launch; headings are
#      compass degrees (0 = north, 90 = east); "forward/left/right" are relative
#      to where the drone faces; the camera points straight down; every tool
#      blocks until the drone is done; a failed tool returns
#      {"ok": false, "error": "..."}.
#   3. Rules: check get_status when unsure (each request starts with a
#      [Drone state] line); never invent coordinates; finish the whole request
#      in order; don't repeat a failing call, fix the cause or explain it;
#      stay hovering when done unless told to land; refuse dangerous or
#      impossible requests.
#   4. How to answer: short, and only what the tool results actually say.
#
# Then measure it. Run the evals, read the failures, and add a rule for each
# mistake you see:
#   uv run --extra agent --extra vision scripts/agent_eval.py --tags quick
#
# Tip: when the model keeps doing math wrong, the fix is usually "use tool X
# and trust its answer", not "be careful".
SYSTEM_PROMPT = """\
You are a drone pilot. Use the tools to do what the user asks.
"""


def state_summary(drone: Drone) -> str | None:
    """One line describing the drone now, or None if telemetry is missing."""
    from ..telemetry import get_status

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
