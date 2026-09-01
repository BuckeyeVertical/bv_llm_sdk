use std::f32::consts::FRAC_PI_2;

use bevy::{
    input::mouse::AccumulatedMouseMotion,
    prelude::*,
    window::{CursorGrabMode, CursorOptions, PrimaryWindow},
};

use crate::sim::{SimEntity, SimUpdateSet, VEHICLE_ID};

const PITCH_LIMIT: f32 = FRAC_PI_2 - 0.01;
const FOLLOW_DISTANCE: f32 = 10.0;
const FOLLOW_HEIGHT: f32 = 5.0;
const FOLLOW_LOOK_HEIGHT: f32 = 0.5;
const FOLLOW_RESPONSE: f32 = 5.0;

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
enum DebugCameraMode {
    #[default]
    Follow,
    Free,
}

/// User-controlled observer with no simulation authority.
/// The onboard drone camera receives its transform from Gazebo.
#[derive(Component)]
pub struct DebugCamera {
    move_speed: f32,
    fast_move_speed: f32,
    look_sensitivity: f32,
    mode: DebugCameraMode,
}

impl Default for DebugCamera {
    fn default() -> Self {
        Self {
            move_speed: 6.0,
            fast_move_speed: 24.0,
            look_sensitivity: 0.002,
            mode: DebugCameraMode::default(),
        }
    }
}

pub struct DebugCameraPlugin;

impl Plugin for DebugCameraPlugin {
    fn build(&self, app: &mut App) {
        app.add_systems(
            Update,
            (
                toggle_camera_mode,
                update_cursor_capture,
                update_debug_camera,
            )
                .chain()
                .after(SimUpdateSet::ApplySnapshot),
        );
    }
}

fn toggle_camera_mode(
    keys: Res<ButtonInput<KeyCode>>,
    mut camera: Single<&mut DebugCamera>,
    mut cursor_options: Single<&mut CursorOptions, With<PrimaryWindow>>,
) {
    if !keys.just_pressed(KeyCode::KeyF) {
        return;
    }

    camera.mode = match camera.mode {
        DebugCameraMode::Follow => DebugCameraMode::Free,
        DebugCameraMode::Free => DebugCameraMode::Follow,
    };

    if camera.mode == DebugCameraMode::Follow {
        cursor_options.visible = true;
        cursor_options.grab_mode = CursorGrabMode::None;
    }
}

/// Left click captures the cursor for mouse look. Escape or losing window
/// focus releases it.
fn update_cursor_capture(
    primary_window: Single<(&Window, &mut CursorOptions), With<PrimaryWindow>>,
    mouse_buttons: Res<ButtonInput<MouseButton>>,
    keys: Res<ButtonInput<KeyCode>>,
    camera: Single<&DebugCamera>,
    vehicles: Query<(&SimEntity, &Visibility)>,
) {
    let (window, mut cursor_options) = primary_window.into_inner();
    let vehicle_visible = vehicles
        .iter()
        .any(|(entity, visibility)| entity.id() == VEHICLE_ID && *visibility != Visibility::Hidden);
    let free_controls = uses_free_controls(camera.mode, vehicle_visible);

    if free_controls && mouse_buttons.just_pressed(MouseButton::Left) && window.focused {
        cursor_options.visible = false;
        cursor_options.grab_mode = CursorGrabMode::Locked;
    }

    if !free_controls || keys.just_pressed(KeyCode::Escape) || !window.focused {
        cursor_options.visible = true;
        cursor_options.grab_mode = CursorGrabMode::None;
    }
}

fn update_debug_camera(
    time: Res<Time>,
    keys: Res<ButtonInput<KeyCode>>,
    mouse_motion: Res<AccumulatedMouseMotion>,
    cursor_options: Single<&CursorOptions, With<PrimaryWindow>>,
    camera: Single<(&mut Transform, &DebugCamera)>,
    sim_entities: Query<(&SimEntity, &Transform, &Visibility), Without<DebugCamera>>,
) {
    let (mut transform, settings) = camera.into_inner();

    if settings.mode == DebugCameraMode::Follow
        && let Some((_, vehicle, _)) = sim_entities.iter().find(|(entity, _, visibility)| {
            entity.id() == VEHICLE_ID && **visibility != Visibility::Hidden
        })
    {
        let desired = follow_transform(vehicle);
        let response = 1.0 - (-FOLLOW_RESPONSE * time.delta_secs()).exp();
        transform.translation = transform.translation.lerp(desired.translation, response);
        transform.rotation = transform.rotation.slerp(desired.rotation, response);
        return;
    }

    if cursor_options.grab_mode == CursorGrabMode::None {
        return;
    }

    let mouse_delta = mouse_motion.delta;

    if mouse_delta != Vec2::ZERO {
        transform.rotation =
            rotated_camera(transform.rotation, mouse_delta, settings.look_sensitivity);
    }

    let mut input = Vec3::ZERO;

    if keys.pressed(KeyCode::KeyD) {
        input.x += 1.0;
    }
    if keys.pressed(KeyCode::KeyA) {
        input.x -= 1.0;
    }
    if keys.pressed(KeyCode::KeyE) {
        input.y += 1.0;
    }
    if keys.pressed(KeyCode::KeyQ) {
        input.y -= 1.0;
    }
    if keys.pressed(KeyCode::KeyW) {
        input.z += 1.0;
    }
    if keys.pressed(KeyCode::KeyS) {
        input.z -= 1.0;
    }

    let direction = movement_direction(transform.rotation, input);
    let speed = if keys.any_pressed([KeyCode::ShiftLeft, KeyCode::ShiftRight]) {
        settings.fast_move_speed
    } else {
        settings.move_speed
    };

    transform.translation += direction * speed * time.delta_secs();
}

