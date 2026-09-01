#!/usr/bin/env python3

import io
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from replay_sim_state import replay_snapshots, serve_consumer
from sim_state import (
    read_recording,
    read_snapshot,
    validate_order,
    validate_snapshot,
    write_recording,
    write_snapshot,
)


def snapshot(sequence: int, sim_time_ns: int) -> dict:
    return {
        "schema": "bv.sim_state",
        "version": 1,
        "stream_id": "test-stream",
        "sequence": sequence,
        "sim_time_ns": sim_time_ns,
        "frame_id": "gazebo_world",
        "entities": [
            {
                "id": "vehicle/test",
                "kind": "vehicle",
                "position_m": [1.0, 2.0, 3.0],
                "orientation_xyzw": [0.0, 0.0, 0.0, 2.0],
            }
        ],
    }


class SimulationStateTest(unittest.TestCase):
    def test_wire_round_trip_preserves_snapshot(self) -> None:
        expected = snapshot(4, 123_456_789)
        stream = io.BytesIO()

        write_snapshot(stream, expected)
        stream.seek(0)

        self.assertEqual(read_snapshot(stream), expected)

    def test_recording_round_trip_preserves_every_snapshot(self) -> None:
        expected = [snapshot(1, 10), snapshot(2, 30)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flight.bvsim"

            self.assertEqual(write_recording(path, expected), 2)

            self.assertEqual(read_recording(path), expected)

    def test_replay_uses_simulation_time_at_requested_speed(self) -> None:
        snapshots = [
            snapshot(1, 100),
            snapshot(2, 200_000_100),
            snapshot(3, 500_000_100),
        ]
        delays = []
        stream = io.BytesIO()

        replay_snapshots(stream, snapshots, speed=2.0, loop=False, sleep=delays.append)
        stream.seek(0)

        self.assertEqual(delays, [0.1, 0.15])
        self.assertEqual([read_snapshot(stream) for _ in snapshots], snapshots)

    def test_two_consumers_receive_independent_complete_replays(self) -> None:
        expected = [snapshot(1, 10), snapshot(2, 30)]
        consumers = []
        workers = []
        for _ in range(2):
            server, client = socket.socketpair()
            worker = threading.Thread(
                target=serve_consumer,
                args=(server, expected, 1_000_000_000.0, False),
            )
            worker.start()
            consumers.append(client)
            workers.append(worker)

        received = []
        for consumer in consumers:
            with consumer, consumer.makefile("rb") as stream:
                received.append([read_snapshot(stream) for _ in expected])
        for worker in workers:
            worker.join(timeout=1)

        self.assertEqual(received, [expected, expected])
        self.assertTrue(all(not worker.is_alive() for worker in workers))

    def test_rejects_unknown_snapshot_and_entity_fields(self) -> None:
        invalid_snapshot = snapshot(1, 10) | {"extra": True}
        invalid_entity = snapshot(1, 10)
        invalid_entity["entities"][0]["extra"] = True

        with self.assertRaisesRegex(ValueError, "unknown fields"):
            validate_snapshot(invalid_snapshot)
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            validate_snapshot(invalid_entity)

    def test_rejects_duplicate_entity_ids(self) -> None:
        invalid = snapshot(1, 10)
        invalid["entities"].append(dict(invalid["entities"][0]))

        with self.assertRaisesRegex(ValueError, "duplicate entity"):
            validate_snapshot(invalid)

    def test_accepts_non_unit_quaternion_for_receiver_normalization(self) -> None:
        validate_snapshot(snapshot(1, 10))

    def test_rejects_blank_identifiers_and_boolean_version(self) -> None:
        blank = snapshot(1, 10)
        blank["entities"][0]["id"] = "  "
        boolean_version = snapshot(1, 10)
        boolean_version["version"] = True

        with self.assertRaisesRegex(ValueError, "invalid id"):
            validate_snapshot(blank)
        with self.assertRaisesRegex(ValueError, "unsupported"):
            validate_snapshot(boolean_version)

    def test_rejects_non_increasing_recording_order(self) -> None:
        with self.assertRaisesRegex(ValueError, "sequence did not increase"):
            validate_order([snapshot(2, 10), snapshot(2, 20)])
        with self.assertRaisesRegex(ValueError, "time did not increase"):
            validate_order([snapshot(1, 20), snapshot(2, 20)])

    def test_rejects_truncated_snapshot(self) -> None:
        encoded = json.dumps(snapshot(1, 10)).encode()

        with self.assertRaisesRegex(ValueError, "size limit"):
            read_snapshot(io.BytesIO(encoded))


if __name__ == "__main__":
    unittest.main()
