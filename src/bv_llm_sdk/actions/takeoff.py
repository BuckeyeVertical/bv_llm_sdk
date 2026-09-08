"""Example action: a command whose effect takes time, so it watches telemetry.

PX4 acknowledges a takeoff the instant it accepts it, long before the vehicle
has climbed. Returning on the acknowledgement would tell an LLM the drone is
at altitude while it is still on the ground, so this waits for the vehicle to
actually get there.
"""

import math

from pymavlink import mavutil

from ..mavlink import link, send_command, wait_for

# Close enough to the target to call the climb finished.
_ALTITUDE_TOLERANCE_M = 0.5


def takeoff(altitude_m: float, timeout_s: float = 60.0) -> None:
    """Take off and climb to altitude_m above the launch point.

    The vehicle must already be armed. Blocks until it reaches the requested
    altitude, and raises if it does not get there in time.

    Args:
        altitude_m: Target height above the launch point, in metres.
        timeout_s: How long to allow for the whole climb.

    Raises:
        RuntimeError: PX4 rejected the takeoff, or the vehicle never reached
            the requested altitude.
    """
    with link() as conn:
        # MAV_CMD_NAV_TAKEOFF takes an altitude above mean sea level, but a
        # caller thinks in height above the ground. GLOBAL_POSITION_INT
        # carries both, so their difference is the launch point's elevation.
        position = wait_for(
            conn, "GLOBAL_POSITION_INT", lambda _: True, timeout_s=5.0
        )
        ground_amsl_m = (position.alt - position.relative_alt) / 1000.0

        send_command(
            conn,
            mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
            0.0,  # param1: minimum pitch, fixed-wing only
            0.0,
            0.0,
            math.nan,  # param4: yaw, NaN to keep the current heading
            math.nan,  # param5/6: latitude and longitude, NaN for current
            math.nan,
            ground_amsl_m + altitude_m,  # param7: altitude AMSL
        )

        wait_for(
            conn,
            "GLOBAL_POSITION_INT",
            lambda m: m.relative_alt / 1000.0 >= altitude_m - _ALTITUDE_TOLERANCE_M,
            timeout_s=timeout_s,
        )
