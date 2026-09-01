"""Export the Grass Medium Geometry Nodes clump as one compact OBJ.

Run with Blender, for example:
  blender -b source.blend --python tools/blender_grass_to_obj.py -- output.obj
"""

from __future__ import annotations

from pathlib import Path
import sys

import bpy


SOURCE_OBJECT = "grass_medium_01_geometry_nodes"
LOD_COLLECTION = "grass_medium_01_geonodes_LOD2"
POINT_DENSITY = 20.0


def output_path() -> Path:
    try:
        separator = sys.argv.index("--")
        return Path(sys.argv[separator + 1]).expanduser().resolve()
    except (ValueError, IndexError):
        raise SystemExit("Expected an output path after --")


def configure_geometry_nodes(source: bpy.types.Object) -> None:
    modifier = source.modifiers.get("GeometryNodes")
    if modifier is None or modifier.node_group is None:
        raise SystemExit(f"Geometry Nodes modifier not found on {SOURCE_OBJECT}")

    group = modifier.node_group
    lod_collection = bpy.data.collections.get(LOD_COLLECTION)
    if lod_collection is None:
        raise SystemExit(f"LOD collection not found: {LOD_COLLECTION}")

    collection_node = group.nodes.get("Collection Info.001")
    if collection_node is None:
        raise SystemExit("Geometry Nodes collection input not found")
    collection_node.inputs["Collection"].default_value = lod_collection

    distribute_nodes = [
        node
        for node in group.nodes
        if node.bl_idname == "GeometryNodeDistributePointsOnFaces"
    ]
    if not distribute_nodes:
        raise SystemExit("No point-distribution nodes found")
    for node in distribute_nodes:
        density = node.inputs["Density"]
        for link in list(density.links):
            group.links.remove(link)
        density.default_value = POINT_DENSITY


def evaluated_single_mesh(source: bpy.types.Object) -> bpy.types.Object:
    source.update_tag()
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = source.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(
        evaluated,
        preserve_all_data_layers=True,
        depsgraph=depsgraph,
    )
    result = bpy.data.objects.new("grass_medium_01", mesh)
    bpy.context.scene.collection.objects.link(result)
    mesh.transform(source.matrix_world)
    return result


def center_and_ground(obj: bpy.types.Object) -> None:
    if not obj.data.vertices:
        raise SystemExit("Evaluated grass mesh has no vertices")
    positions = [vertex.co for vertex in obj.data.vertices]
    minimum = [min(position[axis] for position in positions) for axis in range(3)]
    maximum = [max(position[axis] for position in positions) for axis in range(3)]
    offset = (
        -(minimum[0] + maximum[0]) * 0.5,
        -(minimum[1] + maximum[1]) * 0.5,
        -minimum[2],
    )
    for vertex in obj.data.vertices:
        vertex.co.x += offset[0]
        vertex.co.y += offset[1]
        vertex.co.z += offset[2]
    obj.data.update()


def export_obj(obj: bpy.types.Object, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.wm.obj_export(
        filepath=str(output),
        check_existing=False,
        export_selected_objects=True,
        apply_modifiers=False,
        apply_transform=True,
        export_uv=True,
        export_normals=True,
        export_colors=False,
        export_materials=False,
        export_triangulated_mesh=True,
        forward_axis="NEGATIVE_Z",
        up_axis="Y",
    )


def main() -> None:
    source = bpy.data.objects.get(SOURCE_OBJECT)
    if source is None:
        raise SystemExit(f"Source object not found: {SOURCE_OBJECT}")
    configure_geometry_nodes(source)
    result = evaluated_single_mesh(source)
    center_and_ground(result)
    export_obj(result, output_path())
    print(
        f"Exported {len(result.data.vertices)} vertices and "
        f"{len(result.data.polygons)} polygons"
    )


if __name__ == "__main__":
    main()
