"""Arm and disarm the motors."""

from __future__ import annotations

from ..drone import Drone, DroneError, mav
from ..tools import tool


@tool
def arm(drone: Drone) -> dict:
    """Arm the motors so the propellers spin. The drone stays on the ground.
    You usually do not need this: takeoff arms automatically. PX4 disarms
    again by itself after about 10 seconds if the drone does not take off.
    """
    if drone.armed:
        return {"message": "already armed"}
    drone.command_long(mav.MAV_CMD_COMPONENT_ARM_DISARM, 1)
    drone.wait_armed(True)
    return {"message": "armed"}


@tool
def disarm(drone: Drone) -> dict:
    """Stop the motors. Only works on the ground; to come down from the air
    use land or return_to_launch instead.
    """
    if not drone.armed:
        return {"message": "already disarmed"}
    if drone.landed_state not in ("ON_GROUND", "UNDEFINED"):
        raise DroneError("refusing to disarm in the air. Call land first.")
    drone.command_long(mav.MAV_CMD_COMPONENT_ARM_DISARM, 0)
    drone.wait_armed(False)
    return {"message": "disarmed"}
