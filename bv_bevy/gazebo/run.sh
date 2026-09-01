#!/usr/bin/env bash
set -euo pipefail

world="${BV_GZ_WORLD:-/opt/bv/worlds/minimal.sdf}"
config="${BV_BRIDGE_CONFIG:-/opt/bv/config/minimal.json}"

gz sim --force-version 8 -s -r "$world" &
gazebo_pid=$!

bv_sim_bridge "$config" &
bridge_pid=$!

terminate() {
    kill "$gazebo_pid" "$bridge_pid" 2>/dev/null || true
    wait "$gazebo_pid" "$bridge_pid" 2>/dev/null || true
}

trap terminate EXIT INT TERM

set +e
wait -n "$gazebo_pid" "$bridge_pid"
status=$?
set -e

exit "$status"
