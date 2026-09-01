#!/usr/bin/env python3

import argparse
import json
import math
import socket
from dataclasses import dataclass
from typing import Any

from sim_state import read_snapshot, validate_order


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    snapshots: int
    expected_ids: frozenset[str]
    require_motion: bool


class StreamVerifier:
    def __init__(self, config: Config) -> None:
        self.config = config

    def run(self) -> None:
        with socket.create_connection((self.config.host, self.config.port), timeout=5) as connection:
            stream = connection.makefile("rb")
            snapshots = [read_snapshot(stream) for _ in range(self.config.snapshots)]

        validate_order(snapshots)
        self._verify_contents(snapshots)
        self._verify_motion(snapshots)

        first = snapshots[0]
        last = snapshots[-1]
        duration_s = (last["sim_time_ns"] - first["sim_time_ns"]) / 1_000_000_000
        print(
            f"Verified {len(snapshots)} snapshots from stream {first['stream_id']} "
            f"over {duration_s:.3f} simulated seconds"
        )

    def _verify_contents(self, snapshots: list[dict[str, Any]]) -> None:
        for snapshot in snapshots:
            entity_ids = {entity["id"] for entity in snapshot["entities"]}
            missing = self.config.expected_ids - entity_ids
            if missing:
                raise RuntimeError(f"snapshot is missing expected entities: {sorted(missing)}")
            for entity in snapshot["entities"]:
                self._verify_unit_quaternion(entity)

    @staticmethod
    def _verify_unit_quaternion(entity: dict[str, Any]) -> None:
        norm = math.sqrt(sum(value * value for value in entity["orientation_xyzw"]))
        if not math.isclose(norm, 1.0, rel_tol=1e-6, abs_tol=1e-6):
            raise RuntimeError(f"non-unit quaternion for {entity['id']}")

    def _verify_motion(self, snapshots: list[dict[str, Any]]) -> None:
        if not self.config.require_motion:
            return

        first = self._positions_by_id(snapshots[0])
        last = self._positions_by_id(snapshots[-1])
        if all(first[entity_id] == last[entity_id] for entity_id in self.config.expected_ids):
            raise RuntimeError("none of the expected entities moved")

    @staticmethod
    def _positions_by_id(snapshot: dict[str, Any]) -> dict[str, list[float]]:
        return {entity["id"]: entity["position_m"] for entity in snapshot["entities"]}


def parse_args() -> Config:
    parser = argparse.ArgumentParser(description="Verify a BV simulation state stream")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7001)
    parser.add_argument("--snapshots", type=int, default=20)
    parser.add_argument("--expect-id", action="append", default=[])
    parser.add_argument("--require-motion", action="store_true")
    args = parser.parse_args()

    if args.snapshots < 2:
        parser.error("--snapshots must be at least 2")

    return Config(
        host=args.host,
        port=args.port,
        snapshots=args.snapshots,
        expected_ids=frozenset(args.expect_id),
        require_motion=args.require_motion,
    )


if __name__ == "__main__":
    try:
        StreamVerifier(parse_args()).run()
    except (ConnectionError, EOFError, json.JSONDecodeError, OSError, RuntimeError, ValueError) as error:
        raise SystemExit(f"Verification failed: {error}") from error
