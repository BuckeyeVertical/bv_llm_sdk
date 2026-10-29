"""The drone camera streamed from bv_bevy.

A background thread keeps the newest frame along with the drone position when it
arrived. The camera points straight down and the top of the image is the way the drone
faces.
"""

from __future__ import annotations

import json
import math
import socket
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import geo

if TYPE_CHECKING:
    from .drone import Drone

DEFAULT_CAMERA_ADDR = ("127.0.0.1", 7002)


@dataclass
class Frame:
    """One camera image and where the drone was when it was taken."""

    jpeg: bytes
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float
    sequence: int
    received_at: float
    # Drone position when the frame arrived.
    latitude: float
    longitude: float
    altitude_m: float  # Height above the launch point.
    heading_deg: float

    def pixel_to_ground(self, u: float, v: float) -> tuple[float, float]:
        """Return the north and east distance from the drone to the ground under a pixel.

        Assumes a straight down camera over flat ground.
        """
        forward = -(v - self.cy) / self.fy * self.altitude_m
        right = (u - self.cx) / self.fx * self.altitude_m
        return geo.body_to_ned(forward, right, self.heading_deg)

    @property
    def ground_width_m(self) -> float:
        """Width of ground the image covers."""
        return self.width / self.fx * self.altitude_m

    @property
    def ground_height_m(self) -> float:
        """Length of ground the image covers."""
        return self.height / self.fy * self.altitude_m


class Camera:
    """Keep the newest frame from the camera stream."""

    def __init__(self, drone: Drone, addr: tuple[str, int] = DEFAULT_CAMERA_ADDR):
        self.drone = drone
        self.addr = addr
        self._cond = threading.Condition()
        self._frame: Frame | None = None
        self._error: str | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def latest(self, max_age_s: float = 2.0, timeout_s: float = 5.0) -> Frame:
        """Return the newest frame. Waits up to timeout_s for one newer than max_age_s.
        """
        from .drone import DroneError

        deadline = time.time() + timeout_s
        with self._cond:
            while True:
                frame = self._frame
                if frame is not None and time.time() - frame.received_at <= max_age_s:
                    return frame
                remaining = deadline - time.time()
                if remaining <= 0:
                    why = f" ({self._error})" if self._error else ""
                    raise DroneError(
                        f"no camera image from {self.addr[0]}:{self.addr[1]}{why}. "
                        "Is Bevy running?"
                    )
                self._cond.wait(timeout=remaining)

    def next(self, after: Frame | None, timeout_s: float = 5.0) -> Frame:
        """Return the first frame newer than after."""
        from .drone import DroneError

        deadline = time.time() + timeout_s
        with self._cond:
            while True:
                frame = self._frame
                if frame is not None and (after is None or frame.sequence != after.sequence):
                    return frame
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise DroneError("the camera stopped sending images.")
                self._cond.wait(timeout=remaining)

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            try:
                with socket.create_connection(self.addr, timeout=3.0) as sock:
                    reader = sock.makefile("rb")
                    while not self._stop.is_set():
                        line = reader.readline()
                        if not line:
                            raise ConnectionError("camera stream closed")
                        header = json.loads(line)
                        jpeg = reader.read(header["data_length"])
                        if len(jpeg) != header["data_length"]:
                            raise ConnectionError("camera stream closed mid-frame")
                        self._store(header, jpeg)
            except (OSError, ValueError, KeyError) as exc:
                self._error = str(exc)
                self._stop.wait(1.0)

    def _store(self, header: dict, jpeg: bytes) -> None:
        pos = self.drone.latest("GLOBAL_POSITION_INT")
        if pos is None:
            return
        heading = pos.hdg / 100.0 if pos.hdg != 65535 else 0.0
        width, height = header["width"], header["height"]
        # Older streams do not send intrinsics. Use the default field of view.
        fx = header.get("fx") or width / (2 * math.tan(math.radians(79.8) / 2))
        frame = Frame(
            jpeg=jpeg,
            width=width,
            height=height,
            fx=fx,
            fy=header.get("fy") or fx,
            cx=header.get("cx", width / 2),
            cy=header.get("cy", height / 2),
            sequence=header["sequence"],
            received_at=time.time(),
            latitude=pos.lat / 1e7,
            longitude=pos.lon / 1e7,
            altitude_m=max(pos.relative_alt / 1000.0, 0.1),
            heading_deg=heading,
        )
        with self._cond:
            self._frame = frame
            self._error = None
            self._cond.notify_all()
