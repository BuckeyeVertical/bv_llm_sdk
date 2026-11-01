"""Find the base's center and how far it can turn, then save them on the Arduino.

Usage:
    uv run --extra arm scripts/arm_limits.py

Type a letter and press Enter: l turns left, r turns right, c makes the spot the new center,
m marks the spot as the limit on that side, p moves the park pose to center, and q goes to
center and finishes. Ctrl+C finishes without saving.
"""

import sys

from bv_llm_sdk.toolkits.arm import ArmError, ArmToolkit

STEP = 5.0
TRAVEL = 175.0
HELP = "l = left, r = right, c = set center here, m = mark limit here, p = park at center, q = finish"


def degrees(base, angle: float) -> float:
    return (angle - base.offset) / (base.dir * base.scale)


def describe(base, angle: float) -> str:
    turn = degrees(base, angle)
    side = "left" if turn > 0 else "right"
    return f"{abs(turn):.0f} degrees {side}" if round(turn) else "straight out"


def explore(link, base, angle: float) -> tuple[float, float]:
    low, high = base.min, base.max
    print(f"The base is {describe(base, angle)}.\n{HELP}")
    while True:
        key = input("> ").strip().lower()
        if key == "q":
            return low, high
        if key in ("l", "r"):
            step = STEP * base.dir * (1 if key == "l" else -1)
            angle = min(TRAVEL, max(0.0, angle + step))
            link.send(f"g {angle:.1f}", timeout=30.0)
            print(f"The base is {describe(base, angle)}.")
        elif key == "c":
            base.offset = angle
            print("This is the new center.")
        elif key == "p":
            angle = base.offset
            link.send(f"g {angle:.1f}", timeout=30.0)
            link.send("x")
            link.send("K")
            link.send("C 1")
            print("The arm now parks at center.")
        elif key == "m" and angle == base.offset:
            print("That is straight out. Turn to one side first.")
        elif key == "m":
            if angle > base.offset:
                high = angle
            else:
                low = angle
            print(f"Marked {describe(base, angle)} as the limit.")
        else:
            print(HELP)


def main() -> int:
    toolkit = ArmToolkit.connect()
    link, base = toolkit.robot.link, toolkit.robot.settings.servos[0]
    old_center = base.offset
    angle = link.status()[0][0]
    link.send("C 1")
    limits = None
    try:
        limits = explore(link, base, angle)
    except (KeyboardInterrupt, EOFError):
        print("\nStopping without saving.")
        link.resync()
    link.send(f"g {base.offset:.1f}", timeout=30.0)
    link.send("x")
    if limits and not limits[0] <= base.offset <= limits[1]:
        print(f"Center {base.offset:.1f} is outside the limits {limits[0]:.1f} to {limits[1]:.1f}. "
              "Nothing saved.")
    elif limits and (limits != (base.min, base.max) or base.offset != old_center):
        low, high = limits
        print(f"Limits: {describe(base, low)} and {describe(base, high)}.")
        print(f"Servo numbers: center {base.offset:.1f}, limits {low:.1f} to {high:.1f}.")
        if input("Save them? y/n ").strip().lower() == "y":
            link.send(f"L 1 {low:.1f} {high:.1f}")
            link.send(f"R 1 {base.offset:.1f} {base.dir}")
            link.send("W")
            print("Saved on the Arduino.")
        else:
            print("Not saved.")
    else:
        print("Nothing changed.")
    toolkit.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ArmError as exc:
        sys.exit(f"Arm problem: {exc}")
