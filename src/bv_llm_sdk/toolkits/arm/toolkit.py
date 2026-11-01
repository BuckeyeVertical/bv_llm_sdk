from __future__ import annotations

import os

from ...toolkit import Toolkit
from .arm import Arm, connect
from .link import ArmError
from .tools import ARM_TOOLS

# TODO: write the arm's half of the system prompt. agent/prompt.py adds the
# rules every robot shares. Cover:
#
#   - What it is: a four servo robot arm that draws on paper with a marker.
#   - How its world works: which way is up on the page and how text is laid out.
#   - Its reach: about 16 to 34 cm from the base and 67 degrees to either
#     side. The default center is x 24 y 0.
#   - Arm rules: checking reach, and what to do when a job is refused.
#
# Then run the evals and add a rule for each mistake you see:
#   uv run scripts/arm_eval.py
PROMPT = """\
You control a robot arm.
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
