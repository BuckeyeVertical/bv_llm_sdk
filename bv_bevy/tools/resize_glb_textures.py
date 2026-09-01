#!/usr/bin/env python3
"""Resize embedded PNG textures in a GLB without adding glTF extensions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct
import subprocess
import tempfile


GLB_MAGIC = 0x46546C67
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942


def pad(data: bytes, fill: bytes) -> bytes:
    return data + fill * ((-len(data)) % 4)


def read_glb(path: Path) -> tuple[dict, bytes]:
    with path.open("rb") as source:
        magic, version, _ = struct.unpack("<III", source.read(12))
        if magic != GLB_MAGIC or version != 2:
            raise SystemExit(f"Not a glTF 2.0 binary file: {path}")
        json_length, json_type = struct.unpack("<II", source.read(8))
        if json_type != JSON_CHUNK:
            raise SystemExit("GLB JSON chunk is missing")
        document = json.loads(source.read(json_length))
        bin_length, bin_type = struct.unpack("<II", source.read(8))
        if bin_type != BIN_CHUNK:
            raise SystemExit("GLB binary chunk is missing")
        binary = source.read(bin_length)
    return document, binary


def resize_png(data: bytes, maximum: int, temp: Path, index: int) -> bytes:
    source = temp / f"image-{index}-source.png"
    output = temp / f"image-{index}-resized.png"
    source.write_bytes(data)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-vf",
            f"scale={maximum}:{maximum}:flags=lanczos",
            "-frames:v",
            "1",
            str(output),
        ],
        check=True,
    )
    return output.read_bytes()


def rebuild_binary(document: dict, binary: bytes, maximum: int) -> bytes:
    image_views = {
        image["bufferView"]: (index, image)
        for index, image in enumerate(document.get("images", []))
        if "bufferView" in image
    }
    rebuilt = bytearray()

    with tempfile.TemporaryDirectory(prefix="resize-glb-textures-") as temp_name:
        temp = Path(temp_name)
        for view_index, view in enumerate(document.get("bufferViews", [])):
            offset = view.get("byteOffset", 0)
            data = binary[offset : offset + view["byteLength"]]
            image_entry = image_views.get(view_index)
            if image_entry is not None:
                image_index, image = image_entry
                if image.get("mimeType") != "image/png":
                    raise SystemExit(
                        f"Embedded image {image_index} is not PNG; cannot resize"
                    )
                data = resize_png(data, maximum, temp, image_index)

            while len(rebuilt) % 4:
                rebuilt.append(0)
            view["byteOffset"] = len(rebuilt)
            view["byteLength"] = len(data)
            rebuilt.extend(data)

    document["buffers"] = [{"byteLength": len(rebuilt)}]
    return bytes(rebuilt)


def write_glb(document: dict, binary: bytes, output: Path) -> None:
    json_data = pad(
        json.dumps(document, separators=(",", ":")).encode("utf-8"), b" "
    )
    bin_data = pad(binary, b"\0")
    total_length = 12 + 8 + len(json_data) + 8 + len(bin_data)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as target:
        target.write(struct.pack("<III", GLB_MAGIC, 2, total_length))
        target.write(struct.pack("<II", len(json_data), JSON_CHUNK))
        target.write(json_data)
        target.write(struct.pack("<II", len(bin_data), BIN_CHUNK))
        target.write(bin_data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--maximum", type=int, default=2048)
    args = parser.parse_args()

    document, binary = read_glb(args.source)
    binary = rebuild_binary(document, binary, args.maximum)
    write_glb(document, binary, args.output)


if __name__ == "__main__":
    main()
