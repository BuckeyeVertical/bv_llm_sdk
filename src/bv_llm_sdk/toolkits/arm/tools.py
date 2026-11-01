from __future__ import annotations

from typing import Literal, TypedDict

from ...registry import ToolRegistry
from .arm import Arm
from .kinematics import to_polar
from .link import ArmError
from . import shapes

ARM_TOOLS = ToolRegistry()
tool = ARM_TOOLS.tool


class PaperPoint(TypedDict):
    """A point on the paper.

    Args:
        x_cm: Distance out from the base in cm.
        y_cm: Distance to the left of the arm in cm. Negative is to the right.
    """

    x_cm: float
    y_cm: float


def _center(arm: Arm, x_cm: float | None, y_cm: float | None) -> tuple[float, float]:
    x = arm.settings.center_x if x_cm is None else x_cm
    y = arm.settings.center_y if y_cm is None else y_cm
    return x, y


def _positive(value: float, name: str) -> None:
    if value <= 0:
        raise ArmError(f"{name} must be greater than 0.")


def _message(text: str, result: dict) -> dict:
    return {"message": text, **result}


@tool
def get_status(arm: Arm) -> dict:
    """Report the tip position, whether the servos are holding, whether the pen is on the
    paper, and the text cursor. Moves nothing.
    """
    return arm.status()


@tool
def check_point(arm: Arm, x_cm: float, y_cm: float, z_cm: float | None = None) -> dict:
    """Check whether the tip can reach a point. Moves nothing. Use this before drawing near
    the edge of the reachable area.

    Args:
        x_cm: Distance out from the base in cm.
        y_cm: Distance to the left of the arm in cm. Negative is to the right.
        z_cm: Tip height above the table in cm. Leave it out to check drawing on the paper.
    """
    if z_cm is None:
        return {"drawable": arm.kinematics.drawable(x_cm, y_cm)}
    solution = arm.kinematics.ik_xyz(x_cm, y_cm, z_cm)
    r, theta = to_polar(x_cm, y_cm)
    return {
        "reachable": solution.ok,
        "problems": solution.problems,
        "r_cm": round(r, 2),
        "theta_deg": round(theta, 1),
    }


@tool
def go_home(arm: Arm) -> dict:
    """Lift the pen and return to the home position. Also recovers the arm from any unusual
    pose, so call this when a tool says the arm is not in a drawing pose.
    """
    return _message("at home", arm.execute(arm.planner.home, allow_recover=True))


@tool
def move_to(arm: Arm, x_cm: float, y_cm: float, z_cm: float) -> dict:
    """Move the tip to a point with the pen up. Lifts, swings over, then goes to the height.
    Draws nothing.

    Args:
        x_cm: Distance out from the base in cm.
        y_cm: Distance to the left of the arm in cm. Negative is to the right.
        z_cm: Tip height above the table in cm.
    """
    return _message("moved", arm.execute(lambda: arm.planner.goto(x_cm, y_cm, z_cm)))


@tool
def rotate_base(arm: Arm, degrees: float) -> dict:
    """Turn the base from where it is now, keeping the tip's distance from the base. Lifts the
    pen first and leaves it up. Draws nothing. The base stops about 67 degrees to either side
    of straight out, so how far it can turn depends on where it is now. tip_theta_deg in
    get_status says where it is.

    Args:
        degrees: How far to turn. Positive turns left and negative turns right.
    """
    if abs(degrees) > 180:
        raise ArmError("degrees must be between -180 and 180.")
    return _message(f"rotated {degrees} degrees", arm.execute(lambda: arm.planner.rotate(degrees)))


@tool
def set_pen(arm: Arm, position: Literal["up", "down"]) -> dict:
    """Lift the pen off the paper or lower it onto the paper where it is now.

    Args:
        position: up or down.
    """
    build = arm.planner.lift if position == "up" else arm.planner.lower
    return _message(f"pen {position}", arm.execute(build))


@tool
def draw_line(arm: Arm, x1_cm: float, y1_cm: float, x2_cm: float, y2_cm: float) -> dict:
    """Draw a straight line between two points on the paper.

    Args:
        x1_cm: Start distance out from the base in cm.
        y1_cm: Start distance to the left in cm.
        x2_cm: End distance out from the base in cm.
        y2_cm: End distance to the left in cm.
    """
    points = [(x1_cm, y1_cm), (x2_cm, y2_cm)]
    return _message("drew a line", arm.execute(lambda: arm.planner.stroke(points)))


