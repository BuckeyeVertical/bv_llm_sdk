""""Read the vehicle's remaining battery charge from PX4.

The action opens its own MAVLink connection and closes it after receiving a
fresh ``BATTERY_STATUS`` message. As with the command actions, the initial
heartbeat is sent explicitly: PX4 runs in Docker and needs that packet to
learn where to send telemetry.
"""

import time

from pymavlink import mavutil

# PX4 SITL's GCS MAVLink instance. Not 14580: that one runs "-m onboard".
PX4_ADDR = ("127.0.0.1", 18570)

# PX4 SITL's GCS instance sends telemetry to this port.
LOCAL_PORT = 14550

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


def get_battery(timeout_s: float = 10.0) -> int:
    """Return the vehicle's reported remaining battery charge as a percentage.

    Args:
        timeout_s: How long to wait for a ``BATTERY_STATUS`` message after
            connecting to PX4.

    Returns:
        The remaining battery charge from 0 to 100 percent.

    Raises:
        RuntimeError: PX4 does not come up, does not report battery telemetry,
            or reports an unknown remaining charge.
    """
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

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                battery = conn.recv_match(
                    type="BATTERY_STATUS", blocking=True, timeout=1.0
                )
            except ConnectionResetError:
                time.sleep(0.1)
                continue
            if battery is None:
                continue
            # MAVLink uses -1 to mean that the percentage is unavailable.
            if battery.battery_remaining < 0:
                raise RuntimeError("PX4 reported an unknown battery charge")
            return battery.battery_remaining

        raise RuntimeError(f"no BATTERY_STATUS from PX4 within {timeout_s}s")
    finally:
        conn.close()
