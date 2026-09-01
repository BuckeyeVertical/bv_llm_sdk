use std::{
    io::{self, ErrorKind, Write},
    net::{TcpListener, TcpStream},
    sync::{
        Arc, Condvar, Mutex,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    thread::{self, JoinHandle},
    time::Duration,
};

use bevy::prelude::*;
use image::{ExtendedColorType, codecs::jpeg::JpegEncoder};
use serde::Serialize;

use super::{CameraFrame, CameraFrameReady};

const DEFAULT_BIND_ADDRESS: &str = "127.0.0.1:7002";
const BIND_ADDRESS_ENV: &str = "BV_CAMERA_BIND";
const JPEG_QUALITY_ENV: &str = "BV_CAMERA_JPEG_QUALITY";
const DEFAULT_JPEG_QUALITY: u8 = 90;
const ACCEPT_RETRY: Duration = Duration::from_millis(100);
const WRITE_TIMEOUT: Duration = Duration::from_millis(100);

pub struct CameraFrameServerPlugin {
    bind_address: String,
    jpeg_quality: u8,
}

impl CameraFrameServerPlugin {
    pub fn new(bind_address: impl Into<String>, jpeg_quality: u8) -> Self {
        assert!(jpeg_quality > 0, "JPEG quality must be between 1 and 100");
        assert!(
            jpeg_quality <= 100,
            "JPEG quality must be between 1 and 100"
        );
        Self {
            bind_address: bind_address.into(),
            jpeg_quality,
        }
    }

    pub fn from_env() -> Self {
        let bind_address =
            std::env::var(BIND_ADDRESS_ENV).unwrap_or_else(|_| DEFAULT_BIND_ADDRESS.to_owned());
        let jpeg_quality = std::env::var(JPEG_QUALITY_ENV)
            .ok()
            .and_then(|value| value.parse().ok())
            .unwrap_or(DEFAULT_JPEG_QUALITY);
        Self::new(bind_address, jpeg_quality)
    }
}

impl Plugin for CameraFrameServerPlugin {
    fn build(&self, app: &mut App) {
        app.insert_resource(CameraFramePublisher::spawn(
            self.bind_address.clone(),
            self.jpeg_quality,
        ))
        .init_resource::<ReportedFrameServerStatus>()
        .add_systems(Update, report_frame_server_status)
        .add_observer(publish_camera_frame);
    }
}

#[derive(Resource)]
struct CameraFramePublisher {
    shared: Arc<PublisherShared>,
    workers: Vec<JoinHandle<()>>,
}

impl CameraFramePublisher {
    fn spawn(bind_address: String, jpeg_quality: u8) -> Self {
        let shared = Arc::new(PublisherShared::default());
        let accept_shared = Arc::clone(&shared);
        let broadcast_shared = Arc::clone(&shared);
        let broadcast_address = bind_address.clone();
        let accept_worker = thread::Builder::new()
            .name("bv-camera-frame-accept".to_owned())
            .spawn(move || accept_consumers(&bind_address, &accept_shared))
            .expect("failed to start camera frame server thread");
        let broadcast_worker = thread::Builder::new()
            .name("bv-camera-frame-broadcast".to_owned())
            .spawn(move || broadcast_frames(&broadcast_address, jpeg_quality, &broadcast_shared))
            .expect("failed to start camera frame broadcaster thread");

        Self {
            shared,
            workers: vec![accept_worker, broadcast_worker],
        }
    }

    fn publish(&self, frame: CameraFrame) {
        self.shared
            .state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .latest = Some(frame);
        self.shared.frame_available.notify_one();
    }

    fn status(&self) -> FrameServerStatus {
        self.shared
            .status
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .clone()
    }
}

impl Drop for CameraFramePublisher {
    fn drop(&mut self) {
        self.shared.stop.store(true, Ordering::Relaxed);
        self.shared.frame_available.notify_all();

        for worker in self.workers.drain(..) {
            let _ = worker.join();
        }
    }
}

#[derive(Default)]
struct PublisherShared {
    state: Mutex<PublisherState>,
    frame_available: Condvar,
    stop: AtomicBool,
    consumer_count: AtomicUsize,
    status: Mutex<FrameServerStatus>,
}

#[derive(Default)]
struct PublisherState {
    latest: Option<CameraFrame>,
    consumers: Vec<TcpStream>,
}

impl PublisherShared {
    fn set_status(&self, status: FrameServerStatus) {
        *self
            .status
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner()) = status;
    }

    fn take_delivery(&self) -> Option<(CameraFrame, Vec<TcpStream>)> {
        let mut state = self
            .state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());

        while (state.latest.is_none() || state.consumers.is_empty())
            && !self.stop.load(Ordering::Relaxed)
        {
            let result = self
                .frame_available
                .wait_timeout(state, ACCEPT_RETRY)
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            state = result.0;
        }

        if self.stop.load(Ordering::Relaxed) {
            return None;
        }

        Some((
            state.latest.take().expect("frame availability was checked"),
            std::mem::take(&mut state.consumers),
        ))
    }

    fn add_consumer(&self, consumer: TcpStream) -> usize {
        let mut state = self
            .state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        state.consumers.push(consumer);
        let count = self.consumer_count.fetch_add(1, Ordering::Relaxed) + 1;
        self.frame_available.notify_one();
        count
    }

    fn return_consumers(&self, consumers: Vec<TcpStream>, disconnected: usize) -> usize {
        let mut state = self
            .state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        state.consumers.extend(consumers);
        if disconnected == 0 {
            self.consumer_count.load(Ordering::Relaxed)
        } else {
            self.consumer_count
                .fetch_sub(disconnected, Ordering::Relaxed)
                - disconnected
        }
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
enum FrameServerStatus {
    #[default]
    Starting,
    Listening {
        address: String,
        consumers: usize,
        last_disconnect: Option<String>,
    },
    Failed(String),
    Stopped,
}

#[derive(Resource, Default)]
struct ReportedFrameServerStatus(Option<FrameServerStatus>);

#[derive(Serialize)]
struct FrameHeader<'a> {
    schema: &'static str,
    version: u32,
    sequence: u64,
    sim_stream_id: &'a str,
    sim_sequence: u64,
    sim_time_ns: u64,
    camera_id: &'a str,
    camera_model: &'static str,
    fx: f32,
    fy: f32,
    cx: f32,
    cy: f32,
    width: u32,
    height: u32,
    encoding: &'static str,
    data_length: usize,
}

