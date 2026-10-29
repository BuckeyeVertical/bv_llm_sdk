"""Take off and land."""

from __future__ import annotations

import math
import time

from ..drone import Drone, DroneError, mav
from ..tools import tool
from ..telemetry import get_position
from ._common import (
    fly_to,
    ground_amsl_m,
    require_airborne,
    stop,
    wait_until_landed,
)

# How close to the target height counts as done.
_ALTITUDE_TOLERANCE_M = 0.5


@tool
def takeoff(drone: Drone, altitude_m: float = 10.0) -> dict:
    """Take off and climb to an altitude above the launch point, then hover.
    Arms the motors first if needed. Returns once the drone has actually
    reached the altitude. If the drone is already flying, it just changes
    altitude instead.

    Args:
        altitude_m: Height above the launch point to climb to, in metres
            (2 to 120).
    """
    if not 2.0 <= altitude_m <= 120.0:
        raise DroneError("altitude_m must be between 2 and 120 metres.")

    pos = drone.require("GLOBAL_POSITION_INT")
    if drone.armed and pos.relative_alt > 1000 and drone.landed_state != "ON_GROUND":
        fly_to(drone, pos.lat / 1e7, pos.lon / 1e7, ground_amsl_m(drone) + altitude_m)
        return {"message": f"already flying; now at {altitude_m} m", **get_position(drone)}

    # Arm right before takeoff. PX4 disarms an idle drone after a few seconds.
    if not drone.armed:
        drone.command_long(mav.MAV_CMD_COMPONENT_ARM_DISARM, 1)
        drone.wait_armed(True)

    # Takeoff needs altitude above sea level. Add the ground elevation to the requested
    # height.
    drone.command_long(
        mav.MAV_CMD_NAV_TAKEOFF,
        0.0,  # Minimum pitch. Fixed wing only.
        0.0,
        0.0,
        math.nan,  # Yaw. NaN keeps the current heading.
        math.nan,  # Latitude and longitude. NaN takes off straight up.
        math.nan,
        ground_amsl_m(drone) + altitude_m,
    )

    # The ack only means the command was accepted. Wait for the climb.
    deadline = time.time() + 20.0 + altitude_m / 1.0
    while time.time() < deadline:
        pos = drone.wait_for("GLOBAL_POSITION_INT", timeout_s=3.0)
        if not drone.armed:
            raise DroneError("takeoff aborted: the drone disarmed.")
        # Wait for the climb to level off.
        if (
            pos.relative_alt / 1000.0 >= altitude_m - _ALTITUDE_TOLERANCE_M
            and abs(pos.vz) < 30  # Centimeters per second.
        ):
            return {"message": f"hovering at {altitude_m} m", **get_position(drone)}
    raise DroneError(
        f"did not reach {altitude_m} m in time "
        f"(at {pos.relative_alt / 1000.0:.1f} m)."
    )


@tool
def land(drone: Drone) -> dict:
    """Land straight down at the current position and wait until the drone is
    on the ground. The motors disarm automatically after touchdown.
    """
    if not drone.armed:
        return {"message": "already on the ground"}
    pos = drone.require("GLOBAL_POSITION_INT")
    drone.command_long(
        mav.MAV_CMD_NAV_LAND, 0, 0, 0, math.nan, math.nan, math.nan, math.nan
    )
    wait_until_landed(drone, timeout_s=30.0 + pos.relative_alt / 1000.0 / 0.5)
    return {"message": "landed", **get_position(drone)}


@tool
def return_to_launch(drone: Drone, wait_for_landing: bool = True) -> dict:
    """Fly back to the home (launch) position and land there. The drone first
    climbs to a safe return altitude, flies home, then descends.

    Args:
        wait_for_landing: If true, return only after the drone has landed.
            If false, return as soon as it starts heading home.
    """
    require_airborne(drone)
    drone.command_long(mav.MAV_CMD_NAV_RETURN_TO_LAUNCH)
    if wait_for_landing:
        wait_until_landed(drone, timeout_s=300.0)
        return {"message": "returned home and landed", **get_position(drone)}
    return {"message": "returning home", **get_position(drone)}


@tool
def hold(drone: Drone) -> dict:
    """Stop wherever the drone is and hover in place. Use this to pause,
    or to cancel an orbit or a return_to_launch that is still in the air.
    """
    require_airborne(drone)
    stop(drone)
    # Wait for the drone to stop.
    deadline = time.time() + 15.0
    while time.time() < deadline:
        pos = drone.wait_for("GLOBAL_POSITION_INT", timeout_s=3.0)
        if math.sqrt(pos.vx**2 + pos.vy**2 + pos.vz**2) / 100.0 < 0.5:
            break
    return {"message": "holding position", **get_position(drone)}
