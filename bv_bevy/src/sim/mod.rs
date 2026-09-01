mod coordinates;
mod receiver;
mod state;

pub const ONBOARD_CAMERA_ID: &str = "camera/drone";
pub const VEHICLE_ID: &str = "vehicle/x500";

pub use coordinates::{
    gazebo_direction_to_bevy, gazebo_pose_to_bevy, gazebo_position_to_bevy, gazebo_rotation_to_bevy,
};
pub use receiver::{AppliedSimState, SimEntity, SimReceiverPlugin, SimUpdateSet};
pub use state::{
    BV_SIM_SCHEMA, BV_SIM_VERSION, EntityState, GAZEBO_WORLD_FRAME, SimSnapshot, SimStateError,
};
