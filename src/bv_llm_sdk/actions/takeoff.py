"""Take off and climb to an altitude above the launch point.

PX4 acknowledges a takeoff the instant it accepts it, long before the vehicle
has climbed. Returning on the acknowledgement would tell an LLM the drone is
at altitude while it is still on the ground, so this waits for the vehicle to
actually get there.

This action is self-contained, like every action here. The link rules in
short, with the full reasoning in arm.py:

1. Speak first, repeatedly, until conn.target_system != 0. PX4 is in Docker
   and cannot reach us until it learns our address.
2. Send with conn.port.sendto(msg.pack(conn.mav), PX4_ADDR). A udpin socket
   has no default peer.
3. Catch ConnectionResetError around every recv. On Windows it just means
   nobody was listening yet.
4. LOCAL_PORT is 14550 in every action. Changing it here breaks the link.

One rule beyond those four: a long wait needs a heartbeat kept up alongside
it, because PX4 drops a ground station that goes quiet and a climb is exactly
long enough for that to happen.
"""

import math
import time

from pymavlink import mavutil

# PX4 SITL's GCS MAVLink instance. Not 14580: that one runs "-m onboard".
PX4_ADDR = ("127.0.0.1", 18570)

# Always this exact port, never an ephemeral one.
LOCAL_PORT = 14550

HANDSHAKE_TIMEOUT_S = 15.0

TAKEOFF = mavutil.mavlink.MAV_CMD_NAV_TAKEOFF

# Close enough to the target to call the climb finished.
_ALTITUDE_TOLERANCE_M = 0.5


def _heartbeat(conn: mavutil.mavfile) -> None:
    """Announce ourselves to PX4 as a ground station."""
    conn.port.sendto(
        conn.mav.heartbeat_encode(
            mavutil.mavlink.MAV_TYPE_GCS,
            mavutil.mavlink.MAV_AUTOPILOT_INVALID,
            0,
            0,
            0,
        ).pack(conn.mav),
        PX4_ADDR,
    )


def takeoff(altitude_m: float, timeout_s: float = 60.0) -> None:
    """Take off and climb to altitude_m above the launch point.

    The vehicle must already be armed. Blocks until it reaches the requested
    altitude, and raises if it does not get there in time.

    Args:
        altitude_m: Target height above the launch point, in metres.
        timeout_s: How long to allow for the whole climb.

    Raises:
        RuntimeError: PX4 never came up, rejected the takeoff, or the vehicle
            never reached the requested altitude.
    """
    # --- establish ---
    conn = mavutil.mavlink_connection(
        f"udpin:0.0.0.0:{LOCAL_PORT}", source_system=255
    )

    try:
        deadline = time.time() + HANDSHAKE_TIMEOUT_S
        while time.time() < deadline:
            _heartbeat(conn)
            try:
                conn.recv_match(type="HEARTBEAT", blocking=True, timeout=1.0)
            except ConnectionResetError:
                time.sleep(0.1)
            if conn.target_system != 0:
                break
        else:
            raise RuntimeError(
                f"no heartbeat from PX4 at {PX4_ADDR[0]}:{PX4_ADDR[1]} "
                f"within {HANDSHAKE_TIMEOUT_S}s"
            )

        # MAV_CMD_NAV_TAKEOFF takes an altitude above mean sea level, but a
        # caller thinks in height above the ground. GLOBAL_POSITION_INT
        # carries both, so their difference is the launch point's elevation.
        position = None
        deadline = time.time() + 5.0
        while time.time() < deadline:
            try:
                position = conn.recv_match(
                    type="GLOBAL_POSITION_INT", blocking=True, timeout=1.0
                )
            except ConnectionResetError:
                time.sleep(0.1)
                continue
            if position is not None:
                break
        if position is None:
            raise RuntimeError("no GLOBAL_POSITION_INT from PX4 within 5s")
        ground_amsl_m = (position.alt - position.relative_alt) / 1000.0

        # --- send ---
        conn.port.sendto(
            conn.mav.command_long_encode(
                conn.target_system,
                # pymavlink leaves the component id 0 (broadcast). Address
                # the autopilot explicitly.
                conn.target_component or mavutil.mavlink.MAV_COMP_ID_AUTOPILOT1,
                TAKEOFF,
                0,  # confirmation
                0.0,  # param1: minimum pitch, fixed-wing only
                0.0,
                0.0,
                math.nan,  # param4: yaw, NaN to keep the current heading
                math.nan,  # param5/6: latitude and longitude, NaN for current
                math.nan,
                ground_amsl_m + altitude_m,  # param7: altitude AMSL
            ).pack(conn.mav),
            PX4_ADDR,
        )

        # --- confirm PX4 took the order ---
        deadline = time.time() + 10.0
        while time.time() < deadline:
            try:
                ack = conn.recv_match(
                    type="COMMAND_ACK", blocking=True, timeout=1.0
                )
            except ConnectionResetError:
                time.sleep(0.1)
                continue
            if ack is None or ack.command != TAKEOFF:
                continue
            if ack.result == mavutil.mavlink.MAV_RESULT_ACCEPTED:
                break
            if ack.result != mavutil.mavlink.MAV_RESULT_IN_PROGRESS:
                raise RuntimeError(
                    "takeoff rejected: "
                    f"{mavutil.mavlink.enums['MAV_RESULT'][ack.result].name}"
                )
        else:
            raise RuntimeError("takeoff was never acknowledged in 10s")

        # --- wait for the vehicle to actually get there ---
        deadline = time.time() + timeout_s
        last_heartbeat = 0.0
        while time.time() < deadline:
            # Keep the ground station alive across the climb, or PX4 drops us.
            if time.time() - last_heartbeat > 1.0:
                _heartbeat(conn)
                last_heartbeat = time.time()

            try:
                message = conn.recv_match(
                    type="GLOBAL_POSITION_INT", blocking=True, timeout=1.0
                )
            except ConnectionResetError:
                time.sleep(0.1)
                continue
            if (
                message is not None
                and message.relative_alt / 1000.0
                >= altitude_m - _ALTITUDE_TOLERANCE_M
            ):
                return

        raise RuntimeError(
            f"vehicle did not reach {altitude_m} m within {timeout_s}s"
        )
    finally:
        # --- sever ---
        conn.close()
