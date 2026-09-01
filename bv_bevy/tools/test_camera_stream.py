#!/usr/bin/env python3

import io
import tempfile
import unittest
from pathlib import Path

from camera_stream import (
    CameraFrame,
    read_frame,
    read_recording,
    simulated_duration_ns,
    validate_order,
    write_frame,
    write_recording,
)
from replay_camera_stream import replay_frames


JPEG = b"\xff\xd8payload\xff\xd9"


def frame(
    sequence: int,
    sim_time_ns: int,
    *,
    sim_sequence: int | None = None,
    sim_stream_id: str | None = "test-stream",
    include_intrinsics: bool = True,
) -> CameraFrame:
    header = {
        "schema": "bv.camera_frame",
        "version": 1,
        "sequence": sequence,
        "sim_sequence": sim_sequence if sim_sequence is not None else sequence + 100,
        "sim_time_ns": sim_time_ns,
        "camera_id": "camera/test",
        "width": 2,
        "height": 1,
        "encoding": "jpeg",
        "data_length": len(JPEG),
    }
    if sim_stream_id is not None:
        header["sim_stream_id"] = sim_stream_id
    if include_intrinsics:
        header.update(
            {
                "camera_model": "pinhole",
                "fx": 1.0,
                "fy": 1.0,
                "cx": 1.0,
                "cy": 0.5,
            }
        )
    return CameraFrame(header, JPEG)


class CameraStreamTest(unittest.TestCase):
    def test_wire_round_trip_preserves_metadata_and_payload(self) -> None:
        source = frame(4, 123_456_789)
        stream = io.BytesIO()

        write_frame(stream, source)
        stream.seek(0)

        self.assertEqual(read_frame(stream), source)

    def test_recording_round_trip_preserves_every_frame(self) -> None:
        expected = [frame(1, 10), frame(2, 30)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flight.bvcam"

            self.assertEqual(write_recording(path, expected), 2)

            self.assertEqual(read_recording(path), expected)

    def test_replay_uses_simulation_time_at_requested_speed(self) -> None:
        frames = [frame(1, 100), frame(2, 200_000_100), frame(3, 500_000_100)]
        delays = []
        stream = io.BytesIO()

        replay_frames(stream, frames, speed=2.0, loop=False, sleep=delays.append)
        stream.seek(0)

        self.assertEqual(delays, [0.1, 0.15])
        self.assertEqual([read_frame(stream) for _ in frames], frames)

    def test_recording_and_replay_allow_simulation_reset(self) -> None:
        frames = [
            frame(1, 100, sim_sequence=500),
            frame(2, 200_000_100, sim_sequence=501),
            frame(3, 50, sim_sequence=1),
            frame(4, 100_000_050, sim_sequence=2),
        ]
        delays = []
        stream = io.BytesIO()

        validate_order(frames)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reset.bvcam"
            write_recording(path, frames)
            self.assertEqual(read_recording(path), frames)
        replay_frames(stream, frames, speed=1.0, loop=False, sleep=delays.append)

        self.assertEqual(delays, [0.2, 0.1])
        self.assertEqual(simulated_duration_ns(frames), 300_000_000)

    def test_new_simulation_stream_allows_sequence_and_time_reset(self) -> None:
        frames = [
            frame(1, 1_000, sim_sequence=500, sim_stream_id="first"),
            frame(2, 10, sim_sequence=1, sim_stream_id="second"),
        ]

        validate_order(frames)

    def test_camera_recording_loop_starts_a_new_epoch(self) -> None:
        recorded = [
            frame(10, 1_000, sim_sequence=500),
            frame(11, 2_000, sim_sequence=501),
        ]

        validate_order(recorded + recorded)

    def test_rejects_inconsistent_simulation_order(self) -> None:
        frames = [
            frame(1, 1_000, sim_sequence=500),
            frame(2, 2_000, sim_sequence=499),
        ]

        with self.assertRaisesRegex(ValueError, "simulation sequence"):
            validate_order(frames)

    def test_accepts_legacy_header_without_simulation_stream_id(self) -> None:
        legacy = frame(1, 10, sim_stream_id=None)
        stream = io.BytesIO()

        write_frame(stream, legacy)
        stream.seek(0)

        self.assertEqual(read_frame(stream), legacy)

    def test_accepts_legacy_header_without_intrinsics(self) -> None:
        legacy = frame(1, 10, include_intrinsics=False)
        stream = io.BytesIO()

        write_frame(stream, legacy)
        stream.seek(0)

        self.assertEqual(read_frame(stream), legacy)

    def test_rejects_incomplete_or_invalid_intrinsics(self) -> None:
        incomplete = frame(1, 10, include_intrinsics=False)
        incomplete.header["camera_model"] = "pinhole"
        invalid = frame(1, 10)
        invalid.header["fx"] = float("nan")

        with self.assertRaisesRegex(ValueError, "incomplete intrinsics"):
            write_frame(io.BytesIO(), incomplete)
        with self.assertRaisesRegex(ValueError, "invalid fx"):
            write_frame(io.BytesIO(), invalid)

    def test_rejects_blank_simulation_stream_id(self) -> None:
        invalid = frame(1, 10)
        invalid.header["sim_stream_id"] = "  "

        with self.assertRaisesRegex(ValueError, "sim_stream_id"):
            write_frame(io.BytesIO(), invalid)

    def test_rejects_truncated_payload(self) -> None:
        stream = io.BytesIO()
        write_frame(stream, frame(1, 10))
        stream.seek(0)

        with self.assertRaisesRegex(ValueError, "inside a JPEG payload"):
            read_frame(io.BytesIO(stream.read()[:-1]))


if __name__ == "__main__":
    unittest.main()
