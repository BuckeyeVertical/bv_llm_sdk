"""The MAVLink plumbing every action shares.

Each action opens its own link, acts, and closes it. That is safe only
because every link binds the same fixed port: PX4 latches the remote address
it learns and keeps transmitting there, so reusing one port keeps the address
it latched valid for every action that follows.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager

from pymavlink import mavutil

# PX4 SITL's GCS MAVLink instance. Not 14580: that one runs "-m onboard" and
# expects a single long-lived client such as MAVROS.
PX4_ADDR = ("127.0.0.1", 18570)

# Always this exact port, never an ephemeral one. See the module docstring.
LOCAL_PORT = 14550


def _send(conn: mavutil.mavfile, message) -> None:
    """Send to PX4 explicitly.

    A udpin socket writes back to whatever address it last heard from, which
    is unset before any telemetry arrives, so the first heartbeat would go
    nowhere without an explicit target.
    """
    conn.port.sendto(message.pack(conn.mav), PX4_ADDR)


def _heartbeat(conn: mavutil.mavfile) -> None:
    """Announce ourselves as a ground station."""
    _send(
        conn,
        conn.mav.heartbeat_encode(
            mavutil.mavlink.MAV_TYPE_GCS,
            mavutil.mavlink.MAV_AUTOPILOT_INVALID,
            0,
            0,
            0,
        ),
    )


@contextmanager
def link(timeout_s: float = 15.0) -> Iterator[mavutil.mavfile]:
    """Open the link, hand it over once PX4 is talking, and always close it."""
    conn = mavutil.mavlink_connection(
        f"udpin:0.0.0.0:{LOCAL_PORT}", source_system=255
    )
    try:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            # PX4 only transmits to an address it has heard from, so speak
            # first. Repeat: the first heartbeat after a PX4 restart or a long
            # idle gap often gets no reply.
            _heartbeat(conn)
            conn.recv_match(type="HEARTBEAT", blocking=True, timeout=1.0)
            # Wait on target_system rather than on any heartbeat: pymavlink
            # latches it from the first *vehicle* heartbeat and ignores
            # heartbeats from other ground stations, so a running
            # QGroundControl cannot be mistaken for the drone.
            if conn.target_system != 0:
                yield conn
                return

        raise RuntimeError(
            f"no heartbeat from PX4 at {PX4_ADDR[0]}:{PX4_ADDR[1]} "
            f"within {timeout_s}s"
        )
    finally:
        conn.close()


def send_command(
    conn: mavutil.mavfile, command: int, *params: float, timeout_s: float = 10.0
) -> None:
    """Send a COMMAND_LONG and raise unless PX4 accepts it.

    Acceptance means PX4 took the order, not that the vehicle has carried it
    out. An action whose effect takes time must follow this with wait_for().
    """
    _send(
        conn,
        conn.mav.command_long_encode(
            conn.target_system,
            # pymavlink never learns the component id, leaving it 0
            # (broadcast). Address the autopilot explicitly.
            conn.target_component or mavutil.mavlink.MAV_COMP_ID_AUTOPILOT1,
            command,
            0,
            *(list(params) + [0.0] * (7 - len(params))),
        ),
    )

    # The socket is new, so nothing queued on it can be a stale ack.
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        ack = conn.recv_match(type="COMMAND_ACK", blocking=True, timeout=1.0)
        if ack is None or ack.command != command:
            continue
        if ack.result == mavutil.mavlink.MAV_RESULT_ACCEPTED:
            return
        # IN_PROGRESS is not a verdict, so keep waiting for the real one.
        if ack.result != mavutil.mavlink.MAV_RESULT_IN_PROGRESS:
            raise RuntimeError(
                f"command {command} rejected: "
                f"{mavutil.mavlink.enums['MAV_RESULT'][ack.result].name}"
            )

    raise RuntimeError(f"command {command} was never acknowledged in {timeout_s}s")


def wait_for(
    conn: mavutil.mavfile, message_type: str, predicate, timeout_s: float
):
    """Block until a message of message_type satisfies predicate, or raise."""
    deadline = time.time() + timeout_s
    last_heartbeat = 0.0

    while time.time() < deadline:
        # PX4 drops a ground station that goes quiet, and a long wait such as
        # a climb is exactly when that would happen.
        if time.time() - last_heartbeat > 1.0:
            _heartbeat(conn)
            last_heartbeat = time.time()

        message = conn.recv_match(type=message_type, blocking=True, timeout=1.0)
        if message is not None and predicate(message):
            return message

    raise RuntimeError(f"no {message_type} met the expected condition in {timeout_s}s")
