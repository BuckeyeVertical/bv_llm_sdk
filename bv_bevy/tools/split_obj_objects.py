#!/usr/bin/env python3
"""Split a multi-object OBJ into compact, ground-centered OBJ files."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
import re
import shutil


@dataclass
class ObjPart:
    name: str
    records: list[str] = field(default_factory=list)
    vertex_ids: set[int] = field(default_factory=set)
    texcoord_ids: set[int] = field(default_factory=set)
    normal_ids: set[int] = field(default_factory=set)


def safe_name(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
    return cleaned or "object"


def parse_reference(value: str, count: int) -> int:
    index = int(value)
    if index == 0:
        raise ValueError("OBJ indices cannot be zero")
    return index - 1 if index > 0 else count + index


def parse_obj(path: Path) -> tuple[list[str], list[str], list[str], list[ObjPart]]:
    vertices: list[str] = []
    texcoords: list[str] = []
    normals: list[str] = []
    parts: list[ObjPart] = []
    current: ObjPart | None = None

    with path.open(errors="replace") as source:
        for line in source:
            if line.startswith("v "):
                vertices.append(line)
            elif line.startswith("vt "):
                texcoords.append(line)
            elif line.startswith("vn "):
                normals.append(line)
            elif line.startswith("o "):
                current = ObjPart(line.split(None, 1)[1].strip())
                parts.append(current)
            elif current is not None and line.startswith("f "):
                current.records.append(line)
                for token in line.split()[1:]:
                    fields = token.split("/")
                    current.vertex_ids.add(parse_reference(fields[0], len(vertices)))
                    if len(fields) > 1 and fields[1]:
                        current.texcoord_ids.add(
                            parse_reference(fields[1], len(texcoords))
                        )
                    if len(fields) > 2 and fields[2]:
                        current.normal_ids.add(parse_reference(fields[2], len(normals)))
            elif current is not None and line.startswith(("usemtl ", "s ", "g ")):
                current.records.append(line)

    return vertices, texcoords, normals, parts


def remap_face(
    line: str,
    vertex_map: dict[int, int],
    texcoord_map: dict[int, int],
    normal_map: dict[int, int],
    vertex_count: int,
    texcoord_count: int,
    normal_count: int,
) -> str:
    output = []
    for token in line.split()[1:]:
        fields = token.split("/")
        vertex = vertex_map[parse_reference(fields[0], vertex_count)]
        if len(fields) == 1:
            output.append(str(vertex))
            continue

        texcoord = ""
        normal = ""
        if fields[1]:
            texcoord = str(texcoord_map[parse_reference(fields[1], texcoord_count)])
        if len(fields) > 2 and fields[2]:
            normal = str(normal_map[parse_reference(fields[2], normal_count)])
        output.append(f"{vertex}/{texcoord}/{normal}" if normal else f"{vertex}/{texcoord}")
    return "f " + " ".join(output) + "\n"


def centered_vertices(vertices: list[str], ids: list[int]) -> list[str]:
    parsed = [list(map(float, vertices[index].split()[1:4])) for index in ids]
    minimum = [min(vertex[axis] for vertex in parsed) for axis in range(3)]
    maximum = [max(vertex[axis] for vertex in parsed) for axis in range(3)]
    offset = [
        (minimum[0] + maximum[0]) * 0.5,
        minimum[1],
        (minimum[2] + maximum[2]) * 0.5,
    ]
    return [
        f"v {vertex[0] - offset[0]:.7g} {vertex[1] - offset[1]:.7g} "
        f"{vertex[2] - offset[2]:.7g}\n"
        for vertex in parsed
    ]


def write_part(
    output_dir: Path,
    part: ObjPart,
    vertices: list[str],
    texcoords: list[str],
    normals: list[str],
    material_name: str,
) -> Path:
    name = safe_name(part.name)
    vertex_ids = sorted(part.vertex_ids)
    texcoord_ids = sorted(part.texcoord_ids)
    normal_ids = sorted(part.normal_ids)
    vertex_map = {old: new for new, old in enumerate(vertex_ids, 1)}
    texcoord_map = {old: new for new, old in enumerate(texcoord_ids, 1)}
    normal_map = {old: new for new, old in enumerate(normal_ids, 1)}

    lines = [f"mtllib {material_name}\n", f"o {part.name}\n"]
    lines.extend(centered_vertices(vertices, vertex_ids))
    lines.extend(texcoords[index] for index in texcoord_ids)
    lines.extend(normals[index] for index in normal_ids)
    for record in part.records:
        if record.startswith("f "):
            lines.append(
                remap_face(
                    record,
                    vertex_map,
                    texcoord_map,
                    normal_map,
                    len(vertices),
                    len(texcoords),
                    len(normals),
                )
            )
        else:
            lines.append(record)

    output = output_dir / f"{name}.obj"
    output.write_text("".join(lines))
    return output


def material_blocks(text: str) -> dict[str, str]:
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines(keepends=True):
        if line.startswith("newmtl "):
            current = line.split(None, 1)[1].strip()
            blocks[current] = [line]
        elif current is not None:
            blocks[current].append(line)
    return {name: "".join(lines) for name, lines in blocks.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()

    source = args.source.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    vertices, texcoords, normals, parts = parse_obj(source)
    material_source = source.with_suffix(".mtl")
    materials = material_blocks(
        material_source.read_text(errors="replace").replace(
            "Texture\\\\", "textures/"
        )
    )

    texture_source = source.parent / "Texture"
    texture_output = output_dir / "textures"
    texture_output.mkdir(exist_ok=True)
    for texture in texture_source.iterdir():
        if texture.is_file():
            shutil.copy2(texture, texture_output / texture.name)

    for part in parts:
        part_name = safe_name(part.name)
        used_materials = {
            record.split(None, 1)[1].strip()
            for record in part.records
            if record.startswith("usemtl ")
        }
        part_material = output_dir / f"{part_name}.mtl"
        part_material.write_text(
            "".join(materials[name] for name in used_materials)
        )
        output = write_part(
            output_dir,
            part,
            vertices,
            texcoords,
            normals,
            part_material.name,
        )
        print(f"{part.name}: {len(part.records)} records -> {output.name}")


if __name__ == "__main__":
    main()
