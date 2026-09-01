#!/usr/bin/env python3

import argparse
import math
import socket
import subprocess
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, BinaryIO

from camera_stream import PinholeIntrinsics, read_frame, read_intrinsics
from sim_state import read_snapshot


@dataclass(frozen=True)
class Config:
    state_host: str
    state_port: int
    camera_host: str
    camera_port: int
    camera_id: str
    target_position: tuple[float, float, float]
    horizontal_fov_degrees: float
    tolerance_pixels: float
    attempts: int


class StateHistory:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.snapshots: OrderedDict[tuple[str, int], dict[str, Any]] = OrderedDict()
        self.condition = threading.Condition()
        self.error: Exception | None = None
        self.stop = threading.Event()
        self.worker = threading.Thread(target=self._receive, daemon=True)

    def __enter__(self) -> "StateHistory":
        self.worker.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop.set()
        self.worker.join(timeout=2)

    def wait_until_ready(self, timeout: float = 5) -> None:
        deadline = time.monotonic() + timeout
        with self.condition:
            while not self.snapshots:
                if self.error is not None:
                    raise RuntimeError(f"state receiver failed: {self.error}") from self.error
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("simulation state stream did not become ready")
                self.condition.wait(remaining)

    def wait_for(
        self, stream_id: str | None, sequence: int, timeout: float = 5
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                snapshot = self._find(stream_id, sequence)
                if snapshot is not None:
                    return snapshot
                if self.error is not None:
                    raise RuntimeError(f"state receiver failed: {self.error}") from self.error
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    identity = stream_id or "unknown stream"
                    raise RuntimeError(
                        f"state sequence {sequence} from {identity} was not observed"
                    )
                self.condition.wait(remaining)

    def _find(self, stream_id: str | None, sequence: int) -> dict[str, Any] | None:
        if stream_id is not None:
            return self.snapshots.get((stream_id, sequence))
        for (_, candidate_sequence), snapshot in reversed(self.snapshots.items()):
            if candidate_sequence == sequence:
                return snapshot
        return None

    def _receive(self) -> None:
        try:
            with socket.create_connection((self.host, self.port), timeout=5) as connection:
                connection.settimeout(1)
                stream = connection.makefile("rb")
                while not self.stop.is_set():
                    try:
                        snapshot = read_snapshot(stream)
                    except TimeoutError:
                        continue
                    self._store(snapshot)
        except Exception as error:
            with self.condition:
                self.error = error
                self.condition.notify_all()

    def _store(self, snapshot: dict[str, Any]) -> None:
        sequence = snapshot["sequence"]
        key = (snapshot["stream_id"], sequence)

        with self.condition:
            self.snapshots[key] = snapshot
            self.snapshots.move_to_end(key)
            while len(self.snapshots) > 500:
                self.snapshots.popitem(last=False)
            self.condition.notify_all()


class GeometryVerifier:
    def __init__(self, config: Config) -> None:
        self.config = config

    def run(self) -> None:
        with StateHistory(self.config.state_host, self.config.state_port) as states:
            states.wait_until_ready()
            with socket.create_connection(
                (self.config.camera_host, self.config.camera_port), timeout=10
            ) as connection:
                connection.settimeout(10)
                stream = connection.makefile("rb")
                measurements = self._measure_frames(stream, states)

        best = min(measurements, key=lambda measurement: measurement[0])
        error, expected, observed, sequence = best
        if error > self.config.tolerance_pixels:
            raise RuntimeError(
                f"best target projection error {error:.2f}px exceeds "
                f"{self.config.tolerance_pixels:.2f}px"
            )

        print(
            f"Verified target projection at sim sequence {sequence}: "
            f"expected ({expected[0]:.2f}, {expected[1]:.2f}), "
            f"observed ({observed[0]:.2f}, {observed[1]:.2f}), error {error:.2f}px"
        )

    def _measure_frames(
        self, stream: BinaryIO, states: StateHistory
    ) -> list[tuple[float, tuple[float, float], tuple[float, float], int]]:
        frames = [read_frame(stream) for _ in range(self.config.attempts)]
        measurements = []
        failures = []
        for frame in frames:
            try:
                header = frame.header
                snapshot = states.wait_for(
                    header.get("sim_stream_id"), header["sim_sequence"]
                )
                camera = find_entity(snapshot, self.config.camera_id)
                expected = project_target(
                    camera,
                    self.config.target_position,
                    header["width"],
                    header["height"],
                    self.config.horizontal_fov_degrees,
                    read_intrinsics(header),
                )
                observed = magenta_centroid(
                    decode_rgb(frame.jpeg, header["width"], header["height"]),
                    header["width"],
                    header["height"],
                )
                error = math.dist(expected, observed)
                measurements.append((error, expected, observed, header["sim_sequence"]))
            except RuntimeError as error:
                failures.append(str(error))

        if not measurements:
            details = "; ".join(dict.fromkeys(failures))
            raise RuntimeError(f"no measurable target frame found: {details}")
        return measurements


def find_entity(snapshot: dict[str, Any], entity_id: str) -> dict[str, Any]:
    for entity in snapshot.get("entities", []):
        if entity.get("id") == entity_id:
            return entity
    raise RuntimeError(f"simulation state has no entity '{entity_id}'")


def project_target(
    camera: dict[str, Any],
    target: tuple[float, float, float],
    width: int,
    height: int,
    horizontal_fov_degrees: float,
    intrinsics: PinholeIntrinsics | None = None,
) -> tuple[float, float]:
    position = vector_subtract(target, tuple(camera["position_m"]))
    camera_vector = rotate_vector(quaternion_conjugate(camera["orientation_xyzw"]), position)
    depth = camera_vector[0]
    if depth <= 0:
        raise RuntimeError("target is behind the camera")

    if intrinsics is None:
        focal_length = width / (
            2 * math.tan(math.radians(horizontal_fov_degrees) / 2)
        )
        intrinsics = PinholeIntrinsics(
            fx=focal_length,
            fy=focal_length,
            cx=width / 2,
            cy=height / 2,
        )
    return (
        intrinsics.cx - intrinsics.fx * camera_vector[1] / depth,
        intrinsics.cy - intrinsics.fy * camera_vector[2] / depth,
    )


def quaternion_conjugate(quaternion: list[float]) -> tuple[float, float, float, float]:
    x, y, z, w = quaternion
    norm_squared = x * x + y * y + z * z + w * w
    if norm_squared <= 0:
        raise RuntimeError("camera orientation is invalid")
    return (-x / norm_squared, -y / norm_squared, -z / norm_squared, w / norm_squared)


def rotate_vector(
    quaternion: tuple[float, float, float, float],
    vector: tuple[float, float, float],
) -> tuple[float, float, float]:
    x, y, z, w = quaternion
    vx, vy, vz = vector
    tx = 2 * (y * vz - z * vy)
    ty = 2 * (z * vx - x * vz)
    tz = 2 * (x * vy - y * vx)
    return (
        vx + w * tx + y * tz - z * ty,
        vy + w * ty + z * tx - x * tz,
        vz + w * tz + x * ty - y * tx,
    )


def vector_subtract(
    left: tuple[float, float, float], right: tuple[float, float, float]
) -> tuple[float, float, float]:
    return tuple(a - b for a, b in zip(left, right, strict=True))


def decode_rgb(jpeg: bytes, width: int, height: int) -> bytes:
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "image2pipe",
            "-i",
            "pipe:0",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        input=jpeg,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg could not decode JPEG: {result.stderr.decode().strip()}")
    expected_length = width * height * 3
    if len(result.stdout) != expected_length:
        raise RuntimeError("decoded image dimensions do not match frame header")
    return result.stdout


def magenta_centroid(rgb: bytes, width: int, height: int) -> tuple[float, float]:
    count = 0
    x_total = 0
    y_total = 0
    for index in range(0, len(rgb), 3):
        red, green, blue = rgb[index : index + 3]
        if red > 150 and blue > 100 and green < 110 and red > green * 2:
            pixel = index // 3
            x_total += pixel % width
            y_total += pixel // width
            count += 1
    if count < 20:
        raise RuntimeError("magenta calibration target is not visible")
    return x_total / count, y_total / count


def parse_args() -> Config:
    parser = argparse.ArgumentParser(description="Verify Bevy camera projection geometry")
    parser.add_argument("--state-host", default="127.0.0.1")
    parser.add_argument("--state-port", type=int, default=7001)
    parser.add_argument("--camera-host", default="127.0.0.1")
    parser.add_argument("--camera-port", type=int, default=7002)
    parser.add_argument("--camera-id", default="camera/drone")
    parser.add_argument("--target", type=float, nargs=3, default=(0.0, -2.0, 0.075))
    parser.add_argument("--hfov-deg", type=float, default=65.847456)
    parser.add_argument("--tolerance-pixels", type=float, default=8)
    parser.add_argument("--attempts", type=int, default=20)
    args = parser.parse_args()
    if args.attempts < 1:
        parser.error("--attempts must be positive")
    if args.tolerance_pixels <= 0:
        parser.error("--tolerance-pixels must be positive")
    return Config(
        state_host=args.state_host,
        state_port=args.state_port,
        camera_host=args.camera_host,
        camera_port=args.camera_port,
        camera_id=args.camera_id,
        target_position=tuple(args.target),
        horizontal_fov_degrees=args.hfov_deg,
        tolerance_pixels=args.tolerance_pixels,
        attempts=args.attempts,
    )


if __name__ == "__main__":
    try:
        GeometryVerifier(parse_args()).run()
    except (ConnectionError, OSError, RuntimeError, ValueError) as error:
        raise SystemExit(f"Verification failed: {error}") from error
