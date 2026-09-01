# BV simulation state protocol v1

`BVSimState` is the ROS-independent state contract between an authoritative
simulator and rendering or inspection clients. Gazebo is the first producer;
Bevy is the first consumer. Neither product is named in the wire schema.

The prototype transport sends one UTF-8 JSON object per line. Each object is a
complete snapshot of the configured dynamic-entity set at one simulation time.

```json
{
  "schema": "bv.sim_state",
  "version": 1,
  "stream_id": "bridge-process-uuid",
  "sequence": 42,
  "sim_time_ns": 1250000000,
  "frame_id": "gazebo_world",
  "entities": [
    {
      "id": "vehicle/x500_gimbal_0",
      "kind": "vehicle",
      "position_m": [5.0, 2.0, 10.0],
      "orientation_xyzw": [0.0, 0.0, 0.0, 1.0]
    }
  ]
}
```

## Snapshot rules

- `schema` must be `bv.sim_state` and `version` must be `1`.
- `stream_id` identifies one bridge process/run and must be non-empty.
- `sequence` increases within a stream.
- `sim_time_ns` comes from the simulator, never wall-clock time.
- `frame_id` is `gazebo_world`; all entity poses are world-space poses.
- `id` is stable and unique within the snapshot.
- `kind` is a non-empty classification such as `vehicle`, `camera`, or
  `dynamic_object`. Consumers must select entities by ID, not array position.
- Position is in meters.
- Quaternion order is explicitly `[x, y, z, w]`. Receivers normalize valid
  quaternions and reject zero-length or non-finite values.
- Unknown fields are rejected in v1 so producer/consumer drift fails visibly.
- A new `stream_id`, a decreasing sequence, or decreasing simulation time is a
  stream reset. Consumers must discard any interpolation history.

Entity omission means the entity is no longer part of the configured complete
snapshot. Static world geometry is loaded independently and is not repeated in
this stream.

Rendered `CameraFrame` metadata copies this snapshot's `stream_id`, `sequence`,
and `sim_time_ns` as `sim_stream_id`, `sim_sequence`, and `sim_time_ns`. That
tuple identifies the exact authoritative state used for a frame even if a
simulator restarts or a deterministic recording loops.

## Recording format

Deterministic state fixtures use the `.bvsim` extension. A recording starts
with the eight-byte magic value `BVSIM\0\1\n`, followed by the exact v1
snapshots using the same newline-delimited JSON encoding as the live stream.
Snapshot sequence numbers, simulation timestamps, stream identity, and poses
are preserved. Recording order must be strictly increasing within one stream.

Capture state from any compatible producer:

```bash
python3 tools/record_sim_state.py flight.bvsim --snapshots 300
```

Replay it without Gazebo:

```bash
python3 tools/replay_sim_state.py flight.bvsim --port 7003
BV_SIM_ENDPOINT=127.0.0.1:7003 cargo run
```

Replay timing follows consecutive `sim_time_ns` values. `--speed 2` runs at
twice recorded speed. `--loop` restarts the fixture after its last snapshot;
the sequence and time decrease at that boundary, which v1 consumers handle as
a stream reset. Every TCP consumer receives an independent replay beginning at
the first snapshot, so renderers, inspectors, and tests can run concurrently.

## Gazebo-to-Bevy basis conversion

The v1 source frame uses Gazebo world axes. The Bevy renderer maps them as:

```text
Gazebo +X -> Bevy -Z
Gazebo +Y -> Bevy -X
Gazebo +Z -> Bevy +Y
```

The basis matrix is:

```text
C = [ 0 -1  0 ]
    [ 0  0  1 ]
    [-1  0  0 ]
```

Positions use `p_bevy = C p_gazebo`. Orientations use the explicit basis
change `R_bevy = C R_gazebo C^T`; quaternion elements are not manually
reordered.
