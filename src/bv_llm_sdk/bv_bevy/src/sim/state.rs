use std::{
    collections::HashMap,
    error::Error,
    fmt::{self, Display, Formatter},
};

use bevy::math::{Quat, Vec3};
use serde::Deserialize;

pub const BV_SIM_SCHEMA: &str = "bv.sim_state";
pub const BV_SIM_VERSION: u32 = 1;
pub const GAZEBO_WORLD_FRAME: &str = "gazebo_world";

const MAX_ENTITIES: usize = 10_000;
const MIN_QUATERNION_LENGTH_SQUARED: f32 = 1e-12;

/// One validated, complete snapshot of the selected Gazebo entities.
#[derive(Debug, Clone)]
pub struct SimSnapshot {
    stream_id: String,
    sequence: u64,
    sim_time_ns: u64,
    frame_id: String,
    entities: HashMap<String, EntityState>,
}

impl SimSnapshot {
    pub fn from_json(json: &str) -> Result<Self, SimStateError> {
        let wire: WireSimSnapshot = serde_json::from_str(json)?;
        wire.try_into()
    }

    pub fn stream_id(&self) -> &str {
        &self.stream_id
    }

    pub fn sequence(&self) -> u64 {
        self.sequence
    }

    pub fn sim_time_ns(&self) -> u64 {
        self.sim_time_ns
    }

    pub fn frame_id(&self) -> &str {
        &self.frame_id
    }

    pub fn entity(&self, id: &str) -> Option<&EntityState> {
        self.entities.get(id)
    }

    pub fn entities(&self) -> impl Iterator<Item = &EntityState> {
        self.entities.values()
    }

    pub fn entity_count(&self) -> usize {
        self.entities.len()
    }
}

#[derive(Debug, Clone)]
pub struct EntityState {
    id: String,
    kind: String,
    position_m: Vec3,
    orientation: Quat,
}

impl EntityState {
    pub fn id(&self) -> &str {
        &self.id
    }

    pub fn kind(&self) -> &str {
        &self.kind
    }

    /// Position expressed in the snapshot's Gazebo world frame.
    pub fn position_m(&self) -> Vec3 {
        self.position_m
    }

    /// Orientation expressed in the snapshot's Gazebo world frame.
    pub fn orientation(&self) -> Quat {
        self.orientation
    }
}

#[derive(Debug)]
pub enum SimStateError {
    Json(serde_json::Error),
    UnsupportedSchema(String),
    UnsupportedVersion(u32),
    EmptyStreamId,
    UnsupportedFrame(String),
    TooManyEntities(usize),
    EmptyEntityId,
    EmptyEntityKind(String),
    DuplicateEntityId(String),
    InvalidPosition(String),
    InvalidOrientation(String),
}

impl Display for SimStateError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        match self {
            Self::Json(error) => write!(formatter, "invalid state JSON: {error}"),
            Self::UnsupportedSchema(schema) => {
                write!(formatter, "unsupported state schema '{schema}'")
            }
            Self::UnsupportedVersion(version) => {
                write!(formatter, "unsupported state version {version}")
            }
            Self::EmptyStreamId => write!(formatter, "stream_id must not be empty"),
            Self::UnsupportedFrame(frame) => {
                write!(formatter, "unsupported coordinate frame '{frame}'")
            }
            Self::TooManyEntities(count) => {
                write!(formatter, "snapshot contains too many entities ({count})")
            }
            Self::EmptyEntityId => write!(formatter, "entity id must not be empty"),
            Self::EmptyEntityKind(id) => {
                write!(formatter, "entity '{id}' has an empty kind")
            }
            Self::DuplicateEntityId(id) => {
                write!(formatter, "snapshot contains duplicate entity id '{id}'")
            }
            Self::InvalidPosition(id) => {
                write!(formatter, "entity '{id}' has a non-finite position")
            }
            Self::InvalidOrientation(id) => {
                write!(formatter, "entity '{id}' has an invalid orientation")
            }
        }
    }
}

impl Error for SimStateError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Json(error) => Some(error),
            _ => None,
        }
    }
}

