from __future__ import annotations

import os

from ...toolkit import Toolkit
from .arm import Arm, connect
from .link import ArmError
from .tools import ARM_TOOLS

PROMPT = """\
You control a four-servo robot arm that draws on paper with a marker.
Coordinates are in centimeters: x points away from the base (up the page), positive y
points left, and z is height above the table. Text runs toward decreasing y; new lines
move toward decreasing x. Use the text cursor tools for text layout.
The drawing area is roughly 16 to 34 cm from the base and 67 degrees to either side.
These are approximate limits: check_point and tool validation decide actual reach.
Use the default drawing center, x 24 y 0, unless the user specifies another location.
Prefer the shape and text tools for those tasks. Check reach near the workspace edges
and use measure_text to check text size and fit. Use get_status to inspect the cursor
and recorded drawings when placing something relative to earlier work.
If a job is refused, read the error and adjust the plan; never bypass the limits.
Ask before changing an explicitly requested size or placement if it cannot fit.
Call go_home if a tool reports that the arm is not in a drawing pose. If servos are
limp, tell the user that a person must pose the arm at park and reconnect.
Only clear_board when the user says the board was erased; it forgets drawings and
does not physically erase them. Recorded drawings are not visual confirmation of ink.
"""


class ArmToolkit(Toolkit):
    name = "arm"
    registry = ARM_TOOLS
    prompt = PROMPT
    commands = {"/status": "get_status"}

    robot: Arm | None

    @classmethod
    def connect(cls) -> ArmToolkit:
        sim = os.environ.get("BV_ARM_SIM", "") not in ("", "0")
        arm = connect(os.environ.get("BV_ARM_PORT"), sim=sim)
        if not arm.is_holding():
            if not sim:
                try:
                    input("Pose the arm at its park pose by hand, then press Enter to attach. ")
                except (EOFError, KeyboardInterrupt) as exc:
                    arm.close()
                    raise ArmError("attach cancelled.") from exc
            arm.attach()
        return cls(arm)

    def close(self) -> None:
        if self.robot is not None:
            self.robot.close()

    def state(self) -> str | None:
        if self.robot is None:
            return None
        try:
            s = self.robot.status()
        except Exception:
            return None
        pen = "pen on paper" if s["pen_on_paper"] else "pen up"
        hold = "holding" if s["holding"] else "limp"
        return (f"Arm state: {hold}, {pen}, tip at x {s['tip_x_cm']:.1f} y {s['tip_y_cm']:.1f} "
                f"z {s['tip_z_cm']:.1f} cm")

    def stop(self) -> None:
        if self.robot is not None:
            self.robot.halt()
