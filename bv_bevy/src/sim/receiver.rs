use std::{
    io::{self, BufRead, BufReader, ErrorKind},
    net::{TcpStream, ToSocketAddrs},
    str,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    thread::{self, JoinHandle},
    time::Duration,
};

use bevy::prelude::*;

use super::{SimSnapshot, gazebo_pose_to_bevy};

const DEFAULT_ENDPOINT: &str = "127.0.0.1:7001";
const ENDPOINT_ENV: &str = "BV_SIM_ENDPOINT";
const CONNECT_TIMEOUT: Duration = Duration::from_secs(1);
const READ_TIMEOUT: Duration = Duration::from_millis(250);
const RECONNECT_DELAY: Duration = Duration::from_millis(500);
const MAX_STATE_LINE_BYTES: usize = 4 * 1024 * 1024;

#[derive(Component, Debug)]
pub struct SimEntity {
    id: String,
}

impl SimEntity {
    pub fn new(id: impl Into<String>) -> Self {
        Self { id: id.into() }
    }

    pub fn id(&self) -> &str {
        &self.id
    }
}

pub struct SimReceiverPlugin {
    endpoint: String,
}

impl SimReceiverPlugin {
    pub fn new(endpoint: impl Into<String>) -> Self {
        Self {
            endpoint: endpoint.into(),
        }
    }

    pub fn from_env() -> Self {
        Self::new(std::env::var(ENDPOINT_ENV).unwrap_or_else(|_| DEFAULT_ENDPOINT.to_owned()))
    }
}

impl Plugin for SimReceiverPlugin {
    fn build(&self, app: &mut App) {
        app.insert_resource(SimReceiver::spawn(self.endpoint.clone()))
            .init_resource::<AppliedSimState>()
            .init_resource::<ReportedReceiverStatus>()
            .add_systems(
                Update,
                (report_receiver_status, apply_latest_snapshot)
                    .chain()
                    .in_set(SimUpdateSet::ApplySnapshot),
            );
    }
}

#[derive(SystemSet, Debug, Clone, Copy, Eq, Hash, PartialEq)]
pub enum SimUpdateSet {
    ApplySnapshot,
}

#[derive(Resource, Debug, Default)]
pub struct AppliedSimState {
    stream_id: Option<String>,
    sequence: Option<u64>,
    sim_time_ns: Option<u64>,
}

impl AppliedSimState {
    pub fn stream_id(&self) -> Option<&str> {
        self.stream_id.as_deref()
    }

    pub fn sequence(&self) -> Option<u64> {
        self.sequence
    }

    pub fn sim_time_ns(&self) -> Option<u64> {
        self.sim_time_ns
    }

    fn record(&mut self, snapshot: &SimSnapshot) {
        self.stream_id = Some(snapshot.stream_id().to_owned());
        self.sequence = Some(snapshot.sequence());
        self.sim_time_ns = Some(snapshot.sim_time_ns());
    }
}

#[derive(Resource)]
struct SimReceiver {
    shared: Arc<ReceiverShared>,
    worker: Option<JoinHandle<()>>,
}

impl SimReceiver {
    fn spawn(endpoint: String) -> Self {
        let shared = Arc::new(ReceiverShared::default());
        let worker_shared = Arc::clone(&shared);
        let worker = thread::Builder::new()
            .name("bv-sim-receiver".to_owned())
            .spawn(move || receive_forever(&endpoint, &worker_shared))
            .expect("failed to start simulator receiver thread");

        Self {
            shared,
            worker: Some(worker),
        }
    }

    fn take_latest(&self) -> Option<SimSnapshot> {
        self.shared
            .latest
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .take()
    }

    fn status(&self) -> ReceiverStatus {
        self.shared
            .status
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .clone()
    }
}

