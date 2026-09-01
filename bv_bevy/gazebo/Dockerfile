FROM ubuntu:24.04@sha256:561618e2c15bf2397621dd04f96926663a3b5616c189cf7e38db7e82f5c538ea AS gazebo-base

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
        lsb-release \
    && curl --fail --silent --show-error \
        https://packages.osrfoundation.org/gazebo.gpg \
        --output /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] http://packages.osrfoundation.org/gazebo/ubuntu-stable $(lsb_release -cs) main" \
        > /etc/apt/sources.list.d/gazebo-stable.list \
    && apt-get update \
    && apt-get install --yes --no-install-recommends \
        gz-sim8-cli \
        gz-transport13-cli \
        libgz-physics7-dartsim \
        libgz-sim8-plugins \
    && rm -rf /var/lib/apt/lists/*

RUN plugin_dir="$(find /usr/lib -type d -path '*/gz-physics-7/engine-plugins' -print -quit)" \
    && test -n "$plugin_dir" \
    && ln --symbolic \
        libgz-physics7-dartsim-plugin.so.7 \
        "$plugin_dir/libgz-physics7-dartsim-plugin.so" \
    && ln --symbolic \
        libgz-physics7-dartsim-plugin.so \
        "$plugin_dir/libgz-physics-dartsim-plugin.so"

FROM gazebo-base AS bridge-build

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        build-essential \
        cmake \
        cppzmq-dev \
        libgz-msgs10-dev \
        libgz-transport13-dev \
        ninja-build \
        nlohmann-json3-dev \
        pkg-config \
        python3 \
    && rm -rf /var/lib/apt/lists/*

COPY bridge /src/bridge

RUN cmake -S /src/bridge -B /src/bridge/build -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /src/bridge/build \
    && ctest --test-dir /src/bridge/build --output-on-failure

FROM gazebo-base AS simulator

COPY --from=bridge-build /src/bridge/build/bv_sim_bridge /usr/local/bin/
COPY config /opt/bv/config
COPY worlds /opt/bv/worlds
COPY run.sh /usr/local/bin/bv-gazebo

RUN chmod +x /usr/local/bin/bv-gazebo

EXPOSE 7001

ENTRYPOINT ["/usr/local/bin/bv-gazebo"]

FROM gazebo-base AS px4-build

ARG PX4_COMMIT=7584f7567f6be0aaa677e34dd8aa0e61a0dcc34e

RUN apt-get update \
    && apt-get install --yes --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN git init /src/PX4-Autopilot \
    && git -C /src/PX4-Autopilot remote add origin \
        https://github.com/PX4/PX4-Autopilot.git \
    && git -C /src/PX4-Autopilot fetch --depth 1 origin "$PX4_COMMIT" \
    && git -C /src/PX4-Autopilot checkout --detach FETCH_HEAD \
    && git -C /src/PX4-Autopilot submodule update --init --recursive --depth 1

ARG PX4_GZ_MODELS_COMMIT=ccb6275d08c59e8fdb179e0de24e2bbd89469afa

RUN git -C /src/PX4-Autopilot/Tools/simulation/gz remote set-url origin \
        https://github.com/BuckeyeVertical/PX4-gazebo-models.git \
    && git -C /src/PX4-Autopilot/Tools/simulation/gz fetch --depth 1 \
        origin "$PX4_GZ_MODELS_COMMIT" \
    && git -C /src/PX4-Autopilot/Tools/simulation/gz checkout --detach FETCH_HEAD

RUN apt-get update \
    && apt-get install --yes --no-install-recommends sudo wget \
    && rm -rf /var/lib/apt/lists/*

RUN RUNS_IN_DOCKER=true \
        /src/PX4-Autopilot/Tools/setup/ubuntu.sh --no-nuttx

RUN git -C /src/PX4-Autopilot fetch --depth 1 origin tag v1.17.0-alpha1 \
    && git -C /src/PX4-Autopilot/platforms/nuttx/NuttX/nuttx \
        fetch --depth 1 origin tag nuttx-9.0.0

RUN GIT_SUBMODULES_ARE_EVIL=1 \
        make -C /src/PX4-Autopilot -j2 px4_sitl_default \
    && cmake -S /src/PX4-Autopilot/Tools/simulation/gz/plugins \
        -B /src/PX4-Autopilot/build/bv_gz_plugins \
        -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /src/PX4-Autopilot/build/bv_gz_plugins \
    && mkdir -p \
        /runtime/bin \
        /runtime/etc \
        /runtime/plugins \
        /runtime/rootfs \
    && cp -a /src/PX4-Autopilot/build/px4_sitl_default/bin/. /runtime/bin/ \
    && cp -a /src/PX4-Autopilot/build/px4_sitl_default/etc/. /runtime/etc/ \
    && cp /src/PX4-Autopilot/build/px4_sitl_default/src/modules/simulation/gz_plugins/*.so \
        /runtime/plugins/ \
    && cp /src/PX4-Autopilot/build/bv_gz_plugins/gimbal_stabilizer/*.so \
        /runtime/plugins/ \
    && cp -a /src/PX4-Autopilot/Tools/simulation/gz/models /runtime/ \
    && cp -a /src/PX4-Autopilot/Tools/simulation/gz/worlds /runtime/ \
    && strip /runtime/bin/px4 /runtime/plugins/*.so

FROM px4-build AS px4-runtime

COPY px4/models /runtime/models
COPY px4/server.config /runtime/server.config
COPY px4/bv_mission_offline.patch /tmp/

WORKDIR /runtime

RUN git apply /tmp/bv_mission_offline.patch

FROM simulator AS px4-sitl

COPY --from=px4-runtime /runtime /opt/px4
COPY px4/run.sh /usr/local/bin/bv-px4-sim

RUN chmod +x /usr/local/bin/bv-px4-sim \
    && ln --symbolic /opt/px4/etc /opt/px4/rootfs/etc

ENTRYPOINT ["/usr/local/bin/bv-px4-sim"]

FROM simulator AS default
