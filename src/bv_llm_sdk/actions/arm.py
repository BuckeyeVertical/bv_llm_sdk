"""Example action: a command whose acknowledgement is the whole outcome."""

from pymavlink import mavutil

from ..mavlink import link, send_command


def arm(timeout_s: float = 10.0) -> None:
    """Arm the vehicle's motors.

    Does not take off: the propellers spin but the vehicle stays on the
    ground. Blocks until PX4 acknowledges, and raises if it refuses.

    Args:
        timeout_s: How long to wait for PX4 to acknowledge.

    Raises:
        RuntimeError: PX4 rejected the command or never acknowledged it.
    """
    with link() as conn:
        send_command(
            conn,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            1,  # param1: 1 = arm, 0 = disarm
            timeout_s=timeout_s,
        )
