import json
import math
from pathlib import Path
from typing import Any, BinaryIO, Iterable


SCHEMA = "bv.sim_state"
VERSION = 1
FRAME_ID = "gazebo_world"
RECORDING_MAGIC = b"BVSIM\x00\x01\n"
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
MAX_ENTITIES = 10_000
MAX_U64 = (1 << 64) - 1

SNAPSHOT_FIELDS = {
    "schema",
    "version",
    "stream_id",
    "sequence",
    "sim_time_ns",
    "frame_id",
    "entities",
}
ENTITY_FIELDS = {
    "id",
    "kind",
    "position_m",
    "orientation_xyzw",
}

Snapshot = dict[str, Any]


def read_snapshot(stream: BinaryIO) -> Snapshot:
    line = stream.readline(MAX_SNAPSHOT_BYTES + 1)
    if not line:
        raise EOFError("simulation state stream closed")
    if len(line) > MAX_SNAPSHOT_BYTES or not line.endswith(b"\n"):
        raise ValueError("simulation state snapshot exceeds size limit")

    snapshot = json.loads(line)
    validate_snapshot(snapshot)
    return snapshot


def write_snapshot(stream: BinaryIO, snapshot: Snapshot) -> None:
    validate_snapshot(snapshot)
    encoded = json.dumps(snapshot, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) + 1 > MAX_SNAPSHOT_BYTES:
        raise ValueError("simulation state snapshot exceeds size limit")
    stream.write(encoded)
    stream.write(b"\n")


def read_recording(path: Path) -> list[Snapshot]:
    with path.open("rb") as stream:
        if stream.read(len(RECORDING_MAGIC)) != RECORDING_MAGIC:
            raise ValueError("unsupported simulation state recording format")
        snapshots = list(iter_snapshots(stream))
    if not snapshots:
        raise ValueError("simulation state recording contains no snapshots")
    validate_order(snapshots)
    return snapshots


def write_recording(path: Path, snapshots: Iterable[Snapshot]) -> int:
    captured = list(snapshots)
    for snapshot in captured:
        validate_snapshot(snapshot)
    validate_order(captured)
    with path.open("wb") as stream:
        stream.write(RECORDING_MAGIC)
        for snapshot in captured:
            write_snapshot(stream, snapshot)
    return len(captured)


def iter_snapshots(stream: BinaryIO) -> Iterable[Snapshot]:
    while True:
        try:
            yield read_snapshot(stream)
        except EOFError:
            return


def validate_snapshot(snapshot: Any) -> None:
    if not isinstance(snapshot, dict):
        raise ValueError("simulation state snapshot is not an object")
    _require_exact_fields(snapshot, SNAPSHOT_FIELDS, "snapshot")
    if snapshot["schema"] != SCHEMA or (
        not isinstance(snapshot["version"], int)
        or isinstance(snapshot["version"], bool)
        or snapshot["version"] != VERSION
    ):
        raise ValueError("unsupported simulation state schema")
    if not isinstance(snapshot["stream_id"], str) or not snapshot["stream_id"].strip():
        raise ValueError("simulation state has invalid stream_id")
    _validate_u64(snapshot["sequence"], "sequence")
    _validate_u64(snapshot["sim_time_ns"], "sim_time_ns")
    if snapshot["frame_id"] != FRAME_ID:
        raise ValueError("simulation state is not in Gazebo world coordinates")

    entities = snapshot["entities"]
    if not isinstance(entities, list) or len(entities) > MAX_ENTITIES:
        raise ValueError("simulation state has invalid entities")
    identifiers = set()
    for entity in entities:
        _validate_entity(entity)
        if entity["id"] in identifiers:
            raise ValueError(f"simulation state has duplicate entity '{entity['id']}'")
        identifiers.add(entity["id"])


def validate_order(snapshots: list[Snapshot]) -> None:
    if not snapshots:
        raise ValueError("simulation state sequence is empty")
    stream_id = snapshots[0]["stream_id"]
    for previous, current in zip(snapshots, snapshots[1:]):
        if current["stream_id"] != stream_id:
            raise ValueError("simulation state stream changed")
        if current["sequence"] <= previous["sequence"]:
            raise ValueError("simulation state sequence did not increase")
        if current["sim_time_ns"] <= previous["sim_time_ns"]:
            raise ValueError("simulation state time did not increase")


def _validate_entity(entity: Any) -> None:
    if not isinstance(entity, dict):
        raise ValueError("simulation state entity is not an object")
    _require_exact_fields(entity, ENTITY_FIELDS, "entity")
    for field in ("id", "kind"):
        if not isinstance(entity[field], str) or not entity[field].strip():
            raise ValueError(f"simulation state entity has invalid {field}")
    _validate_vector(entity["position_m"], 3, "position_m")
    orientation = _validate_vector(entity["orientation_xyzw"], 4, "orientation_xyzw")
    if sum(value * value for value in orientation) < 1e-12:
        raise ValueError(f"entity '{entity['id']}' has a zero-length quaternion")


def _validate_vector(value: Any, length: int, field: str) -> list[float]:
    if not isinstance(value, list) or len(value) != length:
        raise ValueError(f"simulation state entity has invalid {field}")
    if not all(
        isinstance(component, (int, float))
        and not isinstance(component, bool)
        and math.isfinite(component)
        for component in value
    ):
        raise ValueError(f"simulation state entity has non-finite {field}")
    return value


def _validate_u64(value: Any, field: str) -> None:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= MAX_U64
    ):
        raise ValueError(f"simulation state has invalid {field}")


def _require_exact_fields(value: dict[str, Any], expected: set[str], name: str) -> None:
    missing = expected - value.keys()
    unknown = value.keys() - expected
    if missing:
        raise ValueError(f"simulation state {name} is missing {sorted(missing)}")
    if unknown:
        raise ValueError(f"simulation state {name} has unknown fields {sorted(unknown)}")
