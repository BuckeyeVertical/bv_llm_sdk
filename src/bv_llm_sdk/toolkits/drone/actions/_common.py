"""Helpers shared by the flight tools. These are not tools."""

from __future__ import annotations

import math
import time
from typing import Callable

from .. import geo
from ..drone import Drone, DroneError, mav

# PX4 custom mode numbers for AUTO LOITER.
PX4_MAIN_MODE_AUTO = 4
PX4_AUTO_LOITER = 3

# When a move counts as finished.
HORIZONTAL_TOLERANCE_M = 0.5
VERTICAL_TOLERANCE_M = 0.6
SETTLED_SPEED_M_S = 0.3
SETTLED_SAMPLES = 3

# PX4 default cruise speed. Used to size timeouts.
DEFAULT_CRUISE_M_S = 5.0

# Distance at which a pass through waypoint counts as reached.
PASS_THROUGH_M = 2.0


def home(drone: Drone) -> tuple[float, float] | None:
    """Return the home latitude and longitude if PX4 has reported it."""
    msg = drone.latest("HOME_POSITION", max_age_s=float("inf"))
    return (msg.latitude / 1e7, msg.longitude / 1e7) if msg else None


def check_geofence(drone: Drone, lat: float, lon: float) -> None:
    """Refuse targets farther from home than the drone limit."""
    h = home(drone)
    if h is None:
        return
    distance = geo.distance(h[0], h[1], lat, lon)
    if distance > drone.max_distance_from_home_m:
        raise DroneError(
            f"that point is {distance:.0f} m from home; the geofence allows "
            f"{drone.max_distance_from_home_m:.0f} m."
        )


def require_airborne(drone: Drone) -> None:
    """Raise an error if the drone is not flying."""
    pos = drone.require("GLOBAL_POSITION_INT")
    if not drone.armed or (
        drone.landed_state == "ON_GROUND" and pos.relative_alt < 1000
    ):
        raise DroneError("the drone is on the ground. Call takeoff first.")


def ground_amsl_m(drone: Drone) -> float:
    """Return the launch point elevation above mean sea level."""
    pos = drone.require("GLOBAL_POSITION_INT")
    return (pos.alt - pos.relative_alt) / 1000.0


def set_hold_mode(drone: Drone) -> None:
    """Switch PX4 to Hold mode so the drone stops and hovers."""
    drone.command_long(
        mav.MAV_CMD_DO_SET_MODE,
        mav.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
        PX4_MAIN_MODE_AUTO,
        PX4_AUTO_LOITER,
    )


def reposition(
    drone: Drone, lat: float, lon: float, alt_amsl_m: float, speed_m_s: float | None = None
) -> None:
    """Send the drone toward a point without waiting for it to arrive.

    Used by tools that steer continuously. Most tools should use fly_to instead.
    """
    check_geofence(drone, lat, lon)
    drone.command_int(
        mav.MAV_CMD_DO_REPOSITION,
        param1=speed_m_s or drone.cruise_speed_m_s or -1.0,
        param2=mav.MAV_DO_REPOSITION_FLAGS_CHANGE_MODE,
        param4=math.nan,
        lat=lat,
        lon=lon,
        alt_amsl_m=alt_amsl_m,
    )


def stop(drone: Drone) -> None:
    """Stop and hover where the drone is now.

    Hold mode alone does not cancel a reposition already in progress. Repositioning onto
    the current position does.
    """
    if drone.flight_mode != "LOITER":
        set_hold_mode(drone)
    pos = drone.require("GLOBAL_POSITION_INT")
    drone.command_int(
        mav.MAV_CMD_DO_REPOSITION,
        param1=-1.0,
        param2=mav.MAV_DO_REPOSITION_FLAGS_CHANGE_MODE,
        param4=math.nan,
        lat=pos.lat / 1e7,
        lon=pos.lon / 1e7,
        alt_amsl_m=pos.alt / 1000.0,
    )


