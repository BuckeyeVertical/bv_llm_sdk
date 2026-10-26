"""Read-only tools: where the drone is and what state it is in.

None of these move the vehicle, so they are always safe to call. An agent
should call ``get_status`` before deciding what to do, and after anything
goes wrong.
"""

from __future__ import annotations

from . import geo
from .drone import Drone, mav
from .tools import tool


def _home(drone: Drone):
    return drone.latest("HOME_POSITION", max_age_s=float("inf"))


def _position(drone: Drone) -> dict:
    pos = drone.require("GLOBAL_POSITION_INT")
    lat, lon = pos.lat / 1e7, pos.lon / 1e7
    out = {
        "latitude": round(lat, 7),
        "longitude": round(lon, 7),
        "altitude_m": round(pos.relative_alt / 1000.0, 2),
        "altitude_amsl_m": round(pos.alt / 1000.0, 2),
    }
    home = _home(drone)
    if home is not None:
        north, east = geo.north_east(home.latitude / 1e7, home.longitude / 1e7, lat, lon)
        out["north_of_home_m"] = round(north, 1)
        out["east_of_home_m"] = round(east, 1)
        out["distance_from_home_m"] = round(geo.distance(
            lat, lon, home.latitude / 1e7, home.longitude / 1e7), 1)
    return out


def heading_deg(drone: Drone) -> float:
    """Current compass heading in degrees (0 = north, 90 = east)."""
    pos = drone.require("GLOBAL_POSITION_INT")
    if pos.hdg != 65535:
        return pos.hdg / 100.0
    return float(drone.require("VFR_HUD").heading)


def _battery(drone: Drone) -> dict:
    out: dict = {}
    status = drone.latest("SYS_STATUS")
    if status is not None:
        if status.battery_remaining >= 0:
            out["battery_percent"] = status.battery_remaining
        if status.voltage_battery not in (0, 65535):
            out["battery_voltage_v"] = round(status.voltage_battery / 1000.0, 2)
    return out


@tool
def get_status(drone: Drone) -> dict:
    """Get a full snapshot of the drone: armed state, flight mode, whether it
    is on the ground or flying, position, altitude, heading, speed, battery
    and GPS. Call this first to understand the situation, and whenever a
    command fails.
    """
    pos = drone.require("GLOBAL_POSITION_INT")
    out = {
        "armed": drone.armed,
        "flight_mode": drone.flight_mode,
        "landed_state": drone.landed_state,
        **_position(drone),
        "heading_deg": round(heading_deg(drone), 1),
        "ground_speed_m_s": round((pos.vx**2 + pos.vy**2) ** 0.5 / 100.0, 2),
        "climb_rate_m_s": round(-pos.vz / 100.0, 2),
        "cruise_speed_m_s": drone.cruise_speed_m_s,
        **_battery(drone),
    }
    gps = drone.latest("GPS_RAW_INT")
    if gps is not None:
        out["gps_fix"] = mav.enums["GPS_FIX_TYPE"][gps.fix_type].name.removeprefix("GPS_FIX_TYPE_")
        out["gps_satellites"] = gps.satellites_visible
    return out


@tool
def get_position(drone: Drone) -> dict:
    """Get the drone's current GPS position, altitude above the launch point,
    heading, and its offset in metres north/east of home.
    """
    return {**_position(drone), "heading_deg": round(heading_deg(drone), 1)}


def battery_verdict(drone: Drone, seconds: float) -> dict:
    """Whether a job of this length fits in the battery, decided here rather
    than left to the model's arithmetic."""
    out: dict = {"minutes_needed": round(seconds / 60.0, 1)}
    battery = _battery(drone)
    if "battery_percent" not in battery:
        out["fits_in_battery"] = None
        out["battery_verdict"] = "unknown: PX4 is not reporting battery state"
        return out
    usable_min = max(0.0, battery["battery_percent"] - drone.battery_reserve_percent) \
        / 100.0 * drone.full_battery_endurance_min
    fits = seconds / 60.0 <= usable_min
    out.update({
        "minutes_usable": round(usable_min, 1),
        "fits_in_battery": fits,
        "battery_verdict": (
            f"{'fits' if fits else 'does NOT fit'}: needs about {seconds / 60:.1f} min, "
            f"{usable_min:.1f} min usable before the {drone.battery_reserve_percent:g}% reserve"
        ),
    })
    return out


