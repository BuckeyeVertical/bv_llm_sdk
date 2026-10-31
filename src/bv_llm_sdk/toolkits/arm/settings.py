from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CALIBRATION_FILE = Path(__file__).with_name("calibration.json")


@dataclass
class Servo:
    name: str
    offset: float
    dir: int
    min: float
    max: float
    scale: float = 1.0


@dataclass
class Settings:
    base_height: float
    shoulder_offset: float
    l1: float
    l2: float
    l3: float
    elbow_up: bool
    paper_z: float
    pen_lift: float
    step: float
    draw_speed: float
    travel_speed: float
    center_x: float
    center_y: float
    text_size: float
    text_start_x: float
    text_start_y: float
    line_spacing: float
    paper_box: list[float] | None
    home_x: float
    home_y: float
    home_z: float
    min_r: float
    max_joint_step: float
    base_backlash: float
    pen_settle: float
    lift_kick_servo: int
    lift_kick_deg: float
    lift_kick_speed: float
    pen_slow_zone: float
    pen_slow_speed: float
    close_overlap: float
    base_enabled: bool
    base_yaw: float
    servos: list[Servo]


def load_settings(path: Path = CALIBRATION_FILE) -> Settings:
    data = json.loads(path.read_text())
    servos = [Servo(**servo) for servo in data.pop("servos")]
    return Settings(servos=servos, **data)
