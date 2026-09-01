#!/usr/bin/env bash
set -euo pipefail

world_name="${BV_PX4_WORLD:-bv_mission}"
world="/opt/px4/worlds/${world_name}.sdf"
config="${BV_BRIDGE_CONFIG:-/opt/bv/config/px4_x500_gimbal.json}"

export GZ_IP="${GZ_IP:-127.0.0.1}"
export GZ_SIM_RESOURCE_PATH="/opt/px4/models:/opt/px4/worlds"
export GZ_SIM_SYSTEM_PLUGIN_PATH="/opt/px4/plugins"
export GZ_SIM_SERVER_CONFIG_PATH="/opt/px4/server.config"
export PX4_GZ_MODELS=/opt/px4/models
export PX4_GZ_WORLDS=/opt/px4/worlds
export PX4_GZ_PLUGINS=/opt/px4/plugins
export PX4_GZ_WORLD="$world_name"
export PX4_GZ_STANDALONE=1
export PX4_GZ_NO_FOLLOW=1
export PX4_SIM_MODEL=gz_x500_gimbal
export PX4_HOME_LAT="${PX4_HOME_LAT:-38.3876112}"
export PX4_HOME_LON="${PX4_HOME_LON:--76.4190542}"
export PX4_HOME_ALT="${PX4_HOME_ALT:-0.0}"

configure_px4() {
    local nav_dll_action="${BV_PX4_NAV_DLL_ACT:-0}"

    for _ in {1..300}; do
        if (
            cd /opt/px4/rootfs
            /opt/px4/bin/px4-commander status >/dev/null 2>&1 || exit 1
            /opt/px4/bin/px4-param set NAV_DLL_ACT "$nav_dll_action" >/dev/null 2>&1 || exit 1
            /opt/px4/bin/px4-param compare NAV_DLL_ACT "$nav_dll_action" >/dev/null 2>&1 || exit 1
            /opt/px4/bin/px4-param save >/dev/null 2>&1
        ); then
            return 0
        fi
        sleep 0.1
    done

    echo "Failed to configure PX4 simulation parameters" >&2
    return 1
}

gz sim --force-version 8 -s -r "$world" &
gazebo_pid=$!

bv_sim_bridge "$config" &
bridge_pid=$!

(
    cd /opt/px4/rootfs
    exec /opt/px4/bin/px4 -d
) &
px4_pid=$!

configure_px4 &

terminate() {
    kill "$px4_pid" "$gazebo_pid" "$bridge_pid" 2>/dev/null || true
    wait "$px4_pid" "$gazebo_pid" "$bridge_pid" 2>/dev/null || true
}

trap terminate EXIT INT TERM

set +e
wait -n "$px4_pid" "$gazebo_pid" "$bridge_pid"
status=$?
set -e

exit "$status"