@tool
def draw_path(arm: Arm, points: list[PaperPoint]) -> dict:
    """Draw connected straight lines through points on the paper without lifting the pen.
    End on the first point to close the shape.

    Args:
        points: The points to draw through, at least 2.
    """
    if len(points) < 2:
        raise ArmError("draw_path needs at least 2 points.")
    path = [(p["x_cm"], p["y_cm"]) for p in points]
    return _message(f"drew {len(path)} points", arm.execute(lambda: arm.planner.stroke(path)))


@tool
def draw_square(arm: Arm, size_cm: float, center_x_cm: float | None = None,
                center_y_cm: float | None = None, angle_deg: float = 0.0) -> dict:
    """Draw a square on the paper.

    Args:
        size_cm: Side length in cm.
        center_x_cm: Center distance out from the base in cm. Leave out for the default center.
        center_y_cm: Center distance to the left in cm. Leave out for the default center.
        angle_deg: Rotation in degrees.
    """
    _positive(size_cm, "size_cm")
    cx, cy = _center(arm, center_x_cm, center_y_cm)
    points = shapes.square(size_cm, cx, cy, angle_deg)
    return _message("drew a square", arm.execute(lambda: arm.planner.stroke(points)))


@tool
def draw_triangle(arm: Arm, size_cm: float, center_x_cm: float | None = None,
                  center_y_cm: float | None = None, angle_deg: float = 0.0) -> dict:
    """Draw an equilateral triangle on the paper.

    Args:
        size_cm: Side length in cm.
        center_x_cm: Center distance out from the base in cm. Leave out for the default center.
        center_y_cm: Center distance to the left in cm. Leave out for the default center.
        angle_deg: Rotation in degrees.
    """
    _positive(size_cm, "size_cm")
    cx, cy = _center(arm, center_x_cm, center_y_cm)
    points = shapes.triangle(size_cm, cx, cy, angle_deg)
    return _message("drew a triangle", arm.execute(lambda: arm.planner.stroke(points)))


@tool
def draw_circle(arm: Arm, diameter_cm: float, center_x_cm: float | None = None,
                center_y_cm: float | None = None) -> dict:
    """Draw a circle on the paper.

    Args:
        diameter_cm: Diameter in cm.
        center_x_cm: Center distance out from the base in cm. Leave out for the default center.
        center_y_cm: Center distance to the left in cm. Leave out for the default center.
    """
    _positive(diameter_cm, "diameter_cm")
    cx, cy = _center(arm, center_x_cm, center_y_cm)
    points = shapes.circle(diameter_cm, cx, cy)
    return _message("drew a circle", arm.execute(lambda: arm.planner.stroke(points)))


@tool
def write_text(arm: Arm, text: str, size_cm: float | None = None) -> dict:
    """Write text at the text cursor. Letters are drawn as capitals. The cursor only moves
    if the text was drawn, so refused text can be retried after new_line.

    Args:
        text: A-Z, 0-9, ! ? and spaces. A newline character starts a new line.
        size_cm: Letter height in cm, 0.5 to 10. Leave it out for the default 3 cm. Stays set
            for later text.
    """
    if size_cm is not None:
        if not 0.5 <= size_cm <= 10:
            raise ArmError("size_cm must be between 0.5 and 10.")
        arm.settings.text_size = size_cm
    return _message(f"wrote {text!r}", arm.write(text))


@tool
def new_line(arm: Arm) -> dict:
    """Move the text cursor to the start of the next line. Moves nothing."""
    return _message("new line", arm.write("\n"))


@tool
def reset_text_cursor(arm: Arm, x_cm: float | None = None, y_cm: float | None = None) -> dict:
    """Put the text cursor back at the first line. Moves nothing.

    Args:
        x_cm: New first line distance out from the base in cm. Leave out to keep it.
        y_cm: New left edge of the first line in cm. Leave out to keep it.
    """
    if x_cm is not None:
        arm.settings.text_start_x = x_cm
    if y_cm is not None:
        arm.settings.text_start_y = y_cm
    arm.cursor = None
    return {"text_cursor": arm.text_cursor()}
