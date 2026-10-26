"""Dropping things: the payload gripper."""

from __future__ import annotations

from ..drone import Drone, DroneError, mav
from ..tools import tool
from ..telemetry import get_position


@tool
def release_payload(drone: Drone) -> dict:
    """Open the gripper to drop whatever the drone is carrying, at the current
    position. To drop onto something, first hover over it (for example with
    fly_to_object) and descend to a low altitude.
    """
    if not drone.armed:
        raise DroneError("the drone is on the ground; take off before dropping a payload.")
    # MAV_CMD_DO_GRIPPER: param1 = gripper instance, param2 = 0 release / 1 grab.
    drone.command_long(mav.MAV_CMD_DO_GRIPPER, 1, mav.GRIPPER_ACTION_RELEASE)
    return {"message": "payload released", **get_position(drone)}


@tool
def grab_payload(drone: Drone) -> dict:
    """Close the gripper to hold a payload."""
    drone.command_long(mav.MAV_CMD_DO_GRIPPER, 1, mav.GRIPPER_ACTION_GRAB)
    return {"message": "gripper closed"}
