from __future__ import annotations

import os

from ...toolkit import Toolkit
from .arm import Arm, connect
from .link import ArmError
from .tools import ARM_TOOLS

PROMPT = """\
You control a four servo robot arm that draws on paper with a marker.

All positions are in centimeters, seen from above with the arm pointing at the paper.
x is straight out from the base. y is to the left of the arm and negative y is to the right.
z is the tip height above the table.

Text reads left to right for someone standing behind the base. Letters grow away from the
base and each new letter is placed toward negative y.

The pen can draw about 16 to 34 cm straight out from the base and about 43 degrees to either
side. That gives y from about -23 to 23 cm near x 20. The default center is x 24 y 0.

Use check_point when a drawing may be too large or too close to the base. A tool checks the
whole job before moving, so a refused job moved nothing. Make it smaller or move it and try
again. Never ask to change calibration or limits.
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