@tool
def get_battery(drone: Drone) -> dict:
    """Get the battery's remaining charge in percent and its voltage, plus an
    estimate of how many minutes of flying are left before the safety
    reserve. Compare it with estimated_time_s from measure_route or a
    plan_only survey to decide whether a mission is safe.
    """
    out = _battery(drone)
    if "battery_percent" not in out:
        out["note"] = "PX4 is not reporting battery state yet"
        return out
    usable = max(0.0, out["battery_percent"] - drone.battery_reserve_percent)
    out["reserve_percent"] = drone.battery_reserve_percent
    out["estimated_flight_minutes_left"] = round(
        usable / 100.0 * drone.full_battery_endurance_min, 1)
    out["estimate_basis"] = (
        f"about {drone.full_battery_endurance_min:g} min on a full battery, keeping "
        f"{drone.battery_reserve_percent:g}% in reserve; a rough estimate, not a measurement"
    )
    return out


@tool
def get_home(drone: Drone) -> dict:
    """Get the home (launch) position the drone returns to with
    return_to_launch.
    """
    home = _home(drone)
    if home is None:
        home = drone.wait_for("HOME_POSITION", timeout_s=3.0)
    return {
        "latitude": round(home.latitude / 1e7, 7),
        "longitude": round(home.longitude / 1e7, 7),
        "altitude_amsl_m": round(home.altitude / 1000.0, 2),
    }


@tool
def get_distance_to(drone: Drone, latitude: float, longitude: float) -> dict:
    """Get the horizontal distance and compass bearing from the drone to a
    GPS location, without moving.

    Args:
        latitude: Target latitude in decimal degrees.
        longitude: Target longitude in decimal degrees.
    """
    pos = drone.require("GLOBAL_POSITION_INT")
    lat, lon = pos.lat / 1e7, pos.lon / 1e7
    north, east = geo.north_east(lat, lon, latitude, longitude)
    return {
        "distance_m": round(geo.distance(lat, lon, latitude, longitude), 1),
        "bearing_deg": round(geo.bearing(lat, lon, latitude, longitude), 1),
        "north_m": round(north, 1),
        "east_m": round(east, 1),
    }


@tool
def get_messages(drone: Drone) -> dict:
    """Get the most recent text messages from the autopilot, such as warnings
    and the reasons a command was refused (for example "Arming denied").
    """
    return {"messages": drone.status_texts()[-10:]}


@tool
def preflight_check(drone: Drone) -> dict:
    """Check whether the drone is ready to fly: PX4's pre-arm checks, GPS,
    battery, sensor health, and home position. Call before takeoff, or when
    takeoff is refused, to find out why.
    """
    status = drone.require("SYS_STATUS")
    enabled, health = status.onboard_control_sensors_enabled, status.onboard_control_sensors_health
    unhealthy = [
        entry.name.removeprefix("MAV_SYS_STATUS_SENSOR_").removeprefix("MAV_SYS_STATUS_")
        for bit, entry in mav.enums["MAV_SYS_STATUS_SENSOR"].items()
        if bit and bit & enabled and not bit & health and bit != mav.MAV_SYS_STATUS_PREARM_CHECK
    ]
    prearm_ok = bool(health & mav.MAV_SYS_STATUS_PREARM_CHECK)
    gps = drone.latest("GPS_RAW_INT")
    # PX4's pre-arm bit already accounts for which sensors matter (the sim,
    # for one, has no RC receiver and does not need it), so a sensor listed
    # here only blocks flight when ready_to_arm is false. Say so in words: a
    # bare list of "unhealthy" sensors reads to an LLM like a blocker.
    if prearm_ok:
        summary = "Ready to arm and take off."
        if unhealthy:
            summary += f" (Not blocking: {', '.join(unhealthy)} reported unhealthy.)"
    else:
        summary = "NOT ready to arm. See unhealthy_sensors and recent_autopilot_messages."
    out = {
        "summary": summary,
        "ready_to_arm": prearm_ok,
        "unhealthy_sensors": unhealthy,
        "home_set": _home(drone) is not None,
        "armed": drone.armed,
        "landed_state": drone.landed_state,
        **_battery(drone),
    }
    if gps is not None:
        out["gps_fix"] = mav.enums["GPS_FIX_TYPE"][gps.fix_type].name.removeprefix("GPS_FIX_TYPE_")
        out["gps_satellites"] = gps.satellites_visible
    warnings = drone.status_texts()[-5:]
    if warnings:
        out["recent_autopilot_messages"] = warnings
    return out
