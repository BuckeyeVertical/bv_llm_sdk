from __future__ import annotations

import math
from dataclasses import dataclass

from .settings import Settings


@dataclass
class Solution:
    joints: list[float]
    servo: list[float]
    pose: dict[str, tuple[float, float, float, float]]
    problems: list[str]

    @property
    def ok(self) -> bool:
        return not self.problems


def to_polar(x: float, y: float) -> tuple[float, float]:
    return math.hypot(x, y), math.degrees(math.atan2(y, x))


class Kinematics:
    def __init__(self, settings: Settings):
        self.settings = settings

    def fk(self, joints: list[float]) -> dict[str, tuple[float, float, float, float]]:
        s = self.settings
        yaw = math.radians(joints[0])
        a2 = math.radians(joints[1])
        a3 = a2 + math.radians(joints[2])
        a4 = a3 + math.radians(joints[3])
        rz = {"shoulder": (s.shoulder_offset, s.base_height)}
        rz["elbow"] = (rz["shoulder"][0] + s.l1 * math.cos(a2), rz["shoulder"][1] + s.l1 * math.sin(a2))
        rz["wrist"] = (rz["elbow"][0] + s.l2 * math.cos(a3), rz["elbow"][1] + s.l2 * math.sin(a3))
        rz["tip"] = (rz["wrist"][0] + s.l3 * math.cos(a4), rz["wrist"][1] + s.l3 * math.sin(a4))
        return {k: (r * math.cos(yaw), r * math.sin(yaw), z, r) for k, (r, z) in rz.items()}

    def joints_to_servo(self, joints: list[float]) -> list[float]:
        return [s.offset + s.dir * s.scale * j for s, j in zip(self.settings.servos, joints)]

    def servo_to_joints(self, servo: list[float]) -> list[float]:
        return [(a - s.offset) / (s.dir * s.scale) for s, a in zip(self.settings.servos, servo)]

    def limit_problems(self, servo: list[float]) -> list[str]:
        out = []
        for i, (s, a) in enumerate(zip(self.settings.servos, servo)):
            if i == 0 and not self.settings.base_enabled:
                continue
            if a < s.min - 1e-6 or a > s.max + 1e-6:
                out.append(f"{s.name} {a:.1f} outside {s.min:.1f}-{s.max:.1f}")
        return out

    def check(self, joints: list[float], problems: list[str] | None = None) -> Solution:
        servo = self.joints_to_servo(joints)
        sol = Solution(joints, servo, self.fk(joints), list(problems or []))
        sol.problems += self.limit_problems(servo)
        if sol.pose["elbow"][2] < 0 or sol.pose["wrist"][2] < 0:
            sol.problems.append("arm would hit the table")
        return sol

    def ik_polar(self, r: float, theta: float, z: float) -> Solution:
        s = self.settings
        problems = []
        if not s.base_enabled and abs(theta - s.base_yaw) > 0.5:
            problems.append("needs base rotation, but the base is disabled")
            theta = s.base_yaw
        if r < s.min_r:
            problems.append(f"closer than {s.min_r} cm to the base")
        if z < s.paper_z - 0.5:
            problems.append("below the paper")
        rw = r - s.shoulder_offset
        zw = z + s.l3 - s.base_height
        cos3 = (rw * rw + zw * zw - s.l1 * s.l1 - s.l2 * s.l2) / (2 * s.l1 * s.l2)
        if not -1.0 <= cos3 <= 1.0:
            problems.append("out of reach")
            cos3 = max(-1.0, min(1.0, cos3))
        sin3 = (-1 if s.elbow_up else 1) * math.sqrt(1 - cos3 * cos3)
        q3 = math.atan2(sin3, cos3)
        q2 = math.atan2(zw, rw) - math.atan2(s.l2 * sin3, s.l1 + s.l2 * cos3)
        q4 = -math.pi / 2 - (q2 + q3)
        return self.check([theta, math.degrees(q2), math.degrees(q3), math.degrees(q4)], problems)

    def ik_xyz(self, x: float, y: float, z: float) -> Solution:
        r, theta = to_polar(x, y)
        return self.ik_polar(r, theta, z)

    def in_paper_box(self, x: float, y: float) -> bool:
        box = self.settings.paper_box
        return box is None or (box[0] <= x <= box[1] and box[2] <= y <= box[3])

    def home(self) -> tuple[float, float, float]:
        return self.settings.home_x, self.settings.home_y, self.settings.home_z

    def drawable(self, x: float, y: float) -> bool:
        s = self.settings
        return (self.ik_xyz(x, y, s.paper_z).ok
                and self.ik_xyz(x, y, s.paper_z + s.pen_lift).ok
                and self.in_paper_box(x, y))

    def sanity_problems(self) -> list[str]:
        s = self.settings
        out = []
        for i, servo in enumerate(s.servos):
            if i == 0 and not s.base_enabled:
                continue
            if servo.min >= servo.max:
                out.append(f"{servo.name}: min {servo.min} is not below max {servo.max}")
            elif not servo.min - 1 <= servo.offset <= servo.max + 1:
                out.append(f"{servo.name}: reference {servo.offset} is outside its limits "
                           f"{servo.min}-{servo.max}, recalibrate")
            if servo.dir not in (1, -1):
                out.append(f"{servo.name}: dir must be 1 or -1")
            if not 0.5 <= servo.scale <= 1.5:
                out.append(f"{servo.name}: scale {servo.scale} is outside 0.5-1.5")
        home = self.ik_xyz(*self.home())
        if not home.ok:
            out.append("home is not reachable: " + "; ".join(home.problems))
        if not self.drawable(s.center_x, s.center_y):
            out.append(f"default center ({s.center_x}, {s.center_y}) is not drawable")
        return out
