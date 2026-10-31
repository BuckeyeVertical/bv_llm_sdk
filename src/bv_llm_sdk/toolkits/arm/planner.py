from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from .kinematics import Kinematics, Solution, to_polar
from .settings import Settings

RECOVER_SPEED_DPS = 20.0
RECOVER_CLEARANCE = 1.0

Point = tuple[float, float]
Position = tuple[float, float, float]
StepKind = Literal["draw", "travel", "lift", "lower", "recover", "settle"]


@dataclass
class Step:
    x: float
    y: float
    z: float
    pen: bool
    ms: float
    sol: Solution
    kind: StepKind
    cmd: list[float] | None = None

    def servo_out(self) -> list[float]:
        return self.cmd if self.cmd is not None else self.sol.servo

    def command(self) -> str | None:
        if self.kind == "settle":
            return None
        a = self.servo_out()
        return f"M {a[0]:.1f} {a[1]:.1f} {a[2]:.1f} {a[3]:.1f} {max(1, int(self.ms))}"


class Planner:
    def __init__(self, kinematics: Kinematics):
        self.k = kinematics
        self.s = kinematics.settings
        self.tip: Position | None = None
        self.servo: list[float] | None = None
        self.recover_from: list[float] | None = None

    @property
    def paper_z(self) -> float:
        return self.s.paper_z

    @property
    def lift_z(self) -> float:
        return self.s.paper_z + self.s.pen_lift

    @property
    def slow_z(self) -> float:
        return self.paper_z + max(0.0, self.s.pen_slow_zone)

    def _line(self, x: float, y: float, z: float, pen: bool, speed: float, kind: StepKind) -> list[Step]:
        x0, y0, z0 = self.tip
        dist = math.dist((x0, y0, z0), (x, y, z))
        if dist < 0.02:
            self.tip = (x, y, z)
            return []
        n = max(1, math.ceil(dist / max(0.05, self.s.step)))
        ms = (dist / n) / speed * 1000.0
        steps = []
        for k in range(1, n + 1):
            t = k / n
            p = (x0 + (x - x0) * t, y0 + (y - y0) * t, z0 + (z - z0) * t)
            sol = self.k.ik_xyz(*p)
            if pen and not self.k.in_paper_box(p[0], p[1]):
                sol.problems.append("outside the paper box")
            steps.append(Step(*p, pen, ms, sol, kind))
        self.tip = (x, y, z)
        return steps

    def _arc(self, x: float, y: float, z: float, speed: float) -> list[Step]:
        x0, y0, z0 = self.tip
        r0, t0 = to_polar(x0, y0)
        r1, t1 = to_polar(x, y)
        length = math.sqrt((r1 - r0) ** 2 + (math.radians(t1 - t0) * (r0 + r1) / 2) ** 2 + (z - z0) ** 2)
        if length < 0.02:
            self.tip = (x, y, z)
            return []
        n = max(1, math.ceil(length / max(0.05, self.s.step)))
        ms = (length / n) / speed * 1000.0
        steps = []
        for k in range(1, n + 1):
            t = k / n
            r, th, zz = r0 + (r1 - r0) * t, t0 + (t1 - t0) * t, z0 + (z - z0) * t
            sol = self.k.ik_polar(r, th, zz)
            px, py = r * math.cos(math.radians(th)), r * math.sin(math.radians(th))
            steps.append(Step(px, py, zz, False, ms, sol, "travel"))
        self.tip = (x, y, z)
        return steps

    def _joint_path(self, start: list[float], goal: list[float], speed_dps: float, max_step: float,
                    min_z: float, why: str) -> list[Step]:
        biggest = max(abs(g - s) for g, s in zip(goal, start))
        n = max(1, math.ceil(biggest / max_step))
        ms = (biggest / n) / speed_dps * 1000.0
        steps = []
        for k in range(1, n + 1):
            t = k / n
            sol = self.k.check([s + (g - s) * t for s, g in zip(start, goal)])
            tx, ty, tz, _ = sol.pose["tip"]
            if tz < min_z - 0.01:
                sol.problems.append(why)
            steps.append(Step(tx, ty, tz, False, ms, sol, "lift"))
        return steps

    def _kick_lift(self) -> list[Step] | None:
        idx = int(self.s.lift_kick_servo) - 1
        deg = self.s.lift_kick_deg
        x, y, z = self.tip
        start = self.k.ik_xyz(x, y, z)
        if not start.ok:
            return None
        best = None
        for sign in (1, -1):
            joints = list(start.joints)
            joints[idx] += sign * deg
            tip_z = self.k.fk(joints)["tip"][2]
            if best is None or tip_z > best[1]:
                best = (joints, tip_z)
        kicked, kick_z = best
        if kick_z < z + 0.2:
            return None
        steps = self._joint_path(start.joints, kicked, self.s.lift_kick_speed, 0.5, z,
                                 "pen-up kick would push the pen down")
        top = max(self.lift_z, kick_z)
        goal = self.k.ik_xyz(x, y, top)
        if not goal.ok:
            steps.append(Step(x, y, top, False, 0, goal, "lift"))
            return steps
        steps += self._joint_path(kicked, goal.joints, RECOVER_SPEED_DPS, 1.0, self.paper_z + 0.3,
                                  "pen would drop back toward the paper")
        self.tip = (x, y, top)
        return steps

    def lift(self) -> list[Step]:
        x, y, z = self.tip
        if z >= self.lift_z - 0.02:
            return []
        if self.s.lift_kick_deg > 0 and z < self.slow_z + 0.02:
            kick = self._kick_lift()
            if kick is not None:
                return kick
        steps = []
        if z < self.slow_z - 0.02:
            steps += self._line(x, y, min(self.slow_z, self.lift_z), False, self.s.pen_slow_speed, "lift")
        steps += self._line(x, y, self.lift_z, False, self.s.travel_speed / 2, "lift")
        return steps

    def lower(self) -> list[Step]:
        x, y, z = self.tip
        steps = []
        if z > self.slow_z + 0.02:
            steps += self._line(x, y, self.slow_z, False, self.s.travel_speed / 2, "lower")
        steps += self._line(x, y, self.paper_z, False, self.s.pen_slow_speed, "lower")
        return steps

    def goto(self, x: float, y: float, z: float) -> list[Step]:
        travel_z = max(self.lift_z, z)
        steps = self.lift()
        steps += self._arc(x, y, travel_z, self.s.travel_speed)
        steps += self._line(x, y, z, False, self.s.travel_speed / 2, "lower" if z < travel_z else "lift")
        return steps

    def stroke(self, points: list[Point]) -> list[Step]:
        if len(points) < 2:
            return []
        steps = self.goto(points[0][0], points[0][1], self.lift_z)
        steps += self.settle()
        steps += self.lower()
        steps += self.settle()
        for x, y in points[1:] + self._overlap(points):
            steps += self._line(x, y, self.paper_z, True, self.s.draw_speed, "draw")
        steps += self.settle()
        steps += self.lift()
        steps += self.settle()
        return steps

    def settle(self) -> list[Step]:
        if self.s.pen_settle <= 0 or self.tip is None:
            return []
        sol = self.k.ik_xyz(*self.tip)
        return [Step(*self.tip, False, self.s.pen_settle * 1000.0, sol, "settle")]

    def home(self) -> list[Step]:
        if self.recover_from is not None:
            return self.recover_home()
        return self.goto(*self.k.home())

    def recover_home(self) -> list[Step]:
        start = list(self.recover_from)
        goal = self.k.ik_xyz(*self.k.home())
        if not goal.ok:
            return [Step(*self.k.home(), False, 0, goal, "recover")]
        start_tip_z = self.k.fk(start)["tip"][2]
        biggest = max(abs(g - s) for g, s in zip(goal.joints, start))
        n = max(1, math.ceil(biggest / 1.0))
        ms = (biggest / n) / RECOVER_SPEED_DPS * 1000.0
        steps = []
        for k in range(1, n + 1):
            t = k / n
            joints = [s + (g - s) * t for s, g in zip(start, goal.joints)]
            if not self.s.base_enabled:
                joints[0] = self.s.base_yaw
            sol = self.k.check(joints)
            tx, ty, tz, _ = sol.pose["tip"]
            if tz < self.paper_z + RECOVER_CLEARANCE and tz < start_tip_z - 0.2:
                sol.problems.append("marker would drop toward the paper while returning home")
            steps.append(Step(tx, ty, tz, False, ms, sol, "recover"))
        self.tip = self.k.home()
        self.recover_from = None
        return steps

    def _overlap(self, points: list[Point]) -> list[Point]:
        left = self.s.close_overlap
        if left <= 0 or len(points) < 3 or math.dist(points[0], points[-1]) > 0.01:
            return []
        out = []
        for a, b in zip(points, points[1:]):
            seg = math.dist(a, b)
            if seg <= 0:
                continue
            if seg >= left:
                t = left / seg
                out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
                return out
            out.append(b)
            left -= seg
        return out


