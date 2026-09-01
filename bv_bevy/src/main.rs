use bevy::{
    camera::{PerspectiveProjection, Projection, visibility::RenderLayers},
    prelude::*,
};
use bv_bevy::camera::{CameraFrameServerPlugin, DebugCamera, DebugCameraPlugin, DroneCameraPlugin};
use bv_bevy::scene::{SimulationScenePlugin, VEHICLE_RENDER_LAYER, WORLD_DEBUG_RENDER_LAYER};
use bv_bevy::sim::SimReceiverPlugin;

fn main() {
    App::new()
        .add_plugins(DefaultPlugins)
        .add_plugins(DebugCameraPlugin)
        .add_plugins(DroneCameraPlugin::from_env())
        .add_plugins(CameraFrameServerPlugin::from_env())
        .add_plugins(SimReceiverPlugin::from_env())
        .add_plugins(SimulationScenePlugin)
        .add_systems(Startup, spawn_debug_camera)
        .run();
}

fn spawn_debug_camera(mut commands: Commands) {
    commands.spawn((
        Camera3d::default(),
        Projection::Perspective(PerspectiveProjection {
            far: 3_000.0,
            ..default()
        }),
        DebugCamera::default(),
        RenderLayers::from_layers(&[0, VEHICLE_RENDER_LAYER, WORLD_DEBUG_RENDER_LAYER]),
        initial_debug_camera_transform(),
    ));
}

fn initial_debug_camera_transform() -> Transform {
    if matches!(
        std::env::var("BV_WORLD_PROFILE").as_deref(),
        Ok("SUAS") | Ok("suas")
    ) {
        let center = Vec3::new(-45.0, 0.0, -175.0);
        Transform::from_xyz(center.x, 1_250.0, center.z).looking_at(center, Vec3::NEG_Z)
    } else {
        Transform::from_xyz(6.0, 5.0, 8.0).looking_at(Vec3::ZERO, Vec3::Y)
    }
}
