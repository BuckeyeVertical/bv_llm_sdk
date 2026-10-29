"""Simulator setup for the evals.

Before each scenario the drone is put on the launch pad or hovering above it so
scenarios do not depend on each other. PX4 sets home where the drone arms so landing on
the pad first keeps home on the pad.
"""

from __future__ import annotations

import math
import subprocess
import time
from dataclasses import dataclass, field

from .. import geo
from ..actions import goto_location, land, set_heading, takeoff
from ..drone import Drone, DroneError

# The forest world launch pad and the mannequin near it.
FOREST_PAD = (36.2146748, -96.0065553)
PERSON = geo.offset(*FOREST_PAD, 18.0, 14.0)

HOVER_ALT_M = 10.0


@dataclass
class Pose:
    north: float  # Meters north of the pad.
    east: float
    alt: float  # Meters above launch.
    heading: float
    armed: bool

    def dist(self, other: "Pose") -> float:
        return math.hypot(self.north - other.north, self.east - other.east)


def pose(drone: Drone) -> Pose:
    p = drone.wait_for("GLOBAL_POSITION_INT")
    north, east = geo.north_east(*FOREST_PAD, p.lat / 1e7, p.lon / 1e7)
    return Pose(north, east, p.relative_alt / 1000, p.hdg / 100, drone.armed)


def _home_on_pad(drone: Drone) -> bool:
    home = drone.latest("HOME_POSITION", max_age_s=float("inf"))
    if home is None:
        return False
    return geo.distance(*FOREST_PAD, home.latitude / 1e7, home.longitude / 1e7) < 1.5


def _reset_agent_state(drone: Drone) -> None:
    drone.places.clear()
    drone.cruise_speed_m_s = None


def on_pad(drone: Drone) -> None:
    """Land and disarm on the launch pad."""
    _reset_agent_state(drone)
    p = pose(drone)
    if not p.armed and math.hypot(p.north, p.east) < 1.5:
        return
    if not p.armed:
        takeoff(drone, 6)
    goto_location(drone, *FOREST_PAD)
    land(drone)
    # Let PX4 disarm before the next arm.
    time.sleep(2)


def hovering(drone: Drone, north: float = 0.0, east: float = 0.0) -> None:
    """Hover at HOVER_ALT_M facing north at an offset from the pad with home on the pad.
    """
    _reset_agent_state(drone)
    if not drone.armed or not _home_on_pad(drone):
        on_pad(drone)
        _reset_agent_state(drone)
        takeoff(drone, HOVER_ALT_M)
    lat, lon = geo.offset(*FOREST_PAD, north, east)
    goto_location(drone, lat, lon, HOVER_ALT_M)
    set_heading(drone, 0.0)


def restart_simulator() -> None:
    """Restart the simulator. Fixes a stale battery state that blocks arming."""
    subprocess.run(["docker", "restart", "bv-simulator"], check=False,
                   capture_output=True, timeout=120)
    time.sleep(45)


@dataclass
class Track:
    """What the drone did during a scenario. Sampled twice a second."""

    path_m: float = 0.0
    min_alt: float = math.inf
    max_alt: float = -math.inf
    max_from_start_m: float = 0.0
    north_range: list = field(default_factory=lambda: [math.inf, -math.inf])
    east_range: list = field(default_factory=lambda: [math.inf, -math.inf])

    @property
    def width_m(self) -> float:
        """Horizontal size of everything flown."""
        if self.north_range[0] == math.inf:
            return 0.0
        return math.hypot(self.north_range[1] - self.north_range[0],
                          self.east_range[1] - self.east_range[0])


class Tracker:
    def __init__(self, drone: Drone):
        self.drone = drone
        self.track = Track()
        self._stop = False

    def run(self, start: Pose) -> None:
        last = start
        while not self._stop:
            time.sleep(0.5)
            try:
                now = pose(self.drone)
            except DroneError:
                continue
            t = self.track
            t.path_m += now.dist(last)
            t.min_alt = min(t.min_alt, now.alt)
            t.max_alt = max(t.max_alt, now.alt)
            t.max_from_start_m = max(t.max_from_start_m, now.dist(start))
            t.north_range = [min(t.north_range[0], now.north), max(t.north_range[1], now.north)]
            t.east_range = [min(t.east_range[0], now.east), max(t.east_range[1], now.east)]
            last = now

    def stop(self) -> None:
        self._stop = True