class Backlash:
    def __init__(self, kinematics: Kinematics):
        self.k = kinematics
        self.dir = 0
        self.comp = 0.0

    def reset(self) -> None:
        self.dir, self.comp = 0, 0.0

    def apply(self, steps: list[Step], start_servo: list[float] | None) -> None:
        s = self.k.settings
        if not s.base_enabled or s.base_backlash <= 0 or not steps:
            for step in steps:
                step.cmd = None
            self.reset()
            return
        base = s.servos[0]
        half = s.base_backlash / 2 * base.dir * base.scale
        if start_servo is None:
            anchor = steps[0].sol.joints[0]
        else:
            anchor = self.k.servo_to_joints([start_servo[0] - self.comp] + list(start_servo[1:]))[0]
        d = self.dir
        for step in steps:
            j = step.sol.joints[0]
            if j - anchor > 0.05:
                d, anchor = 1, j
            elif anchor - j > 0.05:
                d, anchor = -1, j
            elif (j - anchor) * d > 0:
                anchor = j
            step.cmd = list(step.sol.servo)
            step.cmd[0] += d * half
        self.dir, self.comp = d, d * half


def find_problems(steps: list[Step], start_servo: list[float] | None,
                  kinematics: Kinematics) -> list[tuple[int, Step, list[str]]]:
    s: Settings = kinematics.settings
    bad = []
    prev = start_servo
    for i, step in enumerate(steps):
        problems = list(step.sol.problems)
        out = step.servo_out()
        if step.cmd is not None:
            problems += [p for p in kinematics.limit_problems(out) if p not in problems]
        if prev is not None:
            for k, (a, b) in enumerate(zip(prev, out)):
                if k == 0 and not s.base_enabled:
                    continue
                if abs(a - b) > s.max_joint_step:
                    problems.append(f"{s.servos[k].name} would jump {abs(a - b):.1f} deg in one step")
        if problems:
            bad.append((i, step, problems))
        prev = out
    return bad


def summarize(steps: list[Step]) -> dict:
    ink = sum(math.dist((a.x, a.y), (b.x, b.y)) for a, b in zip(steps, steps[1:]) if b.pen)
    return {
        "waypoints": len(steps),
        "seconds": round(sum(step.ms for step in steps) / 1000.0, 1),
        "ink_cm": round(ink, 1),
    }