fn publish_camera_frame(event: On<CameraFrameReady>, publisher: Res<CameraFramePublisher>) {
    publisher.publish(event.0.clone());
}

fn report_frame_server_status(
    publisher: Res<CameraFramePublisher>,
    mut reported: ResMut<ReportedFrameServerStatus>,
) {
    let current = publisher.status();
    if reported.0.as_ref() == Some(&current) {
        return;
    }

    match &current {
        FrameServerStatus::Starting => info!("Starting camera frame server"),
        FrameServerStatus::Listening {
            address,
            consumers,
            last_disconnect,
        } => {
            if let Some(reason) = last_disconnect {
                info!(
                    "Camera frame server listening on {address} with {consumers} consumer(s); last disconnect: {reason}"
                );
            } else {
                info!("Camera frame server listening on {address} with {consumers} consumer(s)");
            }
        }
        FrameServerStatus::Failed(reason) => error!("Camera frame server failed: {reason}"),
        FrameServerStatus::Stopped => info!("Camera frame server stopped"),
    }

    reported.0 = Some(current);
}

fn accept_consumers(bind_address: &str, shared: &PublisherShared) {
    let listener = match TcpListener::bind(bind_address) {
        Ok(listener) => listener,
        Err(error) => {
            shared.set_status(FrameServerStatus::Failed(error.to_string()));
            return;
        }
    };

    if let Err(error) = listener.set_nonblocking(true) {
        shared.set_status(FrameServerStatus::Failed(error.to_string()));
        return;
    }
    let address = match listener.local_addr() {
        Ok(address) => address.to_string(),
        Err(error) => {
            shared.set_status(FrameServerStatus::Failed(error.to_string()));
            return;
        }
    };
    shared.set_status(listening_status(&address, 0, None));

    while !shared.stop.load(Ordering::Relaxed) {
        match listener.accept() {
            Ok((stream, _)) => {
                if let Err(error) = configure_consumer(&stream) {
                    shared.set_status(listening_status(
                        &address,
                        consumer_count(shared),
                        Some(error.to_string()),
                    ));
                    continue;
                }
                let consumers = shared.add_consumer(stream);
                shared.set_status(listening_status(&address, consumers, None));
            }
            Err(error) if error.kind() == ErrorKind::WouldBlock => {
                thread::sleep(ACCEPT_RETRY);
            }
            Err(error) => {
                shared.set_status(FrameServerStatus::Failed(error.to_string()));
                return;
            }
        }
    }

    shared.set_status(FrameServerStatus::Stopped);
}