impl From<serde_json::Error> for SimStateError {
    fn from(error: serde_json::Error) -> Self {
        Self::Json(error)
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct WireSimSnapshot {
    schema: String,
    version: u32,
    stream_id: String,
    sequence: u64,
    sim_time_ns: u64,
    frame_id: String,
    entities: Vec<WireEntityState>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct WireEntityState {
    id: String,
    kind: String,
    position_m: [f32; 3],
    orientation_xyzw: [f32; 4],
}

impl TryFrom<WireSimSnapshot> for SimSnapshot {
    type Error = SimStateError;

    fn try_from(wire: WireSimSnapshot) -> Result<Self, Self::Error> {
        if wire.schema != BV_SIM_SCHEMA {
            return Err(SimStateError::UnsupportedSchema(wire.schema));
        }
        if wire.version != BV_SIM_VERSION {
            return Err(SimStateError::UnsupportedVersion(wire.version));
        }
        if wire.stream_id.trim().is_empty() {
            return Err(SimStateError::EmptyStreamId);
        }
        if wire.frame_id != GAZEBO_WORLD_FRAME {
            return Err(SimStateError::UnsupportedFrame(wire.frame_id));
        }
        if wire.entities.len() > MAX_ENTITIES {
            return Err(SimStateError::TooManyEntities(wire.entities.len()));
        }

        let mut entities = HashMap::with_capacity(wire.entities.len());

        for wire_entity in wire.entities {
            let entity = EntityState::try_from(wire_entity)?;
            let id = entity.id.clone();

            if entities.insert(id.clone(), entity).is_some() {
                return Err(SimStateError::DuplicateEntityId(id));
            }
        }

        Ok(Self {
            stream_id: wire.stream_id,
            sequence: wire.sequence,
            sim_time_ns: wire.sim_time_ns,
            frame_id: wire.frame_id,
            entities,
        })
    }
}

impl TryFrom<WireEntityState> for EntityState {
    type Error = SimStateError;

    fn try_from(wire: WireEntityState) -> Result<Self, Self::Error> {
        if wire.id.trim().is_empty() {
            return Err(SimStateError::EmptyEntityId);
        }
        if wire.kind.trim().is_empty() {
            return Err(SimStateError::EmptyEntityKind(wire.id));
        }

        let position_m = Vec3::from_array(wire.position_m);
        if !position_m.is_finite() {
            return Err(SimStateError::InvalidPosition(wire.id));
        }

        let orientation = Quat::from_array(wire.orientation_xyzw);
        if !orientation.is_finite() || orientation.length_squared() < MIN_QUATERNION_LENGTH_SQUARED
        {
            return Err(SimStateError::InvalidOrientation(wire.id));
        }

        Ok(Self {
            id: wire.id,
            kind: wire.kind,
            position_m,
            orientation: orientation.normalize(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const VALID_STATE: &str = r#"
        {
          "schema": "bv.sim_state",
          "version": 1,
          "stream_id": "test-run-1",
          "sequence": 42,
          "sim_time_ns": 1250000000,
          "frame_id": "gazebo_world",
          "entities": [
            {
              "id": "vehicle/x500",
              "kind": "vehicle",
              "position_m": [5.0, 2.0, 10.0],
              "orientation_xyzw": [0.0, 0.0, 0.0, 2.0]
            }
          ]
        }
    "#;

    #[test]
    fn parses_and_indexes_valid_snapshot() {
        let snapshot = SimSnapshot::from_json(VALID_STATE).unwrap();
        let vehicle = snapshot.entity("vehicle/x500").unwrap();

        assert_eq!(snapshot.stream_id(), "test-run-1");
        assert_eq!(snapshot.sequence(), 42);
        assert_eq!(snapshot.sim_time_ns(), 1_250_000_000);
        assert_eq!(snapshot.frame_id(), GAZEBO_WORLD_FRAME);
        assert_eq!(snapshot.entity_count(), 1);
        assert_eq!(vehicle.kind(), "vehicle");
        assert_eq!(vehicle.position_m(), Vec3::new(5.0, 2.0, 10.0));
        assert!(vehicle.orientation().abs_diff_eq(Quat::IDENTITY, 1e-6));
    }

    #[test]
    fn rejects_unsupported_version() {
        let json = VALID_STATE.replace("\"version\": 1", "\"version\": 2");
        let error = SimSnapshot::from_json(&json).unwrap_err();

        assert!(matches!(error, SimStateError::UnsupportedVersion(2)));
    }

    #[test]
    fn rejects_duplicate_entity_ids() {
        let json = VALID_STATE.replace(
            "]\n        }",
            r#",
            {
              "id": "vehicle/x500",
              "kind": "camera",
              "position_m": [0.0, 0.0, 0.0],
              "orientation_xyzw": [0.0, 0.0, 0.0, 1.0]
            }
          ]
        }"#,
        );
        let error = SimSnapshot::from_json(&json).unwrap_err();

        assert!(matches!(error, SimStateError::DuplicateEntityId(id) if id == "vehicle/x500"));
    }

    #[test]
    fn rejects_zero_length_quaternion() {
        let json = VALID_STATE.replace("[0.0, 0.0, 0.0, 2.0]", "[0.0, 0.0, 0.0, 0.0]");
        let error = SimSnapshot::from_json(&json).unwrap_err();

        assert!(matches!(error, SimStateError::InvalidOrientation(id) if id == "vehicle/x500"));
    }

    #[test]
    fn rejects_unknown_fields() {
        let json = VALID_STATE.replace(
            "\"sequence\": 42,",
            "\"sequence\": 42, \"unexpected\": true,",
        );
        let error = SimSnapshot::from_json(&json).unwrap_err();

        assert!(matches!(error, SimStateError::Json(_)));
    }
}
