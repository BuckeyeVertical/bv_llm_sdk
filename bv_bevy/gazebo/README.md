# Minimal Gazebo integration

This container runs Gazebo Harmonic headlessly with a standalone,
ROS-independent `bv_sim_bridge`. Its runtime installs only the Gazebo server
CLI and simulation system plugins, not the GUI or ROS stack.

```bash
docker compose -f gazebo/compose.yaml up --build
```

The container publishes `BVSimState` on TCP port 7001. Native Bevy connects to
`127.0.0.1:7001` by default. Each TCP consumer receives the latest snapshots
on its own connection; a slow or disconnected consumer cannot hold up the
simulation or other consumers.

The included world moves a minimal `x500` placeholder through Gazebo's
`VelocityControl` system. `PosePublisher` emits model, link, and sensor poses
at 30 Hz. The bridge resolves their parent frames before emitting world-space
poses. Entity mappings are configured in `config/minimal.json`.

## PX4 SITL integration

Build and run the flight-stack integration independently of the development
fixture:

```bash
docker compose -f gazebo/compose.px4.yaml up --build
```

The image reproducibly builds the PX4 commit and Buckeye Vertical Gazebo model
commit pinned in `Dockerfile`. The pinned model commit:

- publishes the `x500_gimbal` model, link, and sensor pose graph;
- derives gimbal command and IMU topics from the active world and model names.

A separate tracked world patch replaces network-hosted mission objects with
local physics proxies.

The final container starts the Gazebo server with PX4's sensor plugin
configuration, the standalone state bridge, and PX4 SITL. It exposes state on
TCP 7001 and the usual PX4 UDP endpoints on 14580 and 18570.

`px4/server.config` keeps flight-critical physics and sensor systems but omits
Gazebo's rendering, optical-flow, and video systems. Bevy provides the camera
frames, so those heavier Gazebo rendering dependencies are neither loaded nor
installed.

The proxy models under `px4/models` are not visual assets. Their only purpose
is deterministic collision geometry and stable target poses; Bevy is the
visual world and simulated camera authority.
