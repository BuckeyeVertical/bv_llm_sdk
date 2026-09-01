mod bv_mission;
mod mission_test;
mod suas;
mod suas_layout;

use bevy::image::{
    ImageAddressMode, ImageFilterMode, ImageLoaderSettings, ImageSampler, ImageSamplerDescriptor,
};
use bevy::math::Affine2;
use bevy::prelude::*;

const MINIMAL_GROUND_SIZE: f32 = 100.0;
const MISSION_TEST_GROUND_SIZE: f32 = 320.0;
const GRID_EXTENT: i32 = 20;
const GRID_SPACING: f32 = 2.0;
const GRASS_TILE_SIZE_METERS: f32 = 2.0;

pub(super) fn spawn(
    commands: &mut Commands,
    asset_server: &AssetServer,
    meshes: &mut Assets<Mesh>,
    materials: &mut Assets<StandardMaterial>,
) {
    match std::env::var("BV_WORLD_PROFILE").as_deref() {
        Ok("minimal") => {
            spawn_ground(
                commands,
                asset_server,
                meshes,
                materials,
                MINIMAL_GROUND_SIZE,
            );
            spawn_grid(commands, meshes, materials);
            spawn_minimal(commands, meshes, materials);
        }
        Ok("bv_mission") => {
            spawn_ground(
                commands,
                asset_server,
                meshes,
                materials,
                MINIMAL_GROUND_SIZE,
            );
            bv_mission::spawn(commands, meshes, materials);
        }
        Ok("missionTest") | Err(std::env::VarError::NotPresent) => {
            spawn_ground(
                commands,
                asset_server,
                meshes,
                materials,
                MISSION_TEST_GROUND_SIZE,
            );
            mission_test::spawn(commands, asset_server);
        }
        Ok("SUAS") | Ok("suas") => {
            suas::spawn(commands, asset_server, meshes, materials);
        }
        Ok(profile) => panic!("unsupported BV_WORLD_PROFILE: {profile}"),
        Err(error) => panic!("cannot read BV_WORLD_PROFILE: {error}"),
    }
}

fn spawn_ground(
    commands: &mut Commands,
    asset_server: &AssetServer,
    meshes: &mut Assets<Mesh>,
    materials: &mut Assets<StandardMaterial>,
    size: f32,
) {
    spawn_ground_rect(commands, asset_server, meshes, materials, Vec2::splat(size));
}

fn spawn_ground_rect(
    commands: &mut Commands,
    asset_server: &AssetServer,
    meshes: &mut Assets<Mesh>,
    materials: &mut Assets<StandardMaterial>,
    dimensions: Vec2,
) {
    let base_color = load_repeating_texture(asset_server, "textures/grass004/color.jpg", true);
    let normal = load_repeating_texture(asset_server, "textures/grass004/normal_gl.jpg", false);
    let roughness = load_repeating_texture(asset_server, "textures/grass004/roughness.jpg", false);
    let ambient_occlusion = load_repeating_texture(
        asset_server,
        "textures/grass004/ambient_occlusion.jpg",
        false,
    );
    let ground = materials.add(StandardMaterial {
        base_color: Color::WHITE,
        base_color_texture: Some(base_color),
        normal_map_texture: Some(normal),
        metallic_roughness_texture: Some(roughness),
        occlusion_texture: Some(ambient_occlusion),
        perceptual_roughness: 1.0,
        metallic: 0.0,
        reflectance: 0.3,
        uv_transform: Affine2::from_scale(dimensions / GRASS_TILE_SIZE_METERS),
        ..default()
    });
    let ground_mesh = Plane3d::default()
        .mesh()
        .size(dimensions.x, dimensions.y)
        .build()
        .with_generated_tangents()
        .expect("the ground plane has valid positions, normals, and UVs");
    commands.spawn((
        Name::new("Ground"),
        Mesh3d(meshes.add(ground_mesh)),
        MeshMaterial3d(ground),
    ));
}

fn load_repeating_texture(
    asset_server: &AssetServer,
    path: &'static str,
    is_srgb: bool,
) -> Handle<Image> {
    asset_server
        .load_builder()
        .with_settings(move |settings: &mut ImageLoaderSettings| {
            settings.is_srgb = is_srgb;
            settings.sampler = ImageSampler::Descriptor(ImageSamplerDescriptor {
                address_mode_u: ImageAddressMode::Repeat,
                address_mode_v: ImageAddressMode::Repeat,
                mag_filter: ImageFilterMode::Linear,
                min_filter: ImageFilterMode::Linear,
                ..default()
            });
        })
        .load(path)
}

fn spawn_grid(
    commands: &mut Commands,
    meshes: &mut Assets<Mesh>,
    materials: &mut Assets<StandardMaterial>,
) {
    let grid = materials.add(Color::srgba(0.72, 0.76, 0.65, 0.42));
    let line_length = GRID_EXTENT as f32 * GRID_SPACING * 2.0;
    let line_mesh_x = meshes.add(Cuboid::new(line_length, 0.008, 0.018));
    let line_mesh_z = meshes.add(Cuboid::new(0.018, 0.008, line_length));

    for index in -GRID_EXTENT..=GRID_EXTENT {
        let offset = index as f32 * GRID_SPACING;
        commands.spawn((
            Mesh3d(line_mesh_x.clone()),
            MeshMaterial3d(grid.clone()),
            Transform::from_xyz(0.0, 0.006, offset),
        ));
        commands.spawn((
            Mesh3d(line_mesh_z.clone()),
            MeshMaterial3d(grid.clone()),
            Transform::from_xyz(offset, 0.006, 0.0),
        ));
    }
}

fn spawn_minimal(
    commands: &mut Commands,
    meshes: &mut Assets<Mesh>,
    materials: &mut Assets<StandardMaterial>,
) {
    let pad = materials.add(Color::srgb(0.18, 0.2, 0.22));
    let obstacle = materials.add(Color::srgb(0.65, 0.29, 0.12));

    commands.spawn((
        Name::new("Landing pad"),
        Mesh3d(meshes.add(Cylinder::new(2.0, 0.04))),
        MeshMaterial3d(pad),
        Transform::from_xyz(0.0, 0.02, 0.0),
    ));

    let obstacle_mesh = meshes.add(Cuboid::new(1.5, 3.0, 1.5));
    for position in [
        Vec3::new(-8.0, 1.5, -8.0),
        Vec3::new(9.0, 1.5, -5.0),
        Vec3::new(-6.0, 1.5, 10.0),
    ] {
        commands.spawn((
            Mesh3d(obstacle_mesh.clone()),
            MeshMaterial3d(obstacle.clone()),
            Transform::from_translation(position),
        ));
    }
}
