use std::{f32::consts::FRAC_PI_2, sync::Arc};

use bevy::{
    camera::{PerspectiveProjection, Projection, RenderTarget},
    diagnostic::FrameCount,
    prelude::*,
    render::{
        gpu_readback::{Readback, ReadbackComplete},
        render_resource::{TextureFormat, TextureUsages},
    },
};

use crate::sim::{AppliedSimState, ONBOARD_CAMERA_ID, SimEntity, SimUpdateSet};

const COPY_BYTES_PER_ROW_ALIGNMENT: usize = 256;
const CAPTURE_WARMUP_FRAMES: u32 = 3;
const CAMERA_WIDTH_ENV: &str = "BV_CAMERA_WIDTH";
const CAMERA_HEIGHT_ENV: &str = "BV_CAMERA_HEIGHT";
const CAMERA_HFOV_DEGREES_ENV: &str = "BV_CAMERA_HFOV_DEG";

#[derive(Clone, Debug)]
pub struct CameraFrame {
    pub sequence: u64,
    pub sim_stream_id: String,
    pub sim_sequence: u64,
    pub sim_time_ns: u64,
    pub camera_id: String,
    pub intrinsics: PinholeIntrinsics,
    pub width: u32,
    pub height: u32,
    pub rgba8: Arc<[u8]>,
}

#[derive(Event)]
pub struct CameraFrameReady(pub CameraFrame);

#[derive(Component)]
pub struct OnboardCamera;

#[derive(Clone, Debug)]
pub struct DroneCameraConfig {
    pub width: u32,
    pub height: u32,
    pub horizontal_fov: f32,
    pub near: f32,
    pub far: f32,
}

