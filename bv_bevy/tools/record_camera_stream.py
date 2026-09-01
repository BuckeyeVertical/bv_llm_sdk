#!/usr/bin/env python3

import argparse
import json
import socket
from dataclasses import dataclass
from pathlib import Path

from camera_stream import RECORDING_MAGIC, read_frame, write_frame


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    frames: int
    output: Path


def record(config: Config) -> None:
    with socket.create_connection((config.host, config.port), timeout=10) as connection:
        connection.settimeout(10)
        source = connection.makefile("rb")
        with config.output.open("wb") as destination:
            destination.write(RECORDING_MAGIC)
            for _ in range(config.frames):
                write_frame(destination, read_frame(source))

    print(f"Recorded {config.frames} frames to {config.output}")


def parse_args() -> Config:
    parser = argparse.ArgumentParser(description="Record a BV camera stream")
    parser.add_argument("output", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7002)
    parser.add_argument("--frames", type=int, default=300)
    args = parser.parse_args()
    if args.frames < 1:
        parser.error("--frames must be positive")
    return Config(args.host, args.port, args.frames, args.output)


if __name__ == "__main__":
    try:
        record(parse_args())
    except (ConnectionError, EOFError, json.JSONDecodeError, OSError, ValueError) as error:
        raise SystemExit(f"Recording failed: {error}") from error
