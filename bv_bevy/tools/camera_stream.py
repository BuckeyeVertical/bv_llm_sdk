import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Iterable


SCHEMA = "bv.camera_frame"
VERSION = 1
RECORDING_MAGIC = b"BVCAM\x00\x01\n"
MAX_HEADER_BYTES = 64 * 1024
MAX_PAYLOAD_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class CameraFrame:
    header: dict[str, Any]
    jpeg: bytes


@dataclass(frozen=True)
class PinholeIntrinsics:
    fx: float
    fy: float
    cx: float
    cy: float


def read_frame(stream: BinaryIO) -> CameraFrame:
    line = stream.readline(MAX_HEADER_BYTES + 1)
    if not line:
        raise EOFError("camera stream closed")
    if len(line) > MAX_HEADER_BYTES or not line.endswith(b"\n"):
        raise ValueError("camera frame header exceeds size limit")

    header = json.loads(line)
    validate_header(header)
    jpeg = read_exact(stream, header["data_length"])
    if jpeg[:2] != b"\xff\xd8" or jpeg[-2:] != b"\xff\xd9":
        raise ValueError("camera payload is not a complete JPEG image")
    return CameraFrame(header, jpeg)


def write_frame(stream: BinaryIO, frame: CameraFrame) -> None:
    header = dict(frame.header)
    header["data_length"] = len(frame.jpeg)
    validate_header(header)
    stream.write(json.dumps(header, separators=(",", ":")).encode())
    stream.write(b"\n")
    stream.write(frame.jpeg)


def read_recording(path: Path) -> list[CameraFrame]:
    with path.open("rb") as stream:
        if stream.read(len(RECORDING_MAGIC)) != RECORDING_MAGIC:
            raise ValueError("unsupported camera recording format")
        frames = list(iter_frames(stream))
    if not frames:
        raise ValueError("camera recording contains no frames")
    validate_order(frames)
    return frames


def write_recording(path: Path, frames: Iterable[CameraFrame]) -> int:
    count = 0
    with path.open("wb") as stream:
        stream.write(RECORDING_MAGIC)
        for frame in frames:
            write_frame(stream, frame)
            count += 1
    return count


def iter_frames(stream: BinaryIO) -> Iterable[CameraFrame]:
    while True:
        try:
            yield read_frame(stream)
        except EOFError:
            return


def validate_header(header: Any) -> None:
    if not isinstance(header, dict):
        raise ValueError("camera frame header is not an object")
    if header.get("schema") != SCHEMA or header.get("version") != VERSION:
        raise ValueError("unsupported camera frame schema")
    if header.get("encoding") != "jpeg":
        raise ValueError("unsupported camera frame encoding")
    for field in ("sequence", "sim_sequence", "sim_time_ns"):
        value = header.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"camera frame has invalid {field}")
    for field in ("width", "height"):
        value = header.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"camera frame has invalid {field}")
    if not isinstance(header.get("camera_id"), str) or not header["camera_id"]:
        raise ValueError("camera frame has invalid camera_id")
    sim_stream_id = header.get("sim_stream_id")
    if sim_stream_id is not None and (
        not isinstance(sim_stream_id, str) or not sim_stream_id.strip()
    ):
        raise ValueError("camera frame has invalid sim_stream_id")
    read_intrinsics(header)
    length = header.get("data_length")
    if (
        not isinstance(length, int)
        or isinstance(length, bool)
        or not 0 < length <= MAX_PAYLOAD_BYTES
    ):
        raise ValueError("invalid camera frame payload length")


def read_intrinsics(header: dict[str, Any]) -> PinholeIntrinsics | None:
    fields = ("camera_model", "fx", "fy", "cx", "cy")
    present = tuple(field in header for field in fields)
    if not any(present):
        return None
    if not all(present):
        raise ValueError("camera frame has incomplete intrinsics")
    if header["camera_model"] != "pinhole":
        raise ValueError("unsupported camera model")

    values = {}
    for field in ("fx", "fy", "cx", "cy"):
        value = header[field]
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
        ):
            raise ValueError(f"camera frame has invalid {field}")
        values[field] = float(value)
    if values["fx"] <= 0 or values["fy"] <= 0:
        raise ValueError("camera frame focal lengths must be positive")
    return PinholeIntrinsics(**values)


def validate_order(frames: list[CameraFrame]) -> None:
    for previous, current in zip(frames, frames[1:]):
        reset = is_simulation_reset(previous, current)
        if current.header["sequence"] <= previous.header["sequence"] and not reset:
            raise ValueError("camera frame sequence did not increase")
        if reset:
            continue
        if current.header["sim_sequence"] <= previous.header["sim_sequence"]:
            raise ValueError("simulation sequence did not increase")
        if current.header["sim_time_ns"] <= previous.header["sim_time_ns"]:
            raise ValueError("camera frame simulation time did not increase")


def is_simulation_reset(previous: CameraFrame, current: CameraFrame) -> bool:
    previous_id = previous.header.get("sim_stream_id")
    current_id = current.header.get("sim_stream_id")
    if previous_id != current_id and (previous_id is not None or current_id is not None):
        return True
    return (
        current.header["sim_sequence"] < previous.header["sim_sequence"]
        and current.header["sim_time_ns"] < previous.header["sim_time_ns"]
    )


def simulated_duration_ns(frames: list[CameraFrame]) -> int:
    validate_order(frames)
    return sum(
        0
        if is_simulation_reset(previous, current)
        else current.header["sim_time_ns"] - previous.header["sim_time_ns"]
        for previous, current in zip(frames, frames[1:])
    )


def read_exact(stream: BinaryIO, length: int) -> bytes:
    result = bytearray()
    while len(result) < length:
        chunk = stream.read(length - len(result))
        if not chunk:
            raise ValueError("camera stream closed inside a JPEG payload")
        result.extend(chunk)
    return bytes(result)
