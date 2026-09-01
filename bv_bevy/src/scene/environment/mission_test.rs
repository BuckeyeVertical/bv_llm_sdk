use std::f32::consts::FRAC_PI_2;

use bevy::prelude::*;

use crate::sim::gazebo_position_to_bevy;

const FIRST_TARGET: Vec3 = Vec3::new(0.0, 10.0, 0.0);
const TARGET_STEP: Vec3 = Vec3::new(17.6777, 17.6777, 0.0);
const TARGETS: [Target; 2] = [
    Target {
        kind: "mannequin",
        asset: "models/polo_shirt_mannequin_optimized.glb",
        scan_index: 2,
        heading_radians: 0.45,
        tilt_radians: FRAC_PI_2,
        ground_clearance_m: 0.17,
    },
    Target {
        kind: "tent",
        asset: "models/Tent_optimized.glb",
        scan_index: 5,
        heading_radians: 1.35,
        tilt_radians: 0.0,
        ground_clearance_m: 0.0,
    },
];

#[derive(Clone, Copy)]
struct Target {
    kind: &'static str,
    asset: &'static str,
    scan_index: usize,
    heading_radians: f32,
    tilt_radians: f32,
    ground_clearance_m: f32,
}

pub(super) fn spawn(commands: &mut Commands, asset_server: &AssetServer) {
    for target in TARGETS {
        let scene = asset_server.load(GltfAssetLabel::Scene(0).from_asset(target.asset));
        let gazebo_position = scan_position(target.scan_index);

        commands.spawn((
            Name::new(format!("Scan target: {}", target.kind)),
            WorldAssetRoot(scene),
            target_transform(target, gazebo_position),
        ));
    }
}

fn target_transform(target: Target, gazebo_position: Vec3) -> Transform {
    Transform::from_translation(
        gazebo_position_to_bevy(gazebo_position) + Vec3::Y * target.ground_clearance_m,
    )
    .with_rotation(
        Quat::from_rotation_y(target.heading_radians) * Quat::from_rotation_x(target.tilt_radians),
    )
}

fn scan_position(index: usize) -> Vec3 {
    FIRST_TARGET + TARGET_STEP * index as f32
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn targets_are_separated_along_the_mission_test_scan_line() {
        let mannequin = scan_position(TARGETS[0].scan_index);
        let tent = scan_position(TARGETS[1].scan_index);

        assert_eq!(TARGETS.len(), 2);
        assert!(mannequin.abs_diff_eq(Vec3::new(35.3554, 45.3554, 0.0), 1e-4));
        assert!(tent.abs_diff_eq(Vec3::new(88.3885, 98.3885, 0.0), 1e-4));
    }

    #[test]
    fn mannequin_lies_horizontally_above_the_ground() {
        let transform = target_transform(TARGETS[0], scan_position(TARGETS[0].scan_index));
        let model_up = transform.rotation * Vec3::Y;

        assert!(model_up.y.abs() < 1e-5);
        assert!((transform.translation.y - 0.17).abs() < 1e-5);
    }
}