def fly_to(
    drone: Drone,
    lat: float,
    lon: float,
    alt_amsl_m: float,
    yaw_deg: float | None = None,
    timeout_s: float | None = None,
    speed_m_s: float | None = None,
    on_sample: Callable[[], bool] | None = None,
    settle: bool = True,
) -> bool:
    """Fly to a point and wait until the drone arrives and settles.

    speed_m_s overrides the cruise speed for this move. on_sample runs on each position
    update and can return True to stop waiting early. With settle set to False the point
    is a pass through waypoint. Returns True if the drone arrived and False if on_sample
    stopped the wait. Raises DroneError if PX4 refuses or loses control or the drone
    does not arrive in time.
    """
    check_geofence(drone, lat, lon)
    pos = drone.require("GLOBAL_POSITION_INT")
    distance = geo.distance(pos.lat / 1e7, pos.lon / 1e7, lat, lon)
    climb = abs(alt_amsl_m - pos.alt / 1000.0)
    speed_m_s = speed_m_s or drone.cruise_speed_m_s
    if timeout_s is None:
        speed = speed_m_s or DEFAULT_CRUISE_M_S
        timeout_s = 20.0 + 1.5 * (distance / speed + climb / 1.5)

    drone.command_int(
        mav.MAV_CMD_DO_REPOSITION,
        param1=speed_m_s or -1.0,  # Ground speed. Negative one uses the default.
        param2=mav.MAV_DO_REPOSITION_FLAGS_CHANGE_MODE,
        # PX4 reads this in radians even though MAVLink says degrees.
        param4=math.radians(yaw_deg) if yaw_deg is not None else math.nan,
        lat=lat,
        lon=lon,
        alt_amsl_m=alt_amsl_m,
    )

    start = time.time()
    deadline = start + timeout_s
    settled = 0
    while time.time() < deadline:
        pos = drone.wait_for("GLOBAL_POSITION_INT", timeout_s=3.0)
        mode = drone.flight_mode
        # Give PX4 time to report the new mode before checking it.
        if time.time() - start > 1.5 and (mode in ("RTL", "LAND") or not drone.armed):
            raise DroneError(
                f"move interrupted: the drone switched to {mode}"
                f"{'' if drone.armed else ' and disarmed'}."
            )
        if on_sample is not None and on_sample():
            return False
        off = geo.distance(pos.lat / 1e7, pos.lon / 1e7, lat, lon)
        dz = abs(pos.alt / 1000.0 - alt_amsl_m)
        speed = math.sqrt(pos.vx**2 + pos.vy**2 + pos.vz**2) / 100.0
        yaw_ok = yaw_deg is None or abs(geo.wrap_180(pos.hdg / 100.0 - yaw_deg)) < 4.0
        if not settle and off <= PASS_THROUGH_M and dz <= 2 * VERTICAL_TOLERANCE_M:
            return True
        if (
            off <= HORIZONTAL_TOLERANCE_M
            and dz <= VERTICAL_TOLERANCE_M
            and speed <= SETTLED_SPEED_M_S
            and yaw_ok
        ):
            settled += 1
            if settled >= SETTLED_SAMPLES:
                return True
        else:
            settled = 0
    raise DroneError(
        f"did not reach the target within {timeout_s:.0f}s "
        f"(still {off:.1f} m away horizontally, {dz:.1f} m vertically)."
    )


def wait_until_landed(drone: Drone, timeout_s: float) -> None:
    """Wait until the drone is on the ground."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        drone.wait_for("HEARTBEAT", timeout_s=3.0)
        if not drone.armed:
            return
        if drone.landed_state == "ON_GROUND":
            # PX4 disarms shortly after touchdown. Wait for that before reporting.
            try:
                drone.wait_armed(False, timeout_s=5.0)
            except DroneError:
                pass
            return
    pos = drone.require("GLOBAL_POSITION_INT")
    raise DroneError(
        f"still not landed after {timeout_s:.0f}s "
        f"(altitude {pos.relative_alt / 1000.0:.1f} m)."
    )
