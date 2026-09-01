#!/usr/bin/env python3
"""Convert the Tree1 3DS asset and its legacy textures into one GLB.

The source leaf mask uses the old transparency convention (black is visible,
white is transparent). This converter inverts that mask, merges it into the
leaf color texture, and creates PBR-friendly bark and foliage materials.

Requires the `assimp` and `ffmpeg` command-line tools.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
from itertools import product


GLB_MAGIC = 0x46546C67
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942


def pad(data: bytes, fill: bytes) -> bytes:
    return data + fill * ((-len(data)) % 4)


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(f"Required tool not found: {name}")


def add_texture(
    document: dict,
    binary: bytearray,
    image_path: Path,
    name: str,
    mime_type: str,
) -> int:
    while len(binary) % 4:
        binary.append(0)

    image_data = image_path.read_bytes()
    view_index = len(document.setdefault("bufferViews", []))
    document["bufferViews"].append(
        {
            "buffer": 0,
            "byteOffset": len(binary),
            "byteLength": len(image_data),
        }
    )
    binary.extend(image_data)

    image_index = len(document.setdefault("images", []))
    document["images"].append(
        {
            "name": name,
            "bufferView": view_index,
            "mimeType": mime_type,
        }
    )
    texture_index = len(document.setdefault("textures", []))
    document["textures"].append({"sampler": 0, "source": image_index})
    return texture_index


def make_leaf_texture(color: Path, mask: Path, output: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(color),
            "-i",
            str(mask),
            "-filter_complex",
            "[0:v]format=rgb24[color];"
            "[1:v]negate,format=gray[alpha];"
            "[color][alpha]alphamerge[out]",
            "-map",
            "[out]",
            "-frames:v",
            "1",
            str(output),
        ],
        check=True,
    )


def remove_assimp_extensions(document: dict) -> None:
    document.pop("extensionsUsed", None)
    document.pop("extensionsRequired", None)
    for material in document.get("materials", []):
        material.pop("extensions", None)
    for mesh in document.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            primitive.pop("extensions", None)


def configure_materials(document: dict, bark_texture: int, leaf_texture: int) -> None:
    for material in document.get("materials", []):
        name = material.get("name", "")
        pbr = material.setdefault("pbrMetallicRoughness", {})
        pbr.pop("metallicRoughnessTexture", None)
        pbr["metallicFactor"] = 0.0
        material.pop("normalTexture", None)
        material.pop("occlusionTexture", None)
        material.pop("emissiveTexture", None)
        material.pop("alphaMode", None)
        material.pop("alphaCutoff", None)

        if name.startswith("Rinde_Leaf"):
            pbr["baseColorFactor"] = [1.0, 1.0, 1.0, 1.0]
            pbr["baseColorTexture"] = {"index": leaf_texture}
            pbr["roughnessFactor"] = 0.8
            material["alphaMode"] = "MASK"
            material["alphaCutoff"] = 0.5
            material["doubleSided"] = True
        elif name.startswith("Rinde"):
            pbr["baseColorFactor"] = [1.0, 1.0, 1.0, 1.0]
            pbr["baseColorTexture"] = {"index": bark_texture}
            pbr["roughnessFactor"] = 1.0
            material["doubleSided"] = False
        else:
            pbr.pop("baseColorTexture", None)
            pbr["baseColorFactor"] = [0.5, 0.5, 0.5, 1.0]
            pbr["roughnessFactor"] = 1.0
            material["doubleSided"] = False


def center_and_ground(document: dict) -> None:
    """Center accessor bounds after applying the 3DS root-axis transform."""
    scene = document.get("scene", 0)
    root_index = document["scenes"][scene]["nodes"][0]
    root = document["nodes"][root_index]
    matrix = root.get(
        "matrix",
        [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0,
        ],
    )

    transformed = []
    for mesh in document.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            position = primitive.get("attributes", {}).get("POSITION")
            if position is None:
                continue
            accessor = document["accessors"][position]
            if "min" not in accessor or "max" not in accessor:
                continue
            for corner in product(*zip(accessor["min"], accessor["max"])):
                x, y, z = corner
                transformed.append(
                    [
                        matrix[0] * x + matrix[4] * y + matrix[8] * z,
                        matrix[1] * x + matrix[5] * y + matrix[9] * z,
                        matrix[2] * x + matrix[6] * y + matrix[10] * z,
                    ]
                )

    if not transformed:
        return
    minimum = [min(point[axis] for point in transformed) for axis in range(3)]
    maximum = [max(point[axis] for point in transformed) for axis in range(3)]
    matrix[12] = -(minimum[0] + maximum[0]) * 0.5
    matrix[13] = -minimum[1]
    matrix[14] = -(minimum[2] + maximum[2]) * 0.5
    root["name"] = "Tree 1"
    root["matrix"] = matrix


def write_glb(document: dict, binary: bytearray, output: Path) -> None:
    document["buffers"] = [{"byteLength": len(binary)}]
    json_data = pad(
        json.dumps(document, separators=(",", ":")).encode("utf-8"), b" "
    )
    bin_data = pad(bytes(binary), b"\0")
    total_length = 12 + 8 + len(json_data) + 8 + len(bin_data)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as glb:
        glb.write(struct.pack("<III", GLB_MAGIC, 2, total_length))
        glb.write(struct.pack("<II", len(json_data), JSON_CHUNK))
        glb.write(json_data)
        glb.write(struct.pack("<II", len(bin_data), BIN_CHUNK))
        glb.write(bin_data)


def convert(source: Path, output: Path) -> None:
    require_tool("assimp")
    require_tool("ffmpeg")
    if not source.is_file():
        raise SystemExit(f"Source model not found: {source}")

    texture_dir = source.parent
    bark = texture_dir / "bark_loo.jpg"
    leaf_color = texture_dir / "blatt1.jpg"
    leaf_mask = texture_dir / "blatt1_a.jpg"
    missing = [str(path) for path in (bark, leaf_color, leaf_mask) if not path.is_file()]
    if missing:
        raise SystemExit("Missing required textures:\n  " + "\n  ".join(missing))

    with tempfile.TemporaryDirectory(prefix="3ds-tree-to-glb-") as temp_name:
        temp = Path(temp_name)
        intermediate = temp / "tree.gltf"
        subprocess.run(
            ["assimp", "export", str(source), str(intermediate), "-f", "gltf2"],
            check=True,
        )

        document = json.loads(intermediate.read_text())
        buffer_uri = document["buffers"][0]["uri"]
        binary = bytearray((temp / buffer_uri).read_bytes())
        remove_assimp_extensions(document)

        document["images"] = []
        document["textures"] = []
        document["samplers"] = [
            {
                "magFilter": 9729,
                "minFilter": 9987,
                "wrapS": 10497,
                "wrapT": 10497,
            }
        ]

        leaf_rgba = temp / "leaf_color_alpha.png"
        make_leaf_texture(leaf_color, leaf_mask, leaf_rgba)
        bark_texture = add_texture(
            document, binary, bark, "Tree Bark", "image/jpeg"
        )
        leaf_texture = add_texture(
            document, binary, leaf_rgba, "Leaves Color + Alpha", "image/png"
        )
        configure_materials(document, bark_texture, leaf_texture)
        center_and_ground(document)
        write_glb(document, binary, output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="source 3DS file")
    parser.add_argument("output", type=Path, help="destination GLB file")
    args = parser.parse_args()
    convert(args.source.expanduser().resolve(), args.output.expanduser().resolve())


if __name__ == "__main__":
    main()