fn broadcast_frames(bind_address: &str, jpeg_quality: u8, shared: &PublisherShared) {
    while let Some((frame, consumers)) = shared.take_delivery() {
        let payload = match encode_jpeg(&frame, jpeg_quality)
            .and_then(|jpeg| serialize_frame(&frame, &jpeg))
        {
            Ok(payload) => payload,
            Err(error) => {
                let count = shared.return_consumers(consumers, 0);
                shared.set_status(listening_status(
                    bind_address,
                    count,
                    Some(error.to_string()),
                ));
                continue;
            }
        };

        let recipient_count = consumers.len();
        let mut connected = Vec::with_capacity(recipient_count);
        let mut last_disconnect = None;
        for mut consumer in consumers {
            match consumer.write_all(&payload) {
                Ok(()) => connected.push(consumer),
                Err(error) => last_disconnect = Some(error.to_string()),
            }
        }

        let disconnected = recipient_count - connected.len();
        let count = shared.return_consumers(connected, disconnected);
        if last_disconnect.is_some() {
            shared.set_status(listening_status(bind_address, count, last_disconnect));
        }
    }
}

fn configure_consumer(stream: &TcpStream) -> io::Result<()> {
    stream.set_nodelay(true)?;
    stream.set_write_timeout(Some(WRITE_TIMEOUT))?;
    Ok(())
}

fn consumer_count(shared: &PublisherShared) -> usize {
    shared.consumer_count.load(Ordering::Relaxed)
}

fn listening_status(
    address: &str,
    consumers: usize,
    last_disconnect: Option<String>,
) -> FrameServerStatus {
    FrameServerStatus::Listening {
        address: address.to_owned(),
        consumers,
        last_disconnect,
    }
}

fn encode_jpeg(frame: &CameraFrame, quality: u8) -> io::Result<Vec<u8>> {
    let expected_length = (frame.width as usize)
        .checked_mul(frame.height as usize)
        .and_then(|pixels| pixels.checked_mul(4))
        .ok_or_else(|| io::Error::new(ErrorKind::InvalidData, "frame dimensions overflow"))?;
    if frame.rgba8.len() != expected_length {
        return Err(io::Error::new(
            ErrorKind::InvalidData,
            "RGBA frame length does not match its dimensions",
        ));
    }

    let mut rgb = Vec::with_capacity(frame.width as usize * frame.height as usize * 3);
    for pixel in frame.rgba8.chunks_exact(4) {
        rgb.extend_from_slice(&pixel[..3]);
    }

    let mut jpeg = Vec::new();
    JpegEncoder::new_with_quality(&mut jpeg, quality)
        .encode(&rgb, frame.width, frame.height, ExtendedColorType::Rgb8)
        .map_err(io::Error::other)?;
    Ok(jpeg)
}

fn serialize_frame(frame: &CameraFrame, jpeg: &[u8]) -> io::Result<Vec<u8>> {
    let header = FrameHeader {
        schema: "bv.camera_frame",
        version: 1,
        sequence: frame.sequence,
        sim_stream_id: &frame.sim_stream_id,
        sim_sequence: frame.sim_sequence,
        sim_time_ns: frame.sim_time_ns,
        camera_id: &frame.camera_id,
        camera_model: "pinhole",
        fx: frame.intrinsics.fx,
        fy: frame.intrinsics.fy,
        cx: frame.intrinsics.cx,
        cy: frame.intrinsics.cy,
        width: frame.width,
        height: frame.height,
        encoding: "jpeg",
        data_length: jpeg.len(),
    };
    let mut payload = serde_json::to_vec(&header).map_err(io::Error::other)?;
    payload.push(b'\n');
    payload.extend_from_slice(jpeg);
    Ok(payload)
}

#[cfg(test)]
mod tests {
    use std::{
        io::{BufRead, BufReader, Read},
        sync::Arc,
        time::Instant,
    };

    use super::*;

    fn frame() -> CameraFrame {
        frame_with_sequence(5)
    }

    fn frame_with_sequence(sequence: u64) -> CameraFrame {
        CameraFrame {
            sequence,
            sim_stream_id: "sim-test-stream".to_owned(),
            sim_sequence: sequence + 7,
            sim_time_ns: sequence * 10,
            camera_id: "camera/test".to_owned(),
            intrinsics: crate::camera::PinholeIntrinsics {
                width: 2,
                height: 1,
                fx: 1.0,
                fy: 1.0,
                cx: 1.0,
                cy: 0.5,
            },
            width: 2,
            height: 1,
            rgba8: Arc::from([255, 0, 0, 255, 0, 255, 0, 255]),
        }
    }

