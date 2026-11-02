"""Find how far a servo can move, then save its limits on the Arduino.

Usage:
    uv run --extra arm scripts/arm_limits.py       # 1 base
    uv run --extra arm scripts/arm_limits.py 2     # 2 shoulder, 3 elbow, 4 wrist
    uv run --extra arm scripts/arm_limits.py 2 --elbow-max    # hold the elbow at its max

Type a letter and press Enter. For the base: l turns left, r turns right, c makes the spot the
new center, p moves the park pose to center, and m marks the limit on that side of center.
For the others: + and - move the servo, c marks where it is straight (the shoulder level, the
elbow and wrist in line), and m marks the limit on that side of where it started. For all of
them, Enter on its own repeats the last move, q finishes and Ctrl+C finishes without saving.
"""

import sys

from bv_llm_sdk.toolkits.arm import ArmError, ArmToolkit

TRAVEL = [175.0, 300.0, 300.0, 300.0]
BASE_HELP = "l = left, r = right, c = set center here, m = mark limit here, p = park at center, q = finish"
SERVO_HELP = "+ = up one step, - = down one step, c = mark straight here, m = mark limit here, q = finish"


def degrees(servo, angle: float) -> float:
    return (angle - servo.offset) / (servo.dir * servo.scale)


def describe(servo, angle: float, n: int) -> str:
    if n > 1:
        return f"at {angle:.1f}"
    turn = degrees(servo, angle)
    side = "left" if turn > 0 else "right"
    return f"{abs(turn):.0f} degrees {side}" if round(turn) else "straight out"


def move(link, n: int, angle: float) -> None:
    link.send(f"C {n}")
    link.send(f"g {angle:.1f}", timeout=30.0)
    link.send("x")


def explore(link, servo, n: int, angle: float) -> tuple[float, float]:
    low, high = servo.min, servo.max
    middle = servo.offset if n == 1 else angle
    if n == 1:
        moves, step, help_text = {"l": servo.dir, "r": -servo.dir}, 5.0, BASE_HELP
    else:
        moves, step, help_text = {"+": 1, "-": -1}, 2.0, SERVO_HELP
    print(f"The {servo.name} is {describe(servo, angle, n)}. Its limits are {low:.1f} to {high:.1f}.")
    print(help_text)
    last = None
    while True:
        key = input("> ").strip().lower() or last
        if key == "q":
            return low, high
        if key in moves:
            last = key
            angle = min(TRAVEL[n - 1], max(0.0, angle + step * moves[key]))
            link.send(f"g {angle:.1f}", timeout=30.0)
            print(f"The {servo.name} is {describe(servo, angle, n)}.")
        elif key == "c" and n == 1:
            servo.offset = middle = angle
            print("This is the new center.")
        elif key == "c":
            servo.offset = angle
            low, high = min(low, angle), max(high, angle)
            print(f"Marked {angle:.1f} as straight. The limits now include it.")
        elif key == "p" and n == 1:
            angle = servo.offset
            link.send(f"g {angle:.1f}", timeout=30.0)
            link.send("x")
            link.send("K")
            link.send("C 1")
            print("The arm now parks at center.")
        elif key == "m" and angle == middle:
            print("That is where it started. Move to one side first.")
        elif key == "m":
            if angle > middle:
                high = angle
            else:
                low = angle
            print(f"Marked {describe(servo, angle, n)} as the limit.")
        else:
            print(help_text)


def main(args: list[str]) -> int:
    elbow_max = "--elbow-max" in args
    args = [a for a in args if a != "--elbow-max"]
    n = int(args[0]) if args else 1
    if n not in (1, 2, 3, 4):
        print("Servo must be 1 base, 2 shoulder, 3 elbow or 4 wrist.")
        return 2
    if elbow_max and n == 3:
        print("--elbow-max is for finding another servo's limits.")
        return 2
    toolkit = ArmToolkit.connect()
    link, servo = toolkit.robot.link, toolkit.robot.settings.servos[n - 1]
    old_center = servo.offset
    angles = link.status()[0]
    start = angles[n - 1]
    if elbow_max:
        elbow = toolkit.robot.settings.servos[2]
        print(f"Moving the elbow to its max, {elbow.max:.1f}.")
        move(link, 3, elbow.max)
    link.send(f"C {n}")
    limits = None
    try:
        limits = explore(link, servo, n, start)
    except (KeyboardInterrupt, EOFError):
        print("\nStopping without saving.")
        link.resync()
    link.send(f"g {servo.offset if n == 1 else start:.1f}", timeout=30.0)
    link.send("x")
    if elbow_max:
        print("Moving the elbow back.")
        move(link, 3, angles[2])
    if limits and not limits[0] <= servo.offset <= limits[1]:
        name = "Center" if n == 1 else "Its reference"
        print(f"{name} {servo.offset:.1f} is outside the limits {limits[0]:.1f} to {limits[1]:.1f}. "
              "Nothing saved.")
    elif limits and (limits != (servo.min, servo.max) or servo.offset != old_center):
        low, high = limits
        print(f"Limits: {describe(servo, low, n)} and {describe(servo, high, n)}.")
        name = "center" if n == 1 else "straight"
        print(f"Servo numbers: {name} {servo.offset:.1f}, limits {low:.1f} to {high:.1f}.")
        if input("Save them? y/n ").strip().lower() == "y":
            link.send(f"L {n} {low:.1f} {high:.1f}")
            link.send(f"R {n} {servo.offset:.1f} {servo.dir}")
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
        sys.exit(main(sys.argv[1:]))
    except ArmError as exc:
        sys.exit(f"Arm problem: {exc}")
