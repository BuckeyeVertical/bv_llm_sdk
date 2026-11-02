"""Measure the board height near the base and far from it, so the pen touches everywhere.

Usage:
    uv run --extra arm scripts/arm_touch.py

The pen goes above a point near the base, then a point far out. At each one press Enter to
lower the pen 1 mm, u to raise it 1 mm, and t when it just touches the board. The results are
saved to calibration.json as paper_z and paper_drop. q or Ctrl+C stops without saving.
"""

import re
import sys

from bv_llm_sdk.toolkits.arm import ArmError, ArmToolkit
from bv_llm_sdk.toolkits.arm.settings import CALIBRATION_FILE

STEP = 0.1
PRESS = 0.1


def lower_to(arm, x: float, y: float, z: float) -> None:
    arm.execute(lambda: arm.planner._line(x, y, z, False, arm.settings.pen_slow_speed, "lower"))


def touch(arm, x: float, y: float, z: float) -> float:
    arm.execute(lambda: arm.planner.goto(x, y, z))
    while True:
        key = input(f"Pen at {z:.1f} cm. Enter = down 1 mm, u = up 1 mm, t = touching > ").strip().lower()
        if key == "t":
            return z
        if key == "q":
            raise KeyboardInterrupt
        z += STEP if key == "u" else -STEP
        lower_to(arm, x, y, z)


def save(values: dict[str, float]) -> None:
    text = CALIBRATION_FILE.read_text()
    for key, value in values.items():
        text = re.sub(rf'"{key}": [-\d.]+', f'"{key}": {value:.2f}', text)
    CALIBRATION_FILE.write_text(text)


def main() -> int:
    toolkit = ArmToolkit.connect()
    arm = toolkit.robot
    arm.execute(arm.planner.home, allow_recover=True)
    s = arm.settings
    start = s.paper_z + 1.0
    points = {"near the base": (s.drop_near_r, 0.0), "far out": (s.drop_far_r, 0.0)}
    s.paper_z, s.paper_drop = -2.0, 0.0
    heights = {}
    try:
        for name, (x, y) in points.items():
            print(f"Point {name}.")
            heights[name] = touch(arm, x, y, start)
            lower_to(arm, x, y, start)
    except KeyboardInterrupt:
        print("\nStopping without saving.")
        toolkit.stop()
    arm.execute(arm.planner.home, allow_recover=True)
    if len(heights) == len(points):
        far = heights["far out"]
        values = {"paper_z": far - PRESS, "paper_drop": far - heights["near the base"]}
        for name, z in heights.items():
            print(f"The board is at {z:.1f} cm {name}.")
        print("New settings: " + ", ".join(f"{k} {v:.2f}" for k, v in values.items()) + ".")
        if input("Save them? y/n ").strip().lower() == "y":
            save(values)
            print("Saved to calibration.json. Restart the chat to use them.")
        else:
            print("Not saved.")
    toolkit.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ArmError as exc:
        sys.exit(f"Arm problem: {exc}")
