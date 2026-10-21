"""Get the current position of the vehicle

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


def get_position(timeout_s: float = 10.0) -> dict[str, float]:
    """Get the current position of the vehicle.


    Args:
        timeout_s: How long to wait for a position message.

    Raises:
        RuntimeError: PX4 never came up, or no position message.
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

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                message = conn.recv_match(
                    type="GLOBAL_POSITION_INT",
                    blocking=True,
                    timeout=1.0,
                )
            except ConnectionResetError:
                time.sleep(0.1)
                continue
            if message is None:
                continue

            return {
                "latitude": message.lat / 1e7,
                "longitude": message.lon / 1e7,
                "altitude_amsl_m": message.alt / 1000.0,
                "altitude_relative_m": message.relative_alt / 1000.0,
                "velocity_north_m_s": message.vx / 100.0,
                "velocity_east_m_s": message.vy / 100.0,
                "velocity_down_m_s": message.vz / 100.0,
                "heading_deg": message.hdg / 100.0,
            }

        raise RuntimeError(f"No GLOBAL_POSITION_INT was received in {timeout_s}s")
    finally:
        # --- sever ---
        conn.close()
