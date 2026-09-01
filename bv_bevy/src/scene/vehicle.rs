use bevy::{camera::visibility::RenderLayers, prelude::*};

use super::VEHICLE_RENDER_LAYER;

const DRONE_SCALE: f32 = 0.001;
const DRONE_CENTER_X: f32 = 0.626_729;
const DRONE_CENTER_Y: f32 = 0.237_018;
const DRONE_CENTER_Z: f32 = 0.626_644;

#[derive(Component)]
pub(super) struct VehicleVisual;

pub(super) fn spawn(parent: &mut ChildSpawnerCommands, asset_server: &AssetServer) {
    let scene =
        asset_server.load(GltfAssetLabel::Scene(0).from_asset("models/Drone_optimized.glb"));

    parent.spawn((
        Name::new("Buckeye Vertical drone visual"),
        VehicleVisual,
        WorldAssetRoot(scene),
        Transform::from_translation(Vec3::new(-DRONE_CENTER_X, -DRONE_CENTER_Y, -DRONE_CENTER_Z))
            .with_scale(Vec3::splat(DRONE_SCALE)),
    ));
}

pub(super) fn apply_render_layer(
    mut commands: Commands,
    added_meshes: Query<(Entity, &ChildOf), Added<Mesh3d>>,
    parents: Query<&ChildOf>,
    visual_roots: Query<(), With<VehicleVisual>>,
) {
    for (mesh, parent) in &added_meshes {
        let mut ancestor = parent.parent();

        loop {
            if visual_roots.contains(ancestor) {
                commands
                    .entity(mesh)
                    .insert(RenderLayers::layer(VEHICLE_RENDER_LAYER));
                break;
            }
            let Ok(parent) = parents.get(ancestor) else {
                break;
            };
            ancestor = parent.parent();
        }
    }
}
