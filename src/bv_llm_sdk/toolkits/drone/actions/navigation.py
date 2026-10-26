"""Moving around once airborne.

Every tool here blocks until the drone has arrived and settled, so tools can
be chained: ``move(drone, north_m=10)`` then ``turn(drone, 90)`` runs the turn
only after the move is done. After each one the drone hovers in place.
"""

from __future__ import annotations

import math
import time

from .. import geo
from ..drone import MAV_CMD_DO_ORBIT, Drone, DroneError, mav
from ..tools import tool
from ..telemetry import get_position, heading_deg
from ._common import fly_to, ground_amsl_m, require_airborne, stop

# Refuse moves that are almost certainly a misunderstanding.
_MAX_MOVE_M = 1000.0
_MAX_ALTITUDE_M = 120.0
_MIN_ALTITUDE_M = 2.0


def _check_altitude(altitude_m: float) -> None:
    if not _MIN_ALTITUDE_M <= altitude_m <= _MAX_ALTITUDE_M:
        raise DroneError(
            f"altitude must be between {_MIN_ALTITUDE_M} and {_MAX_ALTITUDE_M} m "
            "above launch. Use land to come down."
        )


def _check_speed(speed_m_s: float | None) -> None:
    if speed_m_s is not None and not 0.5 <= speed_m_s <= 12.0:
        raise DroneError("speed_m_s must be between 0.5 and 12.")


def _move_by(
    drone: Drone, north_m: float, east_m: float, up_m: float, speed_m_s: float | None
) -> dict:
    _check_speed(speed_m_s)
    require_airborne(drone)
    if math.hypot(north_m, east_m) > _MAX_MOVE_M:
        raise DroneError(f"refusing to move more than {_MAX_MOVE_M:.0f} m in one step.")
    pos = drone.require("GLOBAL_POSITION_INT")
    target_alt_m = pos.relative_alt / 1000.0 + up_m
    _check_altitude(target_alt_m)
    lat, lon = geo.offset(pos.lat / 1e7, pos.lon / 1e7, north_m, east_m)
    fly_to(drone, lat, lon, ground_amsl_m(drone) + target_alt_m, speed_m_s=speed_m_s)
    return get_position(drone)


@tool
def move(
    drone: Drone,
    north_m: float = 0.0,
    east_m: float = 0.0,
    up_m: float = 0.0,
    speed_m_s: float | None = None,
) -> dict:
    """Fly a distance along compass directions from where the drone is now.
    Use negative values for the opposite direction (south, west, down).
    Example: north_m=20, east_m=-5 flies 20 m north and 5 m west.

    Args:
        north_m: Metres to fly north (negative = south).
        east_m: Metres to fly east (negative = west).
        up_m: Metres to climb (negative = descend).
        speed_m_s: Horizontal speed for this move in m/s (0.5 to 12). Leave
            out to use the current speed setting.
    """
    return _move_by(drone, north_m, east_m, up_m, speed_m_s)


@tool
def move_relative(
    drone: Drone,
    forward_m: float = 0.0,
    right_m: float = 0.0,
    up_m: float = 0.0,
    speed_m_s: float | None = None,
) -> dict:
    """Fly a distance relative to the way the drone is facing. Use negative
    values for the opposite direction (backward, left, down). The drone turns
    to face its direction of travel, so after moving right it faces right.

    Args:
        forward_m: Metres to fly forward (negative = backward).
        right_m: Metres to fly to the right (negative = left).
        up_m: Metres to climb (negative = descend).
        speed_m_s: Horizontal speed for this move in m/s (0.5 to 12). Leave
            out to use the current speed setting.
    """
    require_airborne(drone)
    north_m, east_m = geo.body_to_ned(forward_m, right_m, heading_deg(drone))
    return _move_by(drone, north_m, east_m, up_m, speed_m_s)


@tool
def goto_location(
    drone: Drone,
    latitude: float,
    longitude: float,
    altitude_m: float | None = None,
    speed_m_s: float | None = None,
) -> dict:
    """Fly to a GPS location and hover there.

    Args:
        latitude: Target latitude in decimal degrees.
        longitude: Target longitude in decimal degrees.
        altitude_m: Height above the launch point to arrive at, in metres.
            Leave out to keep the current altitude.
        speed_m_s: Horizontal speed for this flight in m/s (0.5 to 12).
            Leave out to use the current speed setting.
    """
    require_airborne(drone)
    _check_speed(speed_m_s)
    pos = drone.require("GLOBAL_POSITION_INT")
    distance = geo.distance(pos.lat / 1e7, pos.lon / 1e7, latitude, longitude)
    if distance > _MAX_MOVE_M:
        raise DroneError(
            f"that location is {distance:.0f} m away; the limit is {_MAX_MOVE_M:.0f} m."
        )
    if altitude_m is None:
        altitude_m = pos.relative_alt / 1000.0
    _check_altitude(altitude_m)
    fly_to(drone, latitude, longitude, ground_amsl_m(drone) + altitude_m, speed_m_s=speed_m_s)
    return get_position(drone)


@tool
def set_altitude(drone: Drone, altitude_m: float) -> dict:
    """Climb or descend to an altitude above the launch point, staying over
    the same spot.

    Args:
        altitude_m: Target height above the launch point, in metres.
    """
    require_airborne(drone)
    _check_altitude(altitude_m)
    pos = drone.require("GLOBAL_POSITION_INT")
    fly_to(drone, pos.lat / 1e7, pos.lon / 1e7, ground_amsl_m(drone) + altitude_m)
    return get_position(drone)