impl Drop for SimReceiver {
    fn drop(&mut self) {
        self.shared.stop.store(true, Ordering::Relaxed);

        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

#[derive(Default)]
struct ReceiverShared {
    latest: Mutex<Option<SimSnapshot>>,
    status: Mutex<ReceiverStatus>,
    stop: AtomicBool,
}

impl ReceiverShared {
    fn publish(&self, snapshot: SimSnapshot) {
        *self
            .latest
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(snapshot);
    }

    fn set_status(&self, status: ReceiverStatus) {
        *self
            .status
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner()) = status;
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
enum ReceiverStatus {
    #[default]
    Connecting,
    Connected,
    Disconnected(String),
    Stopped,
}

#[derive(Resource, Default)]
struct ReportedReceiverStatus(Option<ReceiverStatus>);

fn report_receiver_status(
    receiver: Res<SimReceiver>,
    mut reported: ResMut<ReportedReceiverStatus>,
) {
    let current = receiver.status();
    if reported.0.as_ref() == Some(&current) {
        return;
    }

    match &current {
        ReceiverStatus::Connecting => info!("Connecting to simulator state stream"),
        ReceiverStatus::Connected => info!("Connected to simulator state stream"),
        ReceiverStatus::Disconnected(reason) => {
            warn!("Simulator state stream disconnected: {reason}")
        }
        ReceiverStatus::Stopped => info!("Simulator state receiver stopped"),
    }

    reported.0 = Some(current);
}

fn apply_latest_snapshot(
    receiver: Res<SimReceiver>,
    mut applied: ResMut<AppliedSimState>,
    mut entities: Query<(&SimEntity, &mut Transform, &mut Visibility)>,
) {
    let Some(snapshot) = receiver.take_latest() else {
        return;
    };

    let duplicate = applied.stream_id() == Some(snapshot.stream_id())
        && applied.sequence() == Some(snapshot.sequence())
        && applied.sim_time_ns() == Some(snapshot.sim_time_ns());
    if duplicate {
        return;
    }

    for (sim_entity, mut transform, mut visibility) in &mut entities {
        let Some(entity_state) = snapshot.entity(sim_entity.id()) else {
            *visibility = Visibility::Hidden;
            continue;
        };

        *transform = gazebo_pose_to_bevy(entity_state.position_m(), entity_state.orientation());
        *visibility = Visibility::Visible;
    }

    applied.record(&snapshot);
}

fn receive_forever(endpoint: &str, shared: &ReceiverShared) {
    while !shared.stop.load(Ordering::Relaxed) {
        match connect(endpoint) {
            Ok(stream) => {
                shared.set_status(ReceiverStatus::Connected);

                if let Err(error) = receive_stream(stream, shared)
                    && !shared.stop.load(Ordering::Relaxed)
                {
                    shared.set_status(ReceiverStatus::Disconnected(error.to_string()));
                }
            }
            Err(error) => shared.set_status(ReceiverStatus::Disconnected(error.to_string())),
        }

        wait_for_retry(shared);
    }

    shared.set_status(ReceiverStatus::Stopped);
}

fn connect(endpoint: &str) -> io::Result<TcpStream> {
    let addresses = endpoint.to_socket_addrs()?;
    let mut last_error = None;

    for address in addresses {
        match TcpStream::connect_timeout(&address, CONNECT_TIMEOUT) {
            Ok(stream) => {
                stream.set_nodelay(true)?;
                stream.set_read_timeout(Some(READ_TIMEOUT))?;
                return Ok(stream);
            }
            Err(error) => last_error = Some(error),
        }
    }

    Err(last_error.unwrap_or_else(|| {
        io::Error::new(
            ErrorKind::AddrNotAvailable,
            "endpoint resolved to no addresses",
        )
    }))
}

fn receive_stream(stream: TcpStream, shared: &ReceiverShared) -> io::Result<()> {
    let mut reader = BufReader::new(stream);
    let mut line = Vec::new();

    loop {
        match read_bounded_line(&mut reader, &mut line, &shared.stop)? {
            LineRead::Stopped => return Ok(()),
            LineRead::Eof => {
                return Err(io::Error::new(
                    ErrorKind::UnexpectedEof,
                    "producer closed the connection",
                ));
            }
            LineRead::Line => {}
        }

        let json = match str::from_utf8(&line) {
            Ok(json) => json,
            Err(error) => {
                warn!("Rejected non-UTF-8 simulator state: {error}");
                continue;
            }
        };

        match SimSnapshot::from_json(json) {
            Ok(snapshot) => shared.publish(snapshot),
            Err(error) => warn!("Rejected simulator state: {error}"),
        }
    }
}

#[derive(Debug, Eq, PartialEq)]
enum LineRead {
    Line,
    Eof,
    Stopped,
}

fn read_bounded_line<R: BufRead>(
    reader: &mut R,
    output: &mut Vec<u8>,
    stop: &AtomicBool,
) -> io::Result<LineRead> {
    output.clear();

    loop {
        if stop.load(Ordering::Relaxed) {
            return Ok(LineRead::Stopped);
        }

        let available = match reader.fill_buf() {
            Ok(available) => available,
            Err(error) if matches!(error.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut) => {
                continue;
            }
            Err(error) => return Err(error),
        };

        if available.is_empty() {
            return Ok(if output.is_empty() {
                LineRead::Eof
            } else {
                LineRead::Line
            });
        }

        let newline = available.iter().position(|byte| *byte == b'\n');
        let content_length = newline.unwrap_or(available.len());

        if output.len() + content_length > MAX_STATE_LINE_BYTES {
            return Err(io::Error::new(
                ErrorKind::InvalidData,
                "simulator state line exceeds size limit",
            ));
        }

        output.extend_from_slice(&available[..content_length]);
        let consumed = content_length + usize::from(newline.is_some());
        reader.consume(consumed);

        if newline.is_some() {
            if output.last() == Some(&b'\r') {
                output.pop();
            }
            return Ok(LineRead::Line);
        }
    }
}

fn wait_for_retry(shared: &ReceiverShared) {
    let deadline = std::time::Instant::now() + RECONNECT_DELAY;

    while std::time::Instant::now() < deadline {
        if shared.stop.load(Ordering::Relaxed) {
            return;
        }
        thread::sleep(Duration::from_millis(25));
    }
}

#[cfg(test)]
mod tests {
    use std::io::Cursor;

    use super::*;

    #[test]
    fn reads_one_line_without_consuming_the_next() {
        let mut reader = Cursor::new(b"first\nsecond\n");
        let mut output = Vec::new();
        let stop = AtomicBool::new(false);

        assert_eq!(
            read_bounded_line(&mut reader, &mut output, &stop).unwrap(),
            LineRead::Line
        );
        assert_eq!(output, b"first");
        assert_eq!(
            read_bounded_line(&mut reader, &mut output, &stop).unwrap(),
            LineRead::Line
        );
        assert_eq!(output, b"second");
    }

    #[test]
    fn reads_final_line_without_newline() {
        let mut reader = Cursor::new(b"final");
        let mut output = Vec::new();
        let stop = AtomicBool::new(false);

        assert_eq!(
            read_bounded_line(&mut reader, &mut output, &stop).unwrap(),
            LineRead::Line
        );
        assert_eq!(output, b"final");
    }

    #[test]
    fn honors_shutdown_before_reading() {
        let mut reader = Cursor::new(b"ignored\n");
        let mut output = Vec::new();
        let stop = AtomicBool::new(true);

        assert_eq!(
            read_bounded_line(&mut reader, &mut output, &stop).unwrap(),
            LineRead::Stopped
        );
    }

    #[test]
    fn applies_latest_snapshot_to_matching_entity() {
        let snapshot = SimSnapshot::from_json(
            r#"{
                "schema":"bv.sim_state",
                "version":1,
                "stream_id":"test-stream",
                "sequence":7,
                "sim_time_ns":9000000,
                "frame_id":"gazebo_world",
                "entities":[{
                    "id":"vehicle/x500",
                    "kind":"vehicle",
                    "position_m":[5.0,2.0,10.0],
                    "orientation_xyzw":[0.0,0.0,0.0,1.0]
                }]
            }"#,
        )
        .unwrap();
        let shared = Arc::new(ReceiverShared::default());
        shared.publish(snapshot);

        let mut app = App::new();
        app.insert_resource(SimReceiver {
            shared,
            worker: None,
        })
        .init_resource::<AppliedSimState>()
        .add_systems(Update, apply_latest_snapshot);
        let entity = app
            .world_mut()
            .spawn((
                SimEntity::new("vehicle/x500"),
                Transform::default(),
                Visibility::Hidden,
            ))
            .id();

        app.update();

        let world = app.world();
        let transform = world.entity(entity).get::<Transform>().unwrap();
        let visibility = world.entity(entity).get::<Visibility>().unwrap();
        let applied = world.resource::<AppliedSimState>();

        assert!(
            transform
                .translation
                .abs_diff_eq(Vec3::new(-2.0, 10.0, -5.0), 1e-5)
        );
        assert_eq!(*visibility, Visibility::Visible);
        assert_eq!(applied.sequence(), Some(7));
        assert_eq!(applied.sim_time_ns(), Some(9_000_000));
    }
}
