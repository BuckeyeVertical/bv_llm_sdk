#!/usr/bin/env python3
"""Convert a foliage FBX with separate TGA opacity maps to one GLB.

This converter targets the common SpeedTree-style layout used by bush-01:
color, opacity, and normal TGA files sit beside the FBX. The opacity image is
merged into the color image's alpha channel and foliage uses alpha masking so
the atlas background does not appear in Bevy.

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


FOLIAGE_TEXTURES = {
    "SampleLeaves_2": {
        "color": "SampleLeaves_2.tga",
        "opacity": "SampleLeaves_2_Alpha.tga",
        "normal": "SampleLeaves_2_Normal.tga",
    },
    "ClippedFrond": {
        "color": "ClippedFrond.tga",
        "opacity": "ClippedFrond_Alpha.tga",
        "normal": "ClippedFrond_Normal.tga",
    },
}


def pad(data: bytes, fill: bytes) -> bytes:
    return data + fill * ((-len(data)) % 4)


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(f"Required tool not found: {name}")


def run_ffmpeg(arguments: list[str]) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *arguments], check=True)


def make_color_with_alpha(color: Path, opacity: Path, output: Path) -> None:
    run_ffmpeg(
        [
            "-i",
            str(color),
            "-i",
            str(opacity),
            "-filter_complex",
            "[0:v]format=rgb24[color];"
            "[1:v]extractplanes=r[alpha];"
            "[color][alpha]alphamerge[out]",
            "-map",
            "[out]",
            "-frames:v",
            "1",
            str(output),
        ]
    )


def convert_normal(normal: Path, output: Path) -> None:
    run_ffmpeg(["-i", str(normal), "-frames:v", "1", str(output)])


def add_texture(
    document: dict, binary: bytearray, image_path: Path, name: str
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
            "mimeType": "image/png",
        }
    )
    texture_index = len(document.setdefault("textures", []))
    document["textures"].append({"sampler": 0, "source": image_index})
    return texture_index


def remove_assimp_extensions(document: dict) -> None:
    document.pop("extensionsUsed", None)
    document.pop("extensionsRequired", None)
    for material in document.get("materials", []):
        material.pop("extensions", None)
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
    center_x = (minimum[0] + maximum[0]) / 2.0
    center_z = (minimum[2] + maximum[2]) / 2.0

    scene = document.get("scene", 0)
    root_index = document["scenes"][scene]["nodes"][0]
    root = document["nodes"][root_index]
    root["name"] = "Bush 01"
    root["translation"] = [-center_x, -minimum[1], -center_z]


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
    expected = [
        texture_dir / filename
        for texture_set in FOLIAGE_TEXTURES.values()
        for filename in texture_set.values()
    ]
    missing = [str(path) for path in expected if not path.is_file()]
    if missing:
        raise SystemExit("Missing foliage textures:\n  " + "\n  ".join(missing))

    with tempfile.TemporaryDirectory(prefix="fbx-foliage-to-glb-") as temp_name:
        temp = Path(temp_name)
        intermediate = temp / "model.gltf"
        subprocess.run(
            ["assimp", "export", str(source), str(intermediate), "-f", "gltf2"],
            check=True,
        )

        document = json.loads(intermediate.read_text())
        buffer_uri = document["buffers"][0]["uri"]
        binary = bytearray((temp / buffer_uri).read_bytes())
        remove_assimp_extensions(document)

        # Discard Assimp's loose TGA references and rebuild the texture list
        # with embedded, web/Bevy-compatible PNG images.
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

        material_textures: dict[str, tuple[int, int]] = {}
        for material_name, texture_set in FOLIAGE_TEXTURES.items():
            color_png = temp / f"{material_name}_color.png"
            normal_png = temp / f"{material_name}_normal.png"
            make_color_with_alpha(
                texture_dir / texture_set["color"],
                texture_dir / texture_set["opacity"],
                color_png,
            )
            convert_normal(texture_dir / texture_set["normal"], normal_png)
            color_index = add_texture(
                document, binary, color_png, f"{material_name} Color + Alpha"
            )
            normal_index = add_texture(
                document, binary, normal_png, f"{material_name} Normal"
            )
            material_textures[material_name] = (color_index, normal_index)

        for material in document.get("materials", []):
            material_name = material.get("name", "")
            material.pop("normalTexture", None)
            material.pop("occlusionTexture", None)
            material.pop("emissiveTexture", None)
            material.pop("alphaMode", None)
            material.pop("alphaCutoff", None)
            pbr = material.setdefault("pbrMetallicRoughness", {})
            pbr.pop("baseColorTexture", None)
            pbr.pop("metallicRoughnessTexture", None)
            pbr["metallicFactor"] = 0.0
            pbr["roughnessFactor"] = 0.8

            texture_indexes = material_textures.get(material_name)
            if texture_indexes is None:
                pbr["baseColorFactor"] = [0.18, 0.10, 0.045, 1.0]
                material["doubleSided"] = False
                continue

            color_index, normal_index = texture_indexes
            pbr["baseColorFactor"] = [1.0, 1.0, 1.0, 1.0]
            pbr["baseColorTexture"] = {"index": color_index}
            material["normalTexture"] = {"index": normal_index}
            material["alphaMode"] = "MASK"
            material["alphaCutoff"] = 0.5
            material["doubleSided"] = True

        center_and_ground(document)
        write_glb(document, binary, output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="source FBX file")
    parser.add_argument("output", type=Path, help="destination GLB file")
    args = parser.parse_args()
    convert(args.source.expanduser().resolve(), args.output.expanduser().resolve())


if __name__ == "__main__":
    main()
