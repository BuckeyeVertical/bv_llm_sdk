# BV camera frame protocol v1

`CameraFrame` is the ROS-independent camera output contract. Bevy is the first
producer, but the protocol does not depend on Bevy or any autonomy framework.

The TCP stream contains a compact JSON header terminated by one newline,
followed immediately by exactly `data_length` binary bytes. The next header
begins immediately after that payload.

```json
{
  "schema": "bv.camera_frame",
  "version": 1,
  "sequence": 42,
  "sim_stream_id": "bridge-process-uuid",
  "sim_sequence": 180,
  "sim_time_ns": 1250000000,
  "camera_id": "camera/drone",
  "camera_model": "pinhole",
  "fx": 988.3916,
  "fy": 988.3916,
  "cx": 640.0,
  "cy": 480.0,
  "width": 1280,
  "height": 960,
  "encoding": "jpeg",
  "data_length": 184205
}
```

## Frame rules

- `sequence` increases for frames produced by one Bevy process. A `.bvcam`
  replay loop may restart it at the same simulation epoch boundary described
  below.
- `sim_stream_id` is copied from the applied `BVSimState` snapshot. New v1
  producers include it; consumers remain compatible with legacy v1 recordings
  where it is absent.
- `sim_sequence` identifies the exact applied simulation snapshot.
- `sim_time_ns` is copied from that snapshot and originates in Gazebo.
- `camera_id` matches the camera entity in `BVSimState`.
- New v1 producers include `camera_model: "pinhole"` and the exact `fx`, `fy`,
  `cx`, and `cy` values used to render the frame. Legacy v1 frames may omit the
  complete group. Focal lengths are in pixels.
- `width` and `height` describe the decoded image dimensions.
- Version 1 supports `jpeg` encoding.
- Sequence gaps are expected. Delivery is live and latest-only, not a durable
  frame log.
- Within a simulation stream, `sim_sequence` and `sim_time_ns` increase. A new
  `sim_stream_id`, or a paired decrease of sequence and time at a deterministic
  replay loop, starts a new simulation epoch. Camera `sequence` continues to
  increase across that boundary.
- The producer keeps at most one unprocessed capture. Replacing it never blocks
  Bevy's render loop.
- Each TCP connection is an independent consumer. A connection that cannot
  accept a complete frame within the write timeout is disconnected and may
  reconnect without affecting other consumers.

The default server is `127.0.0.1:7002`. `BV_CAMERA_BIND` changes the bind
address and `BV_CAMERA_JPEG_QUALITY` changes JPEG quality from 1 through 100.

## Recordings

The `.bvcam` recording format starts with the eight-byte magic value
`BVCAM\0\1\n`, followed by the same header-and-payload records used on the
wire. Frame headers and JPEG payloads are preserved exactly at the protocol
level. Replay pacing uses consecutive `sim_time_ns` values, so it is
independent of wall-clock time during capture. Simulation resets have no
invented delay; pacing resumes from the next timestamp in the new epoch.

Record and replay tools are framework-independent:

```bash
python3 tools/record_camera_stream.py flight.bvcam --frames 300
python3 tools/replay_camera_stream.py flight.bvcam --port 7003 --speed 1
python3 tools/verify_camera_stream.py --port 7003 --frames 5
```
