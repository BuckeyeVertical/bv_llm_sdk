"""Hold the vehicle's position (Loiter / Hold mode).

Every action in this package is self-contained: it opens its own link, talks
to PX4 directly, and closes it. Nothing is hidden in a shared helper, so this
file is the whole story and a new action can start as a copy of it.

Four things about this link are not obvious. Read them before writing a new
action, because getting any of them wrong fails in a way that looks like
"PX4 is broken" rather than like a bug here.

1. We must speak first. PX4 runs in Docker and only 18570/udp is published
   inbound, so PX4 cannot reach the host until it learns our address from a
   packet we send. Hence the heartbeat loop below, not mavutil's
   wait_heartbeat(), which listens without ever sending.

2. Every send needs an explicit destination. A udpin socket writes back to
   whatever address it last heard from, and that is unset until telemetry
   arrives, so conn.mav.*_send() would drop the first packet on the floor.
   Use conn.port.sendto(msg.pack(conn.mav), PX4_ADDR).

3. Windows turns an ICMP port-unreachable from an earlier send into a
   ConnectionResetError on the *next* recv. On a connectionless socket that
   only means nobody was listening yet, so catch it and let our own deadline
   decide when to give up.

4. LOCAL_PORT must be 14550 in every action. PX4 SITL's GCS instance
   transmits to that port and latches the remote it learned, so an action
   that binds a different port hears nothing back. Do not change it here
   without changing it everywhere.
"""

import math
import time

from pymavlink import mavutil

# PX4 SITL's GCS MAVLink instance. Not 14580: that one runs "-m onboard".
PX4_ADDR = ("127.0.0.1", 18570)

# Always this exact port, never an ephemeral one. See note 4 above.
LOCAL_PORT = 14550

HANDSHAKE_TIMEOUT_S = 15.0

SET_MODE = mavutil.mavlink.MAV_CMD_DO_SET_MODE


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


def hold(duration_s: float = 10.0, timeout_s: float = 10.0) -> None:
    """Hold the vehicle in place for duration_s seconds or until interrupted.

    Puts the vehicle into Hold / Loiter mode so it hovers at its current
    location and altitude. Maintains a heartbeat stream for duration_s
    seconds, returning early if interrupted.

    Args:
        duration_s: How long to hold position in seconds (default 10.0s).
        timeout_s: How long to wait for PX4 to acknowledge the command.

    Raises:
        ValueError: duration_s is non-finite or negative, or timeout_s is
            non-finite or nonpositive.
        RuntimeError: PX4 never came up, rejected the mode change, or never
            acknowledged it.
    """
    if not math.isfinite(duration_s):
        raise ValueError("duration_s must be finite")
    if duration_s < 0:
        raise ValueError("duration_s must be nonnegative")
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("timeout_s must be finite and positive")

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

        # --- send mode change ---
        # PX4 loiter / hold mode values from px4_map:
        # base_mode = 29 (CUSTOM_MODE_ENABLED | AUTO | GUIDED | STABILIZE)
        # main_mode = 4 (PX4_CUSTOM_MAIN_MODE_AUTO)
        # sub_mode = 3 (PX4_CUSTOM_SUB_MODE_AUTO_LOITER)
        base_mode, custom_main_mode, custom_sub_mode = mavutil.px4_map["LOITER"]

        conn.port.sendto(
            conn.mav.command_long_encode(
                conn.target_system,
                conn.target_component or mavutil.mavlink.MAV_COMP_ID_AUTOPILOT1,
                SET_MODE,
                0,  # confirmation
                base_mode,
                custom_main_mode,
                custom_sub_mode,
                0,
                0,
                0,
                0,
            ).pack(conn.mav),
            PX4_ADDR,
        )

        # --- confirm ---
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                ack = conn.recv_match(
                    type="COMMAND_ACK", blocking=True, timeout=1.0
                )
            except ConnectionResetError:
                time.sleep(0.1)
                continue
            if ack is None or ack.command != SET_MODE:
                continue
            if ack.result == mavutil.mavlink.MAV_RESULT_ACCEPTED:
                break
            if ack.result != mavutil.mavlink.MAV_RESULT_IN_PROGRESS:
                raise RuntimeError(
                    "hold rejected: "
                    f"{mavutil.mavlink.enums['MAV_RESULT'][ack.result].name}"
                )
        else:
            raise RuntimeError(f"hold was never acknowledged in {timeout_s}s")

        # --- hold position for duration_s or until interrupted ---
        deadline = time.time() + duration_s
        last_heartbeat = 0.0
        while time.time() < deadline:
            if time.time() - last_heartbeat > 1.0:
                _heartbeat(conn)
                last_heartbeat = time.time()

            try:
                conn.recv_match(blocking=True, timeout=0.1)
            except ConnectionResetError:
                time.sleep(0.1)
            except KeyboardInterrupt:
                break
    finally:
        # --- sever ---
        conn.close()
