"""Pieces shared by the flight tools. Not tools themselves."""

from __future__ import annotations

import math
import time
from typing import Callable

from .. import geo
from ..drone import Drone, DroneError, mav

# PX4 custom mode numbers (px4_custom_mode.h): main mode AUTO, sub mode LOITER.
PX4_MAIN_MODE_AUTO = 4
PX4_AUTO_LOITER = 3

# When a move counts as finished.
HORIZONTAL_TOLERANCE_M = 0.5
VERTICAL_TOLERANCE_M = 0.6
SETTLED_SPEED_M_S = 0.3
SETTLED_SAMPLES = 3

# PX4's default horizontal cruise speed, used to size timeouts.
DEFAULT_CRUISE_M_S = 5.0

# A pass-through waypoint (settle=False) counts as reached this close.
PASS_THROUGH_M = 2.0


def home(drone: Drone) -> tuple[float, float] | None:
    """Home latitude and longitude, if PX4 has reported it."""
    msg = drone.latest("HOME_POSITION", max_age_s=float("inf"))
    return (msg.latitude / 1e7, msg.longitude / 1e7) if msg else None


def check_geofence(drone: Drone, lat: float, lon: float) -> None:
    """Refuse targets further from home than drone.max_distance_from_home_m."""
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
    """Raise a clear error if the drone is not flying."""
    pos = drone.require("GLOBAL_POSITION_INT")
    if not drone.armed or (
        drone.landed_state == "ON_GROUND" and pos.relative_alt < 1000
    ):
        raise DroneError("the drone is on the ground. Call takeoff first.")


def ground_amsl_m(drone: Drone) -> float:
    """Elevation of the launch point above mean sea level."""
    pos = drone.require("GLOBAL_POSITION_INT")
    return (pos.alt - pos.relative_alt) / 1000.0


def set_hold_mode(drone: Drone) -> None:
    """Switch PX4 to Hold (AUTO.LOITER): stop and hover in place."""
    drone.command_long(
        mav.MAV_CMD_DO_SET_MODE,
        mav.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
        PX4_MAIN_MODE_AUTO,
        PX4_AUTO_LOITER,
    )


def reposition(
    drone: Drone, lat: float, lon: float, alt_amsl_m: float, speed_m_s: float | None = None
) -> None:
    """Send the drone towards a point without waiting for it to get there.

    For tools that steer continuously (track_object). Most tools want
    ``fly_to``, which waits for arrival.
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
    """Brake and hover where the drone is now.

    Switching to Hold is not enough on its own: a DO_REPOSITION is flown
    *inside* Hold mode, so if one is in progress PX4 accepts the mode change
    and keeps flying to the old target. Repositioning onto the current
    position cancels it.
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
    """Reposition to a point and block until the drone is there and settled.

    Uses MAV_CMD_DO_REPOSITION, which PX4 flies in Hold mode, so the drone
    hovers at the target afterwards. PX4 acknowledges it at once; this waits
    for the vehicle to arrive, so a caller never reports "done" early.

    ``speed_m_s`` overrides the drone's cruise speed for this move only.
    ``on_sample`` is called on every position update while flying; if it
    returns True the wait stops early (the drone keeps flying, so the caller
    should stop it with ``stop``). Returns True if the drone
    arrived, False if on_sample stopped the wait.

    With ``settle=False`` the point is a pass-through waypoint: it counts as
    reached within PASS_THROUGH_M, without waiting for the drone to stop.

    Raises:
        DroneError: PX4 refused, something else took control (RTL, land,
            failsafe), or the drone did not arrive in time.
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
        param1=speed_m_s or -1.0,  # ground speed, -1 = default
        param2=mav.MAV_DO_REPOSITION_FLAGS_CHANGE_MODE,
        # PX4 reads this as radians, despite the MAVLink spec saying degrees.
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
        # Give PX4 a heartbeat or so to report the new mode before judging it.
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
    """Block until the drone is on the ground (PX4 disarms soon after)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        drone.wait_for("HEARTBEAT", timeout_s=3.0)
        if not drone.armed:
            return
        if drone.landed_state == "ON_GROUND":
            # PX4 disarms a couple of seconds after touchdown; report the
            # settled state rather than racing it.
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