impl DroneCameraConfig {
    pub fn intrinsics(&self) -> PinholeIntrinsics {
        let focal_length = self.width as f32 / (2.0 * (self.horizontal_fov / 2.0).tan());
        PinholeIntrinsics {
            width: self.width,
            height: self.height,
            fx: focal_length,
            fy: focal_length,
            cx: self.width as f32 / 2.0,
            cy: self.height as f32 / 2.0,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PinholeIntrinsics {
    pub width: u32,
    pub height: u32,
    pub fx: f32,
    pub fy: f32,
    pub cx: f32,
    pub cy: f32,
}

impl PinholeIntrinsics {
    pub fn project_camera_point(&self, point: Vec3) -> Option<Vec2> {
        let depth = -point.z;
        if depth <= 0.0 {
            return None;
        }

        Some(Vec2::new(
            self.cx + self.fx * point.x / depth,
            self.cy - self.fy * point.y / depth,
        ))
    }

    pub fn project_world_point(
        &self,
        camera_transform: &GlobalTransform,
        world_point: Vec3,
    ) -> Option<Vec2> {
        let camera_point = camera_transform
            .affine()
            .inverse()
            .transform_point3(world_point);
        self.project_camera_point(camera_point)
    }
}

impl Default for DroneCameraConfig {
    fn default() -> Self {
        Self {
            width: 1280,
            height: 720,
            horizontal_fov: FRAC_PI_2,
            near: 0.05,
            far: 1000.0,
        }
    }
}

pub struct DroneCameraPlugin {
    config: DroneCameraConfig,
}

impl DroneCameraPlugin {
    pub fn new(config: DroneCameraConfig) -> Self {
        assert!(config.width > 0, "camera width must be positive");
        assert!(config.height > 0, "camera height must be positive");
        assert!(
            config.horizontal_fov > 0.0 && config.horizontal_fov < std::f32::consts::PI,
            "camera horizontal field of view must be between zero and pi"
        );
        assert!(
            config.near > 0.0 && config.far > config.near,
            "camera clipping planes are invalid"
        );

        Self { config }
    }

    pub fn from_env() -> Self {
        let mut config = DroneCameraConfig::default();
        config.width = env_value(CAMERA_WIDTH_ENV).unwrap_or(config.width);
        config.height = env_value(CAMERA_HEIGHT_ENV).unwrap_or(config.height);
        let horizontal_fov_degrees =
            env_value(CAMERA_HFOV_DEGREES_ENV).unwrap_or(config.horizontal_fov.to_degrees());
        config.horizontal_fov = horizontal_fov_degrees.to_radians();
        Self::new(config)
    }
}

impl Default for DroneCameraPlugin {
    fn default() -> Self {
        Self::new(DroneCameraConfig::default())
    }
}

impl Plugin for DroneCameraPlugin {
    fn build(&self, app: &mut App) {
        app.insert_resource(CameraCaptureConfig(self.config.clone()))
            .init_resource::<CaptureState>()
            .add_systems(Startup, spawn_onboard_camera)
            .add_systems(PreUpdate, make_readbacks_one_shot)
            .add_systems(
                Update,
                (update_camera_activity, queue_camera_capture)
                    .chain()
                    .after(SimUpdateSet::ApplySnapshot),
            )
            .add_observer(complete_camera_capture);
    }
}

#[derive(Resource)]
struct CameraCaptureConfig(DroneCameraConfig);

#[derive(Resource)]
struct DroneRenderTarget(Handle<Image>);

#[derive(Resource, Default)]
struct CaptureState {
    next_sequence: u64,
    pending: Option<Entity>,
}

#[derive(Component)]
struct PendingCapture {
    frame_sequence: u64,
    sim_stream_id: String,
    sim_sequence: u64,
    sim_time_ns: u64,
    intrinsics: PinholeIntrinsics,
    width: u32,
    height: u32,
}

fn spawn_onboard_camera(
    mut commands: Commands,
    config: Res<CameraCaptureConfig>,
    mut images: ResMut<Assets<Image>>,
) {
    let config = &config.0;
    let mut image = Image::new_target_texture(
        config.width,
        config.height,
        TextureFormat::Rgba8UnormSrgb,
        None,
    );
    image.texture_descriptor.usage |= TextureUsages::COPY_SRC;
    let target = images.add(image);
    let aspect_ratio = config.width as f32 / config.height as f32;

    commands.spawn((
        Name::new("Gazebo onboard camera"),
        SimEntity::new(ONBOARD_CAMERA_ID),
        OnboardCamera,
        Camera3d::default(),
        Camera {
            order: -1,
            is_active: false,
            ..default()
        },
        RenderTarget::Image(target.clone().into()),
        Projection::Perspective(PerspectiveProjection {
            fov: vertical_fov(config.horizontal_fov, aspect_ratio),
            aspect_ratio,
            near: config.near,
            far: config.far,
            ..default()
        }),
        Transform::default(),
        Visibility::Hidden,
    ));
    commands.insert_resource(DroneRenderTarget(target));
}

fn update_camera_activity(camera: Single<(&Visibility, &mut Camera), With<OnboardCamera>>) {
    let (visibility, mut camera) = camera.into_inner();
    camera.is_active = *visibility != Visibility::Hidden;
}

fn queue_camera_capture(
    mut commands: Commands,
    frame_count: Res<FrameCount>,
    applied: Res<AppliedSimState>,
    config: Res<CameraCaptureConfig>,
    target: Res<DroneRenderTarget>,
    camera: Single<&Camera, With<OnboardCamera>>,
    mut state: ResMut<CaptureState>,
) {
    if !applied.is_changed()
        || !camera.is_active
        || state.pending.is_some()
        || frame_count.0 < CAPTURE_WARMUP_FRAMES
    {
        return;
    }

    let (Some(sim_stream_id), Some(sim_sequence), Some(sim_time_ns)) = (
        applied.stream_id(),
        applied.sequence(),
        applied.sim_time_ns(),
    ) else {
        return;
    };

    let pending = commands
        .spawn((
            Readback::texture(target.0.clone()),
            PendingCapture {
                frame_sequence: state.next_sequence,
                sim_stream_id: sim_stream_id.to_owned(),
                sim_sequence,
                sim_time_ns,
                intrinsics: config.0.intrinsics(),
                width: config.0.width,
                height: config.0.height,
            },
        ))
        .id();

    state.next_sequence += 1;
    state.pending = Some(pending);
}

fn make_readbacks_one_shot(
    mut commands: Commands,
    readbacks: Query<Entity, (With<PendingCapture>, With<Readback>)>,
) {
    for entity in &readbacks {
        commands.entity(entity).remove::<Readback>();
    }
}

fn complete_camera_capture(
    event: On<ReadbackComplete>,
    pending: Query<&PendingCapture>,
    mut state: ResMut<CaptureState>,
    mut commands: Commands,
) {
    let Ok(metadata) = pending.get(event.entity) else {
        return;
    };

    let rgba8 = remove_row_padding(&event.data, metadata.width, metadata.height);
    commands.trigger(CameraFrameReady(CameraFrame {
        sequence: metadata.frame_sequence,
        sim_stream_id: metadata.sim_stream_id.clone(),
        sim_sequence: metadata.sim_sequence,
        sim_time_ns: metadata.sim_time_ns,
        camera_id: ONBOARD_CAMERA_ID.to_owned(),
        intrinsics: metadata.intrinsics,
        width: metadata.width,
        height: metadata.height,
        rgba8: Arc::from(rgba8),
    }));
    commands.entity(event.entity).despawn();
    state.pending = None;
}

fn vertical_fov(horizontal_fov: f32, aspect_ratio: f32) -> f32 {
    2.0 * ((horizontal_fov / 2.0).tan() / aspect_ratio).atan()
}

fn env_value<T>(name: &str) -> Option<T>
where
    T: std::str::FromStr,
    T::Err: std::fmt::Display,
{
    std::env::var(name).ok().map(|value| {
        value
            .parse()
            .unwrap_or_else(|error| panic!("invalid {name} value '{value}': {error}"))
    })
}

fn remove_row_padding(data: &[u8], width: u32, height: u32) -> Vec<u8> {
    let row_bytes = width as usize * 4;
    let padded_row_bytes =
        row_bytes.div_ceil(COPY_BYTES_PER_ROW_ALIGNMENT) * COPY_BYTES_PER_ROW_ALIGNMENT;

    if row_bytes == padded_row_bytes {
        return data[..row_bytes * height as usize].to_vec();
    }

    let mut pixels = Vec::with_capacity(row_bytes * height as usize);
    for row in data.chunks_exact(padded_row_bytes).take(height as usize) {
        pixels.extend_from_slice(&row[..row_bytes]);
    }
    pixels
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn converts_horizontal_to_vertical_field_of_view() {
        let fov = vertical_fov(FRAC_PI_2, 16.0 / 9.0);

        assert!((fov - 1.024_778_9).abs() < 1e-6);
    }

    #[test]
    fn projects_known_camera_points_to_pixels() {
        let intrinsics = DroneCameraConfig::default().intrinsics();

        assert_eq!(
            intrinsics.project_camera_point(Vec3::new(0.0, 0.0, -10.0)),
            Some(Vec2::new(640.0, 360.0))
        );
        assert_eq!(
            intrinsics.project_camera_point(Vec3::new(1.0, 0.5, -2.0)),
            Some(Vec2::new(960.0, 200.0))
        );
        assert_eq!(intrinsics.project_camera_point(Vec3::Z), None);
    }

    #[test]
    fn projection_accounts_for_camera_world_pose() {
        let intrinsics = DroneCameraConfig::default().intrinsics();
        let camera = GlobalTransform::from(Transform::from_xyz(5.0, 2.0, 3.0));

        assert_eq!(
            intrinsics.project_world_point(&camera, Vec3::new(5.0, 2.0, -7.0)),
            Some(Vec2::new(640.0, 360.0))
        );
    }

    #[test]
    fn removes_gpu_row_padding() {
        let width = 2_u32;
        let height = 2_u32;
        let mut data = vec![0; COPY_BYTES_PER_ROW_ALIGNMENT * height as usize];
        data[..8].copy_from_slice(&[1, 2, 3, 4, 5, 6, 7, 8]);
        data[COPY_BYTES_PER_ROW_ALIGNMENT..COPY_BYTES_PER_ROW_ALIGNMENT + 8]
            .copy_from_slice(&[9, 10, 11, 12, 13, 14, 15, 16]);

        assert_eq!(
            remove_row_padding(&data, width, height),
            (1_u8..=16).collect::<Vec<_>>()
        );
    }
}
