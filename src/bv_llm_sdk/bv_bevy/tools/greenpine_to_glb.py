#!/usr/bin/env python3
"""Convert the GreenPine OBJ into a textured, self-contained GLB.

The source export has no texture assignments, so this explicitly maps a bark
texture to the trunk mesh and the supplied RGBA branch texture to the leaves.
Requires the `assimp` command-line tool.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile


GLB_MAGIC = 0x46546C67
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942


def pad(data: bytes, fill: bytes) -> bytes:
    return data + fill * ((-len(data)) % 4)


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


def clean_document(document: dict) -> None:
    document.pop("extensionsUsed", None)
    document.pop("extensionsRequired", None)
    document.pop("cameras", None)
    for mesh in document.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            primitive.pop("extensions", None)


def center_and_ground(document: dict) -> None:
    bounds = []
    for mesh in document.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            position = primitive.get("attributes", {}).get("POSITION")
            if position is None:
                continue
            accessor = document["accessors"][position]
            if "min" in accessor and "max" in accessor:
                bounds.append((accessor["min"], accessor["max"]))

    if not bounds:
        return
    minimum = [min(item[0][axis] for item in bounds) for axis in range(3)]
    maximum = [max(item[1][axis] for item in bounds) for axis in range(3)]
    translation = [
        -(minimum[0] + maximum[0]) * 0.5,
        -minimum[1],
        -(minimum[2] + maximum[2]) * 0.5,
    ]
    root_index = document["scenes"][document.get("scene", 0)]["nodes"][0]
    root = document["nodes"][root_index]
    root["name"] = "Green Pine"
    root["translation"] = translation


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


def convert(source: Path, bark: Path, leaves: Path, output: Path) -> None:
    if shutil.which("assimp") is None:
        raise SystemExit("Required tool not found: assimp")
    missing = [str(path) for path in (source, bark, leaves) if not path.is_file()]
    if missing:
        raise SystemExit("Missing required files:\n  " + "\n  ".join(missing))

    with tempfile.TemporaryDirectory(prefix="greenpine-to-glb-") as temp_name:
        temp = Path(temp_name)
        intermediate = temp / "tree.gltf"
        subprocess.run(
            [
                "assimp",
                "export",
                str(source),
                str(intermediate),
                "-f",
                "gltf2",
                "-gsn",
            ],
            check=True,
        )

        document = json.loads(intermediate.read_text())
        buffer_uri = document["buffers"][0]["uri"]
        binary = bytearray((temp / buffer_uri).read_bytes())
        clean_document(document)
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

        bark_texture = add_texture(
            document, binary, bark, "Green Pine Bark", "image/jpeg"
        )
        leaf_texture = add_texture(
            document, binary, leaves, "Green Pine Branches", "image/png"
        )
        document["materials"] = [
            {
                "name": "Green Pine Bark",
                "pbrMetallicRoughness": {
                    "baseColorFactor": [1.0, 1.0, 1.0, 1.0],
                    "baseColorTexture": {"index": bark_texture},
                    "metallicFactor": 0.0,
                    "roughnessFactor": 1.0,
                },
            },
            {
                "name": "Green Pine Foliage",
                "pbrMetallicRoughness": {
                    "baseColorFactor": [1.0, 1.0, 1.0, 1.0],
                    "baseColorTexture": {"index": leaf_texture},
                    "metallicFactor": 0.0,
                    "roughnessFactor": 0.85,
                },
                "alphaMode": "MASK",
                "alphaCutoff": 0.45,
                "doubleSided": True,
            },
        ]

        for mesh in document.get("meshes", []):
            material = 1 if mesh.get("name", "").lower() == "leaves" else 0
            for primitive in mesh.get("primitives", []):
                primitive["material"] = material

        center_and_ground(document)
        write_glb(document, binary, output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="source OBJ file")
    parser.add_argument("bark", type=Path, help="bark color texture")
    parser.add_argument("leaves", type=Path, help="RGBA foliage texture")
    parser.add_argument("output", type=Path, help="destination GLB file")
    args = parser.parse_args()
    convert(
        args.source.expanduser().resolve(),
        args.bark.expanduser().resolve(),
        args.leaves.expanduser().resolve(),
        args.output.expanduser().resolve(),
    )


if __name__ == "__main__":
    main()
