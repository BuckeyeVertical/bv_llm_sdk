#!/usr/bin/env python3

import argparse
import json
import socket
from dataclasses import dataclass
from pathlib import Path

from sim_state import read_snapshot, write_recording


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    snapshots: int
    output: Path


def record(config: Config) -> None:
    with socket.create_connection((config.host, config.port), timeout=10) as connection:
        connection.settimeout(10)
        source = connection.makefile("rb")
        snapshots = [read_snapshot(source) for _ in range(config.snapshots)]

    write_recording(config.output, snapshots)

    print(f"Recorded {config.snapshots} snapshots to {config.output}")


def parse_args() -> Config:
    parser = argparse.ArgumentParser(description="Record a BV simulation state stream")
    parser.add_argument("output", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7001)
    parser.add_argument("--snapshots", type=int, default=300)
    args = parser.parse_args()
    if args.snapshots < 1:
        parser.error("--snapshots must be positive")
    return Config(args.host, args.port, args.snapshots, args.output)


if __name__ == "__main__":
    try:
        record(parse_args())
    except (ConnectionError, EOFError, json.JSONDecodeError, OSError, ValueError) as error:
        raise SystemExit(f"Recording failed: {error}") from error
