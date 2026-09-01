#!/usr/bin/env python3

import argparse
import hashlib
import json
import socket
from dataclasses import dataclass
from pathlib import Path

from camera_stream import is_simulation_reset, read_frame, simulated_duration_ns


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    frames: int
    require_change: bool
    require_reset: bool
    output: Path | None


class CameraStreamVerifier:
    def __init__(self, config: Config) -> None:
        self.config = config

    def run(self) -> None:
        with socket.create_connection((self.config.host, self.config.port), timeout=10) as connection:
            connection.settimeout(10)
            stream = connection.makefile("rb")
            frames = [read_frame(stream) for _ in range(self.config.frames)]

        duration_s = simulated_duration_ns(frames) / 1_000_000_000
        resets = sum(
            is_simulation_reset(previous, current)
            for previous, current in zip(frames, frames[1:])
        )
        hashes = {hashlib.sha256(frame.jpeg).digest() for frame in frames}
        if self.config.require_change and len(hashes) < 2:
            raise RuntimeError("camera image did not change across captured simulation states")
        if self.config.require_reset and resets == 0:
            raise RuntimeError("camera stream did not cross a simulation reset")
        if self.config.output is not None:
            self.config.output.write_bytes(frames[-1].jpeg)

        first = frames[0].header
        last = frames[-1].header
        print(
            f"Verified {len(frames)} JPEG frames at {first['width']}x{first['height']} "
            f"over {duration_s:.3f} simulated seconds across {resets} reset(s)"
        )


def parse_args() -> Config:
    parser = argparse.ArgumentParser(description="Verify a BV camera frame stream")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7002)
    parser.add_argument("--frames", type=int, default=5)
    parser.add_argument("--require-change", action="store_true")
    parser.add_argument("--require-reset", action="store_true")
    parser.add_argument("--output", type=Path, help="save the last verified JPEG")
    args = parser.parse_args()

    if args.frames < 2:
        parser.error("--frames must be at least 2")

    return Config(
        host=args.host,
        port=args.port,
        frames=args.frames,
        require_change=args.require_change,
        require_reset=args.require_reset,
        output=args.output,
    )


if __name__ == "__main__":
    try:
        CameraStreamVerifier(parse_args()).run()
    except (ConnectionError, EOFError, json.JSONDecodeError, OSError, RuntimeError, ValueError) as error:
        raise SystemExit(f"Verification failed: {error}") from error