    fn wait_for_listener(publisher: &CameraFramePublisher) -> String {
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            match publisher.status() {
                FrameServerStatus::Listening { address, .. } => return address,
                FrameServerStatus::Failed(error) => panic!("frame server failed: {error}"),
                _ if Instant::now() < deadline => thread::sleep(Duration::from_millis(5)),
                _ => panic!("frame server did not start"),
            }
        }
    }

    fn wait_for_consumers(publisher: &CameraFramePublisher, expected: usize) {
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            match publisher.status() {
                FrameServerStatus::Listening { consumers, .. } if consumers == expected => return,
                FrameServerStatus::Failed(error) => panic!("frame server failed: {error}"),
                _ if Instant::now() < deadline => thread::sleep(Duration::from_millis(5)),
                _ => panic!("frame server did not reach {expected} consumers"),
            }
        }
    }

    fn connect(address: &str) -> BufReader<TcpStream> {
        let stream = TcpStream::connect(address).unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        BufReader::new(stream)
    }

    fn read_frame(reader: &mut BufReader<TcpStream>) -> (serde_json::Value, Vec<u8>) {
        let mut header = String::new();
        reader.read_line(&mut header).unwrap();
        let metadata: serde_json::Value = serde_json::from_str(&header).unwrap();
        let mut jpeg = vec![0; metadata["data_length"].as_u64().unwrap() as usize];
        reader.read_exact(&mut jpeg).unwrap();
        (metadata, jpeg)
    }

    #[test]
    fn encodes_valid_jpeg_markers() {
        let jpeg = encode_jpeg(&frame(), 90).unwrap();

        assert_eq!(&jpeg[..2], &[0xff, 0xd8]);
        assert_eq!(&jpeg[jpeg.len() - 2..], &[0xff, 0xd9]);
    }

    #[test]
    fn writes_json_header_before_exact_payload() {
        let jpeg = encode_jpeg(&frame(), 90).unwrap();
        let payload = serialize_frame(&frame(), &jpeg).unwrap();
        let newline = payload.iter().position(|byte| *byte == b'\n').unwrap();
        let header: serde_json::Value = serde_json::from_slice(&payload[..newline]).unwrap();

        assert_eq!(header["schema"], "bv.camera_frame");
        assert_eq!(header["sim_stream_id"], "sim-test-stream");
        assert_eq!(header["sim_time_ns"], 50);
        assert_eq!(header["camera_model"], "pinhole");
        assert_eq!(header["fx"], 1.0);
        assert_eq!(header["fy"], 1.0);
        assert_eq!(header["cx"], 1.0);
        assert_eq!(header["cy"], 0.5);
        assert_eq!(header["data_length"], jpeg.len());
        assert_eq!(&payload[newline + 1..], jpeg);
    }

    #[test]
    fn rejects_malformed_rgba_frame() {
        let mut malformed = frame();
        malformed.rgba8 = Arc::from([0_u8; 4]);

        assert_eq!(
            encode_jpeg(&malformed, 90).unwrap_err().kind(),
            ErrorKind::InvalidData
        );
    }

    #[test]
    fn broadcasts_one_encoded_frame_to_multiple_consumers() {
        let publisher = CameraFramePublisher::spawn("127.0.0.1:0".to_owned(), 90);
        let address = wait_for_listener(&publisher);
        let mut first = connect(&address);
        let mut second = connect(&address);
        wait_for_consumers(&publisher, 2);

        publisher.publish(frame());
        let (first_header, first_jpeg) = read_frame(&mut first);
        let (second_header, second_jpeg) = read_frame(&mut second);

        assert_eq!(first_header, second_header);
        assert_eq!(first_jpeg, second_jpeg);
    }

    #[test]
    fn sends_only_the_latest_frame_waiting_for_a_consumer() {
        let publisher = CameraFramePublisher::spawn("127.0.0.1:0".to_owned(), 90);
        let address = wait_for_listener(&publisher);
        publisher.publish(frame_with_sequence(1));
        publisher.publish(frame_with_sequence(2));

        let mut consumer = connect(&address);
        wait_for_consumers(&publisher, 1);
        let (header, _) = read_frame(&mut consumer);

        assert_eq!(header["sequence"], 2);
        assert_eq!(header["sim_sequence"], 9);
        assert_eq!(header["sim_time_ns"], 20);
    }

    #[test]
    fn accepts_a_new_consumer_after_disconnect() {
        let publisher = CameraFramePublisher::spawn("127.0.0.1:0".to_owned(), 90);
        let address = wait_for_listener(&publisher);
        let mut first = connect(&address);
        wait_for_consumers(&publisher, 1);
        publisher.publish(frame_with_sequence(1));
        read_frame(&mut first);

        first.get_ref().shutdown(std::net::Shutdown::Both).unwrap();
        drop(first);
        let deadline = Instant::now() + Duration::from_secs(2);
        let mut sequence = 2;
        while consumer_count(&publisher.shared) != 0 && Instant::now() < deadline {
            publisher.publish(frame_with_sequence(sequence));
            sequence += 1;
            thread::sleep(Duration::from_millis(10));
        }
        assert_eq!(consumer_count(&publisher.shared), 0);

        let mut second = connect(&address);
        wait_for_consumers(&publisher, 1);
        publisher.publish(frame_with_sequence(sequence));
        let (header, _) = read_frame(&mut second);

        assert_eq!(header["sequence"], sequence);
    }
}