@tool
def set_heading(drone: Drone, heading_deg: float) -> dict:
    """Rotate in place to face a compass heading.

    Args:
        heading_deg: Compass heading in degrees: 0 = north, 90 = east,
            180 = south, 270 = west.
    """
    require_airborne(drone)
    pos = drone.require("GLOBAL_POSITION_INT")
    fly_to(
        drone, pos.lat / 1e7, pos.lon / 1e7, pos.alt / 1000.0,
        yaw_deg=heading_deg % 360.0,
    )
    return get_position(drone)


@tool
def turn(drone: Drone, degrees: float) -> dict:
    """Rotate in place by an angle relative to the current heading.

    Args:
        degrees: Angle to turn. Positive turns right (clockwise), negative
            turns left. For example 90 = quarter turn right, -180 = turn
            around, 360 = a full spin back to the same heading (up to 1080).
    """
    require_airborne(drone)
    if abs(degrees) > 1080:
        raise DroneError("degrees must be between -1080 and 1080.")
    # PX4 always yaws the short way to a target heading, so a 360 would do
    # nothing and a 270 would turn 90 the other way. Turn in steps small
    # enough that the short way is the requested way.
    steps = max(1, math.ceil(abs(degrees) / 90.0))
    start = heading_deg(drone)
    for i in range(1, steps + 1):
        result = set_heading(drone, start + degrees * i / steps)
    return {"turned_deg": degrees, **result}


@tool
def set_speed(drone: Drone, speed_m_s: float) -> dict:
    """Set the default horizontal speed for all later moves (a single move
    can still override it with its own speed_m_s).

    Args:
        speed_m_s: Ground speed in metres per second (0.5 to 12). The
            default is about 5.
    """
    if not 0.5 <= speed_m_s <= 12.0:
        raise DroneError("speed_m_s must be between 0.5 and 12.")
    drone.command_long(mav.MAV_CMD_DO_CHANGE_SPEED, 1, speed_m_s, -1)
    drone.cruise_speed_m_s = speed_m_s
    return {"message": f"speed set to {speed_m_s} m/s"}


@tool
def orbit(
    drone: Drone,
    radius_m: float = 10.0,
    speed_m_s: float = 3.0,
    laps: float = 1.0,
    clockwise: bool = True,
    center_latitude: float | None = None,
    center_longitude: float | None = None,
) -> dict:
    """Fly in a circle around a point while facing its centre, for example to
    inspect or film something. Returns after the requested number of laps and
    then hovers.

    Args:
        radius_m: Radius of the circle in metres (2 to 100).
        speed_m_s: Speed along the circle in metres per second.
        laps: How many times to go around.
        clockwise: Direction seen from above.
        center_latitude: Latitude of the centre. Leave out (with
            center_longitude) to circle around the drone's current position.
        center_longitude: Longitude of the centre.
    """
    require_airborne(drone)
    if not 2.0 <= radius_m <= 100.0:
        raise DroneError("radius_m must be between 2 and 100.")
    if not 0.5 <= speed_m_s <= 10.0:
        raise DroneError("speed_m_s must be between 0.5 and 10.")
    if laps <= 0 or laps > 10:
        raise DroneError("laps must be more than 0 and at most 10.")

    pos = drone.require("GLOBAL_POSITION_INT")
    if center_latitude is None or center_longitude is None:
        center_latitude, center_longitude = pos.lat / 1e7, pos.lon / 1e7
    elif geo.distance(pos.lat / 1e7, pos.lon / 1e7,
                      center_latitude, center_longitude) > _MAX_MOVE_M:
        raise DroneError(f"the orbit centre is more than {_MAX_MOVE_M:.0f} m away.")

    drone.command_int(
        MAV_CMD_DO_ORBIT,
        param1=radius_m if clockwise else -radius_m,  # sign sets direction
        param2=speed_m_s,
        param3=0,  # yaw behaviour: face the centre
        lat=center_latitude,
        lon=center_longitude,
        alt_amsl_m=pos.alt / 1000.0,
    )

    # Count laps by the angle swept around the centre, starting once the
    # drone is out on the circle.
    circumference = 2 * math.pi * radius_m
    start = time.time()
    deadline = start + 2.0 * (laps * circumference / speed_m_s) + 60.0
    swept = 0.0
    last_bearing = None
    while time.time() < deadline:
        pos = drone.wait_for("GLOBAL_POSITION_INT", timeout_s=3.0)
        if time.time() - start > 1.5 and (
            not drone.armed or drone.flight_mode in ("RTL", "LAND")
        ):
            raise DroneError(f"orbit interrupted: the drone switched to {drone.flight_mode}.")
        lat, lon = pos.lat / 1e7, pos.lon / 1e7
        if geo.distance(center_latitude, center_longitude, lat, lon) < 0.7 * radius_m:
            continue
        bearing = geo.bearing(center_latitude, center_longitude, lat, lon)
        if last_bearing is not None:
            swept += geo.wrap_180(bearing - last_bearing)
        last_bearing = bearing
        if abs(swept) >= laps * 360.0:
            stop(drone)
            return {"message": f"completed {laps:g} lap(s)", **get_position(drone)}
    stop(drone)
    raise DroneError(
        f"orbit timed out after {abs(swept) / 360.0:.1f} of {laps:g} laps; now holding."
    )


@tool
def wait(drone: Drone, seconds: float) -> dict:
    """Hover in place for a number of seconds before doing anything else.

    Args:
        seconds: How long to wait (at most 300).
    """
    if not 0 <= seconds <= 300:
        raise DroneError("seconds must be between 0 and 300.")
    drone.sleep(seconds)
    return {"message": f"waited {seconds:g} s", **get_position(drone)}
