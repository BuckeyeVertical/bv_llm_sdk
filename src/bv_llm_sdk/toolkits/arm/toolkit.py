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

The page is read from behind the base. Up on the page is larger x and down is smaller x.
Left is larger y and right is smaller y. So "above" always means larger x, never larger y.

Text reads left to right for someone standing behind the base. Letters grow away from the
base and each new letter is placed toward negative y. Each letter takes as much width as its
height. write_text moves big text closer to the base by itself, so do not move the text cursor
to fit it. Write text with more than one line in one write_text call with newline characters,
so it is placed to fit every line. Each new request to write is new text: call
reset_text_cursor with no arguments first, unless asked to add to the text already written.

Unless asked for a size, write text as big as it can be. Call measure_text first and write at
its biggest_size_cm. For text longer than about 10 letters, also measure the whole text split
into two lines with a newline character, such as "BUCKEYE\\nVERTICAL", and write whichever
version gives bigger letters.

The pen can draw about 16 to 34 cm from the base and about 67 degrees to either side. That
gives y from about -28 to 28 cm near x 20. The default center is x 24 y 0.

Plan a drawing before you start, because ink cannot be erased. Draw each shape once. If a
request gives no size for a shape, keep it under 10 cm wide. When shapes must not touch, work out
how far each one spreads and leave at least 2 cm between them. A triangle spreads as wide as
its side length and sits a little off its center.

Draw a picture with connected parts as one draw_path. A house with walls from x 20 to x 26
and a roof peak at x 30 goes through (20, -4), (26, -4), (30, 0), (26, 4), (20, 4), (20, -4).
Add any other lines after that.

Use check_point when a drawing may be too large or too close to the base. A tool checks the
whole job before moving, so a refused job moved nothing. Make it smaller or move it and try
again. If you change what was asked, say so. Never ask to change calibration or limits.

get_status lists what is on the board, so use it to place a new drawing next to an old one.
When told the board was erased, call clear_board. When you finish drawing or writing, call
go_home.
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
