#!/usr/bin/env python3
"""Convert an FBX and its loose PBR textures into a self-contained GLB.

Expected texture names beside the FBX:
  texture_diff.png, texture_normal.png, texture_occlusion.png,
  texture_rough.png, and texture_metal.png

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


GLB_MAGIC = 0x46546C67
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(f"Required tool not found: {name}")


def pad(data: bytes, fill: bytes) -> bytes:
    return data + fill * ((-len(data)) % 4)


def remove_assimp_extensions(document: dict) -> None:
    document.pop("extensionsUsed", None)
    document.pop("extensionsRequired", None)
    for material in document.get("materials", []):
        material.pop("extensions", None)
    for mesh in document.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            primitive.pop("extensions", None)


def add_embedded_png(document: dict, binary: bytearray, path: Path) -> int:
    while len(binary) % 4:
        binary.append(0)

    image_data = path.read_bytes()
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
        {"bufferView": view_index, "mimeType": "image/png"}
    )
    return image_index


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


def convert(fbx: Path, output: Path, scale: float) -> None:
    texture_dir = fbx.parent
    texture_paths = {
        "base_color": texture_dir / "texture_diff.png",
        "normal": texture_dir / "texture_normal.png",
        "occlusion": texture_dir / "texture_occlusion.png",
        "roughness": texture_dir / "texture_rough.png",
        "metallic": texture_dir / "texture_metal.png",
    }
    missing = [str(path) for path in texture_paths.values() if not path.is_file()]
    if missing:
        raise SystemExit("Missing required textures:\n  " + "\n  ".join(missing))

    require_tool("assimp")
    require_tool("ffmpeg")

    with tempfile.TemporaryDirectory(prefix="fbx-pbr-to-glb-") as temp_name:
        temp = Path(temp_name)
        intermediate = temp / "model.gltf"
        subprocess.run(
            ["assimp", "export", str(fbx), str(intermediate), "-f", "gltf2"],
            check=True,
        )

        orm = temp / "texture_orm.png"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(texture_paths["roughness"]),
                "-i",
                str(texture_paths["metallic"]),
                "-i",
                str(texture_paths["occlusion"]),
                "-filter_complex",
                "[0:v]extractplanes=r[rough];"
                "[1:v]extractplanes=r[metal];"
                "[2:v]extractplanes=r[occ];"
                "[rough][metal][occ]mergeplanes=0x001020:gbrp[orm]",
                "-map",
                "[orm]",
                "-frames:v",
                "1",
                str(orm),
            ],
            check=True,
        )

        document = json.loads(intermediate.read_text())
        buffer_uri = document["buffers"][0]["uri"]
        binary = bytearray((temp / buffer_uri).read_bytes())
        remove_assimp_extensions(document)

        base_image = add_embedded_png(
            document, binary, texture_paths["base_color"]
        )
        normal_image = add_embedded_png(document, binary, texture_paths["normal"])
        orm_image = add_embedded_png(document, binary, orm)

        document["samplers"] = [
            {
                "magFilter": 9729,
                "minFilter": 9987,
                "wrapS": 10497,
                "wrapT": 10497,
            }
        ]
        document["textures"] = [
            {"sampler": 0, "source": base_image},
            {"sampler": 0, "source": normal_image},
            {"sampler": 0, "source": orm_image},
        ]

        material = document["materials"][0]
        material["pbrMetallicRoughness"] = {
            "baseColorFactor": [1.0, 1.0, 1.0, 1.0],
            "baseColorTexture": {"index": 0},
            "metallicFactor": 1.0,
            "roughnessFactor": 1.0,
            "metallicRoughnessTexture": {"index": 2},
        }
        material["normalTexture"] = {"index": 1}
        material["occlusionTexture"] = {"index": 2}
        material["doubleSided"] = True

        if scale != 1.0:
            root_node = document["scenes"][document.get("scene", 0)]["nodes"][0]
            document["nodes"][root_node]["scale"] = [scale, scale, scale]

        write_glb(document, binary, output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fbx", type=Path, help="source FBX file")
    parser.add_argument("output", type=Path, help="destination GLB file")
    parser.add_argument(
        "--scale",
        type=float,
        default=0.01,
        help="root scale applied during conversion (default: 0.01 for cm to m)",
    )
    args = parser.parse_args()
    convert(args.fbx.expanduser().resolve(), args.output.expanduser().resolve(), args.scale)


if __name__ == "__main__":
    main()
