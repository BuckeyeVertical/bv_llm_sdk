#!/usr/bin/env python3

import argparse
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable

from camera_stream import CameraFrame, is_simulation_reset, read_recording, write_frame


@dataclass(frozen=True)
class Config:
    recording: Path
    host: str
    port: int
    speed: float
    loop: bool
    once: bool


def replay_frames(
    stream: BinaryIO,
    frames: list[CameraFrame],
    speed: float,
    loop: bool,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    while True:
        previous = frames[0]
        for index, frame in enumerate(frames):
            if index:
                if not is_simulation_reset(previous, frame):
                    delay = (
                        frame.header["sim_time_ns"] - previous.header["sim_time_ns"]
                    ) / 1_000_000_000
                    sleep(delay / speed)
                previous = frame
            write_frame(stream, frame)
            stream.flush()
        if not loop:
            return


def serve_consumer(
    connection: socket.socket, frames: list[CameraFrame], speed: float, loop: bool
) -> None:
    try:
        with connection, connection.makefile("wb") as stream:
            replay_frames(stream, frames, speed, loop)
    except (BrokenPipeError, ConnectionResetError, OSError):
        return


def serve(config: Config) -> None:
    frames = read_recording(config.recording)
    with socket.create_server((config.host, config.port), reuse_port=False) as server:
        address = server.getsockname()
        print(
            f"Replaying {len(frames)} frames on {address[0]}:{address[1]} "
            f"at {config.speed:g}x",
            flush=True,
        )
        while True:
            connection, _ = server.accept()
            worker = threading.Thread(
                target=serve_consumer,
                args=(connection, frames, config.speed, config.loop),
                daemon=True,
            )
            worker.start()
            if config.once:
                worker.join()
                return


def parse_args() -> Config:
    parser = argparse.ArgumentParser(description="Replay a BV camera recording")
    parser.add_argument("recording", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7002)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.speed <= 0:
        parser.error("--speed must be positive")
    return Config(args.recording, args.host, args.port, args.speed, args.loop, args.once)


if __name__ == "__main__":
    try:
        serve(parse_args())
    except KeyboardInterrupt:
        print("\nReplay stopped")
    except (OSError, ValueError) as error:
        raise SystemExit(f"Replay failed: {error}") from error
