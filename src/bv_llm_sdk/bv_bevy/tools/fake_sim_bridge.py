#!/usr/bin/env python3

import argparse
import math
import socket
import time
import uuid
from dataclasses import dataclass
from typing import BinaryIO

from sim_state import write_snapshot


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    rate_hz: float
    east_m: float
    north_m: float
    radius_m: float
    altitude_m: float


class FakeSimBridge:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.stream_id = str(uuid.uuid4())

    def run(self) -> None:
        with socket.create_server(
            (self.config.host, self.config.port), reuse_port=False
        ) as server:
            print(f"Fake simulator listening on {self.config.host}:{self.config.port}")

            while True:
                connection, address = server.accept()
                print(f"Consumer connected from {address[0]}:{address[1]}")

                try:
                    with connection, connection.makefile("wb") as stream:
                        self._stream(stream)
                except (BrokenPipeError, ConnectionResetError):
                    print("Consumer disconnected")

    def _stream(self, stream: BinaryIO) -> None:
        period_s = 1.0 / self.config.rate_hz
        period_ns = round(period_s * 1_000_000_000)
        sequence = 0
        next_send = time.monotonic()

        while True:
            angle = sequence * period_s * 0.5
            position = [
                self.config.east_m + self.config.radius_m * math.cos(angle),
                self.config.north_m + self.config.radius_m * math.sin(angle),
                self.config.altitude_m + 0.5 * math.sin(angle * 2.0),
            ]
            orientation = [0.0, 0.0, math.sin(angle / 2.0), math.cos(angle / 2.0)]
            camera_orientation = [0.0, math.sqrt(0.5), 0.0, math.sqrt(0.5)]
            packet = {
                "schema": "bv.sim_state",
                "version": 1,
                "stream_id": self.stream_id,
                "sequence": sequence,
                "sim_time_ns": sequence * period_ns,
                "frame_id": "gazebo_world",
                "entities": [
                    {
                        "id": "vehicle/x500",
                        "kind": "vehicle",
                        "position_m": position,
                        "orientation_xyzw": orientation,
                    },
                    {
                        "id": "camera/drone",
                        "kind": "camera",
                        "position_m": position,
                        "orientation_xyzw": camera_orientation,
                    },
                ],
            }
            write_snapshot(stream, packet)
            stream.flush()

            sequence += 1
            next_send += period_s
            time.sleep(max(0.0, next_send - time.monotonic()))


def parse_args() -> Config:
    parser = argparse.ArgumentParser(description="Serve deterministic BVSimState")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7001)
    parser.add_argument("--rate-hz", type=float, default=30.0)
    parser.add_argument("--east-m", type=float, default=0.0)
    parser.add_argument("--north-m", type=float, default=0.0)
    parser.add_argument("--radius-m", type=float, default=4.0)
    parser.add_argument("--altitude-m", type=float, default=2.0)
    args = parser.parse_args()

    if args.rate_hz <= 0.0:
        parser.error("--rate-hz must be positive")

    return Config(
        host=args.host,
        port=args.port,
        rate_hz=args.rate_hz,
        east_m=args.east_m,
        north_m=args.north_m,
        radius_m=args.radius_m,
        altitude_m=args.altitude_m,
    )


if __name__ == "__main__":
    try:
        FakeSimBridge(parse_args()).run()
    except KeyboardInterrupt:
        print("\nFake simulator stopped")
