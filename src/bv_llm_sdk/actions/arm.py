"""Arm the vehicle's motors.

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

import time

from pymavlink import mavutil

# PX4 SITL's GCS MAVLink instance. Not 14580: that one runs "-m onboard" and
# expects a single long-lived client such as MAVROS.
PX4_ADDR = ("127.0.0.1", 18570)

# Always this exact port, never an ephemeral one. See note 4 above.
LOCAL_PORT = 14550

# How long to wait for PX4 to start talking to us at all.
HANDSHAKE_TIMEOUT_S = 15.0

ARM = mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM


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


def arm(timeout_s: float = 10.0) -> None:
    """Arm the vehicle's motors.

    Does not take off: the propellers spin but the vehicle stays on the
    ground. Blocks until PX4 acknowledges, and raises if it refuses.

    Args:
        timeout_s: How long to wait for PX4 to acknowledge the command.

    Raises:
        RuntimeError: PX4 never came up, rejected the command, or never
            acknowledged it.
    """
    # --- establish ---
    conn = mavutil.mavlink_connection(
        f"udpin:0.0.0.0:{LOCAL_PORT}", source_system=255
    )

    try:
        deadline = time.time() + HANDSHAKE_TIMEOUT_S
        while time.time() < deadline:
            # Repeat rather than send once: the first heartbeat after a PX4
            # restart or a long idle gap often gets no reply.
            _heartbeat(conn)
            try:
                conn.recv_match(type="HEARTBEAT", blocking=True, timeout=1.0)
            except ConnectionResetError:
                # Pace the retry, or an absent PX4 becomes a busy loop.
                time.sleep(0.1)
            # Wait on target_system rather than on any heartbeat: pymavlink
            # latches it from the first *vehicle* heartbeat and ignores
            # heartbeats from other ground stations, so a running
            # QGroundControl cannot be mistaken for the drone.
            if conn.target_system != 0:
                break
        else:
            raise RuntimeError(
                f"no heartbeat from PX4 at {PX4_ADDR[0]}:{PX4_ADDR[1]} "
                f"within {HANDSHAKE_TIMEOUT_S}s"
            )

        # --- send ---
        conn.port.sendto(
            conn.mav.command_long_encode(
                conn.target_system,
                # pymavlink never learns the component id, leaving it 0
                # (broadcast). Address the autopilot explicitly.
                conn.target_component or mavutil.mavlink.MAV_COMP_ID_AUTOPILOT1,
                ARM,
                0,  # confirmation
                1,  # param1: 1 = arm, 0 = disarm
                0,
                0,
                0,
                0,
                0,
                0,
            ).pack(conn.mav),
            PX4_ADDR,
        )

        # --- confirm ---
        # The socket is new, so nothing queued on it can be a stale ack.
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                ack = conn.recv_match(
                    type="COMMAND_ACK", blocking=True, timeout=1.0
                )
            except ConnectionResetError:
                time.sleep(0.1)
                continue
            # An ack for some other command is not ours to interpret.
            if ack is None or ack.command != ARM:
                continue
            if ack.result == mavutil.mavlink.MAV_RESULT_ACCEPTED:
                return
            # IN_PROGRESS is not a verdict, so keep waiting for the real one.
            if ack.result != mavutil.mavlink.MAV_RESULT_IN_PROGRESS:
                raise RuntimeError(
                    "arm rejected: "
                    f"{mavutil.mavlink.enums['MAV_RESULT'][ack.result].name}"
                )

        raise RuntimeError(f"arm was never acknowledged in {timeout_s}s")
    finally:
        # --- sever ---
        conn.close()
