from __future__ import annotations

import math
import time
from typing import Callable

from .font import FONT, GLYPH_ADVANCE, glyph_strokes
from .kinematics import Kinematics
from .link import ArmError, Link, SimLink, find_port
from .planner import Backlash, Planner, Point, Step, find_problems, summarize
from .settings import Settings, load_settings


class Arm:
    def __init__(self, link: Link, settings: Settings):
        self.link = link
        self.settings = settings
        self.kinematics = Kinematics(settings)
        self.planner = Planner(self.kinematics)
        self.backlash = Backlash(self.kinematics)
        self.cursor: tuple[float, float] | None = None

    def close(self) -> None:
        self.link.close()

    def sync_calibration(self) -> None:
        firmware = self.link.read_calibration()
        for i, servo in enumerate(self.settings.servos):
            for key in ("min", "max", "offset", "dir"):
                setattr(servo, key, firmware[i][key])
        if not firmware[0]["enabled"]:
            self.settings.base_enabled = False

    def check_settings(self) -> None:
        problems = self.kinematics.sanity_problems()
        if problems:
            raise ArmError("fix the arm settings: " + "; ".join(problems))

    def _needed_servos(self) -> list[int]:
        return [1, 2, 3] + ([0] if self.settings.base_enabled else [])

    def is_holding(self) -> bool:
        _, attached = self.link.status()
        return all(attached[i] == "1" for i in self._needed_servos())

    def attach(self) -> None:
        self.link.send("A")
        self._read_pose()

    def _joints(self, servo: list[float]) -> list[float]:
        joints = self.kinematics.servo_to_joints(servo)
        if not self.settings.base_enabled:
            joints[0] = self.settings.base_yaw
        return joints

    def _read_pose(self) -> str:
        angles, attached = self.link.status()
        true_angles = list(angles)
        true_angles[0] -= self.backlash.comp
        joints = self._joints(true_angles)
        if not self.settings.base_enabled:
            angles[0] = self.kinematics.joints_to_servo(joints)[0]
        tip = self.kinematics.fk(joints)["tip"][:3]
        self.planner.tip = tip
        self.planner.servo = angles
        expected = self.kinematics.ik_xyz(*tip)
        matches = all(abs(a - b) < 3.0 for a, b in zip(expected.joints[1:], joints[1:]))
        self.planner.recover_from = None if expected.ok and matches else joints
        return attached

    def _ensure_ready(self, allow_recover: bool) -> None:
        try:
            attached = self._read_pose()
        except ArmError:
            self.link.resync()
            raise
        if any(attached[i] != "1" for i in self._needed_servos()):
            raise ArmError("some servos are limp. A person must pose the arm at park and reconnect.")
        if self.planner.recover_from is not None and not allow_recover:
            raise ArmError("the arm is not in a drawing pose. Call go_home first.")

    def _stream(self, steps: list[Step]) -> None:
        for step in steps:
            if step.kind == "settle":
                self.link.send("Z", timeout=30.0)
                if not self.link.simulated:
                    time.sleep(step.ms / 1000.0)
                continue
            self._moves_sent += 1
            self.link.send(step.command(), timeout=10.0, strict=True)
        self.link.send("Z", timeout=30.0)

    def execute(self, build: Callable[[], list[Step]], allow_recover: bool = False) -> dict:
        self._ensure_ready(allow_recover)
        steps = build()
        start = self.planner.servo
        saved = self.backlash.dir, self.backlash.comp
        self.backlash.apply(steps, start)
        bad = find_problems(steps, start, self.kinematics)
        if bad:
            self.backlash.dir, self.backlash.comp = saved
            self.planner.tip = None
            raise ArmError(_refusal(bad, len(steps)))
        self._moves_sent = 0
        try:
            self._stream(steps)
        except ArmError:
            if not self._moves_sent:
                self.backlash.dir, self.backlash.comp = saved
            self.link.resync()
            raise
        finally:
            self.planner.tip = None
        return summarize(steps)

    def halt(self) -> None:
        self.planner.tip = None
        self.backlash.reset()
        try:
            self.link.send("Z", timeout=5.0)
        except ArmError:
            pass
        self.link.resync()

    def status(self) -> dict:
        angles, attached = self.link.status()
        true_angles = list(angles)
        true_angles[0] -= self.backlash.comp
        joints = self._joints(true_angles)
        x, y, z, r = self.kinematics.fk(joints)["tip"]
        return {
            "simulated": self.link.simulated,
            "holding": all(attached[i] == "1" for i in self._needed_servos()),
            "tip_x_cm": round(x, 2),
            "tip_y_cm": round(y, 2),
            "tip_z_cm": round(z, 2),
            "tip_r_cm": round(r, 2),
            "tip_theta_deg": round(math.degrees(math.atan2(y, x)), 1),
            "pen_on_paper": z <= self.settings.paper_z + 0.05,
            "text_cursor": self.text_cursor(),
        }

    def text_cursor(self) -> dict:
        x, y = self._cursor()
        return {"x_cm": round(x, 2), "y_cm": round(y, 2), "size_cm": self.settings.text_size}

    def _cursor(self) -> tuple[float, float]:
        if self.cursor is None:
            self.cursor = (self.settings.text_start_x, self.settings.text_start_y)
        return self.cursor

    def write(self, text: str) -> dict:
        s = self.settings
        text = text.upper()
        bad = sorted({c for c in text if c not in FONT and c not in " \n"})
        if bad:
            raise ArmError(f"cannot write {' '.join(bad)}. Use A-Z, 0-9, ! ? and spaces.")
        advance = GLYPH_ADVANCE / 6.0 * s.text_size
        x, y = self._cursor()
        strokes: list[list[Point]] = []
        for c in text:
            if c == " ":
                y -= advance
            elif c == "\n":
                x -= s.line_spacing * s.text_size
                y = s.text_start_y
            else:
                strokes += glyph_strokes(c, y, x, s.text_size)
                y -= advance
        result = {}
        if strokes:
            def build() -> list[Step]:
                steps = []
                for stroke in strokes:
                    steps += self.planner.stroke(stroke)
                return steps
            result = self.execute(build)
        self.cursor = (x, y)
        return {**result, "text_cursor": self.text_cursor()}


def _refusal(bad: list[tuple[int, Step, list[str]]], total: int) -> str:
    details = [f"x={s.x:.1f} y={s.y:.1f} z={s.z:.1f}: {'; '.join(p)}" for _, s, p in bad[:3]]
    return f"refused, nothing moved. {len(bad)} of {total} waypoints have problems. " + " | ".join(details)


def connect(port: str | None = None, sim: bool = False) -> Arm:
    settings = load_settings()
    if sim:
        home = Kinematics(settings).ik_xyz(settings.home_x, settings.home_y, settings.home_z)
        return Arm(SimLink(home.servo, settings.base_enabled), settings)
    arm = Arm(Link(port or find_port()), settings)
    try:
        arm.sync_calibration()
        arm.check_settings()
    except ArmError:
        arm.close()
        raise
    return arm
