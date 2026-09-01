use bevy::{math::Mat3, prelude::*};

/// Convert a Gazebo position into Bevy's basis: `(x, y, z)` becomes `(-y, z, -x)`.
pub fn gazebo_position_to_bevy(gazebo: Vec3) -> Vec3 {
    Vec3::new(-gazebo.y, gazebo.z, -gazebo.x)
}

/// Convert a direction from Gazebo's world basis to Bevy's world basis.
pub fn gazebo_direction_to_bevy(gazebo: Vec3) -> Vec3 {
    gazebo_position_to_bevy(gazebo)
}

/// Convert an orientation by changing both its world and local coordinate
/// bases. Quaternion components are never reordered directly.
pub fn gazebo_rotation_to_bevy(gazebo: Quat) -> Quat {
    let basis = gazebo_to_bevy_basis();
    let gazebo_rotation = Mat3::from_quat(gazebo.normalize());
    let bevy_rotation = basis * gazebo_rotation * basis.transpose();

    Quat::from_mat3(&bevy_rotation).normalize()
}

pub fn gazebo_pose_to_bevy(position: Vec3, rotation: Quat) -> Transform {
    Transform::from_translation(gazebo_position_to_bevy(position))
        .with_rotation(gazebo_rotation_to_bevy(rotation))
}

fn gazebo_to_bevy_basis() -> Mat3 {
    Mat3::from_cols(
        Vec3::new(0.0, 0.0, -1.0),
        Vec3::new(-1.0, 0.0, 0.0),
        Vec3::new(0.0, 1.0, 0.0),
    )
}

#[cfg(test)]
mod tests {
    use std::f32::consts::{FRAC_PI_2, FRAC_PI_3};

    use super::*;

    const EPSILON: f32 = 1e-5;

    #[test]
    fn converts_each_gazebo_basis_axis() {
        assert!(gazebo_position_to_bevy(Vec3::X).abs_diff_eq(Vec3::NEG_Z, EPSILON));
        assert!(gazebo_position_to_bevy(Vec3::Y).abs_diff_eq(Vec3::NEG_X, EPSILON));
        assert!(gazebo_position_to_bevy(Vec3::Z).abs_diff_eq(Vec3::Y, EPSILON));
    }

    #[test]
    fn identity_rotation_remains_identity() {
        let converted = gazebo_rotation_to_bevy(Quat::IDENTITY);

        assert!(converted.abs_diff_eq(Quat::IDENTITY, EPSILON));
    }

    #[test]
    fn converted_rotation_maps_vectors_equivalently() {
        let gazebo_rotation = Quat::from_euler(EulerRot::ZYX, FRAC_PI_2, -FRAC_PI_3, 0.27);
        let gazebo_vector = Vec3::new(0.3, -0.7, 0.2).normalize();

        let rotate_then_convert = gazebo_direction_to_bevy(gazebo_rotation * gazebo_vector);
        let convert_then_rotate =
            gazebo_rotation_to_bevy(gazebo_rotation) * gazebo_direction_to_bevy(gazebo_vector);

        assert!(rotate_then_convert.abs_diff_eq(convert_then_rotate, EPSILON));
    }

    #[test]
    fn pose_conversion_preserves_unit_scale() {
        let transform =
            gazebo_pose_to_bevy(Vec3::new(5.0, 2.0, 10.0), Quat::from_rotation_z(FRAC_PI_2));

        assert!(
            transform
                .translation
                .abs_diff_eq(Vec3::new(-2.0, 10.0, -5.0), EPSILON)
        );
        assert!(transform.scale.abs_diff_eq(Vec3::ONE, EPSILON));
    }
}
