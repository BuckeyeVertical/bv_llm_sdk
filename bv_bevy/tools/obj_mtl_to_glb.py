#!/usr/bin/env python3
"""Convert an OBJ/MTL model with unavailable textures to a clean GLB."""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import zipfile


GLB_MAGIC = 0x46546C67
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942


def pad(data: bytes, fill: bytes) -> bytes:
    return data + fill * ((-len(data)) % 4)


def clean_document(document: dict, scale: float) -> None:
    # The CLO MTL refers to textures on its creator's Windows machine. Assimp
    # carries those broken references into glTF, so retain material colors but
    # remove texture slots that cannot be made self-contained.
    document.pop("images", None)
    document.pop("textures", None)
    document.pop("samplers", None)
    document.pop("extensionsUsed", None)
    document.pop("extensionsRequired", None)

    for material in document.get("materials", []):
        material.pop("extensions", None)
        material.pop("normalTexture", None)
        material.pop("occlusionTexture", None)
        material.pop("emissiveTexture", None)
        material["doubleSided"] = True
        pbr = material.get("pbrMetallicRoughness", {})
        pbr.pop("baseColorTexture", None)
        pbr.pop("metallicRoughnessTexture", None)

    for mesh in document.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            primitive.pop("extensions", None)

    scene = document.get("scene", 0)
    root = document["scenes"][scene]["nodes"][0]
    document["nodes"][root]["name"] = "Polo Shirt Mannequin"
    document["nodes"][root]["scale"] = [scale, scale, scale]


def read_clo_assets(project: Path) -> dict[str, bytes]:
    assets: dict[str, bytes] = {}
    with zipfile.ZipFile(project) as outer:
        nested_names = [
            name
            for name in outer.namelist()
            if name.lower().endswith((".avt", ".zpac"))
        ]
        for nested_name in nested_names:
            nested_data = outer.read(nested_name)
            with zipfile.ZipFile(io.BytesIO(nested_data)) as nested:
                for name in nested.namelist():
                    basename = Path(name).name
                    if basename.lower().endswith((".png", ".jpg", ".jpeg")):
                        assets.setdefault(basename.lower(), nested.read(name))
    return assets


def add_texture(
    document: dict,
    binary: bytearray,
    name: str,
    image_data: bytes,
) -> int:
    while len(binary) % 4:
        binary.append(0)
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
    mime_type = "image/png" if name.lower().endswith(".png") else "image/jpeg"
    document["images"].append(
        {"name": name, "bufferView": view_index, "mimeType": mime_type}
    )
    texture_index = len(document.setdefault("textures", []))
    document["textures"].append({"sampler": 0, "source": image_index})
    return texture_index


def add_clo_textures(
    document: dict,
    binary: bytearray,
    project: Path,
) -> None:
    assets = read_clo_assets(project)
    base_color_map = {
        "PT_FABRIC_FRONT_1726902": "2.3cm_Edit.png",
        "Material1727116": "singstitch01.jpg",
        "Material1727236": "singstitch01.jpg",
        "Material1727362": "stitch02.jpg",
        "Material1727386": "stitch02_304382.jpg",
        "Material20958": "Male_Sneaker_01.jpg",
        "Material20977": "Male_Sneaker_01.jpg",
    }
    normal_map = {
        "FABRIC_1_FRONT_1726865": "Knit_Ponte_Jersey_NRM.jpg",
        "collar_FRONT_1843166": "Rib_2X2_468gsm_NRM.jpg",
    }
    garment_colors_srgb = {
        "FABRIC_1_FRONT_1726865": (0x8B, 0x26, 0x35),
        "collar_FRONT_1843166": (0x69, 0x1B, 0x28),
        "Material670190": (0x69, 0x1B, 0x28),
        "PT_FABRIC_FRONT_1726902": (0x8B, 0x26, 0x35),
        "Material1727116": (0x9D, 0x35, 0x45),
        "Material1727236": (0x9D, 0x35, 0x45),
        "Material1727362": (0x9D, 0x35, 0x45),
        "Material1727386": (0x9D, 0x35, 0x45),
    }

    required_names = set(base_color_map.values()) | set(normal_map.values())
    texture_indexes: dict[str, int] = {}
    document["samplers"] = [
        {
            "magFilter": 9729,
            "minFilter": 9987,
            "wrapS": 10497,
            "wrapT": 10497,
        }
    ]
    for name in sorted(required_names):
        image_data = assets.get(name.lower())
        if image_data is None:
            raise SystemExit(f"Texture not found inside CLO project: {name}")
        texture_indexes[name] = add_texture(document, binary, name, image_data)

    for material in document.get("materials", []):
        material_name = material.get("name")
        pbr = material["pbrMetallicRoughness"]
        if rgb := garment_colors_srgb.get(material_name):
            alpha = pbr.get("baseColorFactor", [1.0, 1.0, 1.0, 1.0])[3]
            pbr["baseColorFactor"] = [
                *(srgb_to_linear(channel / 255.0) for channel in rgb),
                alpha,
            ]
        if texture_name := base_color_map.get(material_name):
            pbr["baseColorTexture"] = {
                "index": texture_indexes[texture_name]
            }
        if texture_name := normal_map.get(material_name):
            material["normalTexture"] = {"index": texture_indexes[texture_name]}


def srgb_to_linear(channel: float) -> float:
    if channel <= 0.04045:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def write_glb(document: dict, binary: bytes, output: Path) -> None:
    document["buffers"] = [{"byteLength": len(binary)}]
    json_data = pad(
        json.dumps(document, separators=(",", ":")).encode("utf-8"), b" "
    )
    bin_data = pad(binary, b"\0")
    total_length = 12 + 8 + len(json_data) + 8 + len(bin_data)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as glb:
        glb.write(struct.pack("<III", GLB_MAGIC, 2, total_length))
        glb.write(struct.pack("<II", len(json_data), JSON_CHUNK))
        glb.write(json_data)
        glb.write(struct.pack("<II", len(bin_data), BIN_CHUNK))
        glb.write(bin_data)


def convert(
    source: Path,
    output: Path,
    scale: float,
    clo_project: Path | None,
) -> None:
    if shutil.which("assimp") is None:
        raise SystemExit("Required tool not found: assimp")
    if not source.is_file():
        raise SystemExit(f"Source model not found: {source}")

    with tempfile.TemporaryDirectory(prefix="obj-mtl-to-glb-") as temp_name:
        temp = Path(temp_name)
        intermediate = temp / "model.gltf"
        subprocess.run(
            ["assimp", "export", str(source), str(intermediate), "-f", "gltf2"],
            check=True,
        )

        document = json.loads(intermediate.read_text())
        buffer_uri = document["buffers"][0]["uri"]
        binary = bytearray((temp / buffer_uri).read_bytes())
        clean_document(document, scale)
        if clo_project is not None:
            add_clo_textures(document, binary, clo_project)
        write_glb(document, binary, output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="source OBJ file")
    parser.add_argument("output", type=Path, help="destination GLB file")
    parser.add_argument(
        "--clo-project",
        type=Path,
        help="optional ZPRJ containing textures referenced by the MTL",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=0.001,
        help="root scale applied during conversion (default: 0.001 for mm to m)",
    )
    args = parser.parse_args()
    clo_project = (
        args.clo_project.expanduser().resolve() if args.clo_project else None
    )
    convert(
        args.source.expanduser().resolve(),
        args.output.expanduser().resolve(),
        args.scale,
        clo_project,
    )


if __name__ == "__main__":
    main()