fn uses_free_controls(mode: DebugCameraMode, vehicle_visible: bool) -> bool {
    mode == DebugCameraMode::Free || !vehicle_visible
}

fn follow_transform(vehicle: &Transform) -> Transform {
    let forward = horizontal_forward(vehicle.rotation);
    let target = vehicle.translation + Vec3::Y * FOLLOW_LOOK_HEIGHT;
    let position = vehicle.translation - forward * FOLLOW_DISTANCE + Vec3::Y * FOLLOW_HEIGHT;

    Transform::from_translation(position).looking_at(target, Vec3::Y)
}

fn horizontal_forward(rotation: Quat) -> Vec3 {
    let forward = rotation * Vec3::NEG_Z;
    Vec3::new(forward.x, 0.0, forward.z).normalize_or(Vec3::NEG_Z)
}

fn movement_direction(rotation: Quat, input: Vec3) -> Vec3 {
    if input == Vec3::ZERO {
        return Vec3::ZERO;
    }

    let right = rotation * Vec3::X;
    let forward = rotation * Vec3::NEG_Z;

    (right * input.x + Vec3::Y * input.y + forward * input.z).normalize_or_zero()
}

fn rotated_camera(rotation: Quat, mouse_delta: Vec2, sensitivity: f32) -> Quat {
    let (yaw, pitch, _) = rotation.to_euler(EulerRot::YXZ);
    let yaw = yaw - mouse_delta.x * sensitivity;
    let pitch = (pitch - mouse_delta.y * sensitivity).clamp(-PITCH_LIMIT, PITCH_LIMIT);

    Quat::from_euler(EulerRot::YXZ, yaw, pitch, 0.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    const EPSILON: f32 = 1e-5;

    #[test]
    fn identity_camera_moves_forward_along_negative_z() {
        let direction = movement_direction(Quat::IDENTITY, Vec3::Z);

        assert!(direction.abs_diff_eq(Vec3::NEG_Z, EPSILON));
    }

    #[test]
    fn follow_mode_uses_free_controls_until_a_vehicle_is_visible() {
        assert!(uses_free_controls(DebugCameraMode::Follow, false));
        assert!(!uses_free_controls(DebugCameraMode::Follow, true));
        assert!(uses_free_controls(DebugCameraMode::Free, true));
    }

    #[test]
    fn diagonal_movement_is_normalized() {
        let direction = movement_direction(Quat::IDENTITY, Vec3::new(1.0, 1.0, 1.0));

        assert!((direction.length() - 1.0).abs() < EPSILON);
    }

    #[test]
    fn mouse_look_clamps_pitch_and_removes_roll() {
        let rotation = Quat::from_euler(EulerRot::YXZ, 0.4, 0.2, 0.3);
        let rotated = rotated_camera(rotation, Vec2::new(0.0, -10_000.0), 1.0);
        let (_, pitch, roll) = rotated.to_euler(EulerRot::YXZ);

        assert!((pitch - PITCH_LIMIT).abs() < EPSILON);
        assert!(roll.abs() < EPSILON);
    }

    #[test]
    fn follow_camera_stays_behind_and_above_vehicle() {
        let vehicle = Transform::from_xyz(4.0, 20.0, -3.0);
        let camera = follow_transform(&vehicle);

        assert!(camera.translation.abs_diff_eq(
            Vec3::new(4.0, 20.0 + FOLLOW_HEIGHT, -3.0 + FOLLOW_DISTANCE),
            EPSILON,
        ));
        assert!(
            (camera.forward().as_vec3().dot(
                (vehicle.translation + Vec3::Y * FOLLOW_LOOK_HEIGHT - camera.translation)
                    .normalize()
            ) - 1.0)
                .abs()
                < EPSILON
        );
    }

    #[test]
    fn follow_direction_ignores_vehicle_pitch() {
        let pitched = Quat::from_rotation_x(0.7);
        let forward = horizontal_forward(pitched);

        assert!(forward.abs_diff_eq(Vec3::NEG_Z, EPSILON));
    }
}
