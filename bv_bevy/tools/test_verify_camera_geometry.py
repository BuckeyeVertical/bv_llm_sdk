#!/usr/bin/env python3

import unittest

from verify_camera_geometry import (
    StateHistory,
    magenta_centroid,
    project_target,
    rotate_vector,
)
from camera_stream import PinholeIntrinsics


class GeometryVerifierTest(unittest.TestCase):
    def test_state_history_distinguishes_reused_sequences_by_stream(self) -> None:
        history = StateHistory("127.0.0.1", 1)
        first = {"stream_id": "first", "sequence": 7}
        second = {"stream_id": "second", "sequence": 7}
        history._store(first)
        history._store(second)

        self.assertIs(history.wait_for("first", 7), first)
        self.assertIs(history.wait_for("second", 7), second)
        self.assertIs(history.wait_for(None, 7), second)

    def test_projects_camera_forward_to_image_center(self) -> None:
        camera = {
            "position_m": [1.0, 2.0, 3.0],
            "orientation_xyzw": [0.0, 0.0, 0.0, 1.0],
        }

        pixel = project_target(camera, (6.0, 2.0, 3.0), 1280, 720, 90.0)

        self.assertAlmostEqual(pixel[0], 640.0)
        self.assertAlmostEqual(pixel[1], 360.0)

    def test_projects_gazebo_left_to_image_left(self) -> None:
        camera = {
            "position_m": [0.0, 0.0, 0.0],
            "orientation_xyzw": [0.0, 0.0, 0.0, 1.0],
        }

        pixel = project_target(camera, (4.0, 1.0, 0.0), 800, 600, 90.0)

        self.assertAlmostEqual(pixel[0], 300.0)
        self.assertAlmostEqual(pixel[1], 300.0)

    def test_uses_frame_intrinsics_for_asymmetric_projection(self) -> None:
        camera = {
            "position_m": [0.0, 0.0, 0.0],
            "orientation_xyzw": [0.0, 0.0, 0.0, 1.0],
        }
        intrinsics = PinholeIntrinsics(fx=400.0, fy=500.0, cx=620.0, cy=350.0)

        pixel = project_target(
            camera,
            (4.0, 1.0, 1.0),
            1280,
            720,
            90.0,
            intrinsics,
        )

        self.assertAlmostEqual(pixel[0], 520.0)
        self.assertAlmostEqual(pixel[1], 225.0)

    def test_rotates_vector_with_quaternion(self) -> None:
        half_sqrt_two = 2**-0.5

        rotated = rotate_vector(
            (0.0, 0.0, half_sqrt_two, half_sqrt_two), (1.0, 0.0, 0.0)
        )

        self.assertAlmostEqual(rotated[0], 0.0)
        self.assertAlmostEqual(rotated[1], 1.0)
        self.assertAlmostEqual(rotated[2], 0.0)

    def test_finds_magenta_centroid(self) -> None:
        pixels = bytes(
            [
                0,
                0,
                0,
                240,
                10,
                200,
                0,
                0,
                0,
                0,
                0,
                0,
                240,
                10,
                200,
            ]
            * 10
        )

        centroid = magenta_centroid(pixels, 5, 10)

        self.assertAlmostEqual(centroid[0], 2.5)
        self.assertAlmostEqual(centroid[1], 4.5)


if __name__ == "__main__":
    unittest.main()
