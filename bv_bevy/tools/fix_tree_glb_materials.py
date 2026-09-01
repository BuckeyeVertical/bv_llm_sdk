#!/usr/bin/env python3
"""Correct legacy OBJ tree materials after glTF conversion.

The source atlases already contain baked color and lighting. Multiplying them
by the MTL's old 0.8 diffuse value a second time makes the foliage too dark and
muddy in a PBR renderer, so the GLB uses a neutral white texture multiplier.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from resize_glb_textures import read_glb, write_glb


def fix(path: Path, diffuse_multiplier: float) -> None:
    document, binary = read_glb(path)
    for material in document.get("materials", []):
        pbr = material.setdefault("pbrMetallicRoughness", {})
        pbr["baseColorFactor"] = [
            diffuse_multiplier,
            diffuse_multiplier,
            diffuse_multiplier,
            1.0,
        ]
        pbr["metallicFactor"] = 0.0
        pbr["roughnessFactor"] = 1.0
        material["doubleSided"] = True
    write_glb(document, binary, path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("glbs", type=Path, nargs="+")
    parser.add_argument(
        "--diffuse-multiplier",
        type=float,
        default=1.0,
        help="neutral texture multiplier (default: 1.0)",
    )
    args = parser.parse_args()
    for path in args.glbs:
        fix(path.resolve(), args.diffuse_multiplier)


if __name__ == "__main__":
    main()
