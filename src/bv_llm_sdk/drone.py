"""One long-lived MAVLink link to PX4, shared by every tool.

Every tool in this package takes a ``Drone`` as its first argument::

    from bv_llm_sdk import connect, takeoff, move, land

    with connect() as drone:
        takeoff(drone, 10)
        move(drone, north_m=20)
        land(drone)

A ``Drone`` owns the socket and two background threads: one keeps a
heartbeat going so PX4 never drops us, and one reads every incoming message
into a cache. Tools never call ``recv`` themselves. They ask the cache for the
latest message of a type (``drone.latest``), wait for a fresh one
(``drone.wait_for``), or send a command and wait for its ack
(``drone.command_long`` / ``drone.command_int``). Because only one thread ever
reads the socket, two tools can never steal each other's messages.

Four things about this link are not obvious. Getting any of them wrong fails
in a way that looks like "PX4 is broken" rather than like a bug here.

1. We must speak first. PX4 runs in Docker and only 18570/udp is published
   inbound, so PX4 cannot reach the host until it learns our address from a
   packet we send. Hence ``connect`` heartbeats until PX4 answers, rather
   than mavutil's wait_heartbeat(), which listens without ever sending.

2. Every send needs an explicit destination. A udpin socket writes back to
   whatever address it last heard from, and that is unset until telemetry
   arrives, so conn.mav.*_send() would drop the first packet on the floor.
   ``Drone.send`` always uses conn.port.sendto(..., px4_addr).

3. Windows turns an ICMP port-unreachable from an earlier send into a
   ConnectionResetError on the *next* recv. On a connectionless socket that
   only means nobody was listening yet, so the reader ignores it.

4. The local port must be 14550. PX4 SITL's GCS instance transmits to that
   port and latches the remote it learned, so binding any other port hears
   nothing back. It also means only one ``Drone`` per machine at a time.
"""

from __future__ import annotations

import collections
import os
import threading
import time
from typing import Any

# PX4 speaks MAVLink 2 and the "common" dialect. pymavlink otherwise defaults
# to MAVLink 1 and ArduPilot's dialect.
os.environ.setdefault("MAVLINK20", "1")
from pymavlink import mavutil  # noqa: E402

mavutil.set_dialect("common")

# PX4 SITL's GCS MAVLink instance. Not 14580: that one runs "-m onboard" and
# expects a single long-lived client such as MAVROS.
DEFAULT_PX4_ADDR = ("127.0.0.1", 18570)

# Always this exact port, never an ephemeral one. See note 4 above.
LOCAL_PORT = 14550

# Who we are on the MAVLink network: a ground station. PX4 checks these, for
# example to decide who may steer the gimbal, so they must not be 0.
GCS_SYSTEM_ID = 255
GCS_COMPONENT_ID = 190  # MAV_COMP_ID_MISSIONPLANNER

mav = mavutil.mavlink

# PX4 supports this, but recent pymavlink releases dropped it from every
# dialect, so it is defined here.
MAV_CMD_DO_ORBIT = 34


class DroneError(RuntimeError):
    """A tool could not do what it was asked. The message says why."""


class Drone:
    """A connected vehicle. Create one with ``connect()``, not directly."""

    def __init__(
        self,
        px4_addr: tuple[str, int] = DEFAULT_PX4_ADDR,
        camera_addr: tuple[str, int] = ("127.0.0.1", 7002),
    ):
        self.px4_addr = px4_addr
        self.camera_addr = camera_addr
        self._camera = None
        self.conn = mavutil.mavlink_connection(
            f"udpin:0.0.0.0:{LOCAL_PORT}",
            source_system=GCS_SYSTEM_ID,
            source_component=GCS_COMPONENT_ID,
        )
        # Ground speed for moves, in m/s. None uses PX4's default cruise speed.
        self.cruise_speed_m_s: float | None = None
        # Rough flight time on a full battery, for estimates (PX4 reports only
        # charge, not consumption). ~20 min suits an x500-class quadcopter.
        self.full_battery_endurance_min: float = 20.0
        # Share of the battery to keep in reserve when judging what is safe.
        self.battery_reserve_percent: float = 25.0
        # Toolkit-side geofence: no move may end further than this from home.
        self.max_distance_from_home_m: float = 500.0
        # Named places, {name: (latitude, longitude, altitude_m or None)}.
        # Filled by save_location and automatically by detections.
        self.places: dict[str, tuple[float, float, float | None]] = {}

        self._send_lock = threading.Lock()
        self._cond = threading.Condition()
        # Latest message of each type, and the time it arrived.
        self._latest: dict[str, tuple[float, Any]] = {}
        self._acks: collections.deque = collections.deque(maxlen=50)
        self._status_texts: collections.deque = collections.deque(maxlen=20)
        self._stop = threading.Event()
        self._threads = [
            threading.Thread(target=self._read_loop, daemon=True),
            threading.Thread(target=self._heartbeat_loop, daemon=True),
        ]

    # --- lifecycle --------------------------------------------------------

    def _start(self, timeout_s: float) -> None:
        for thread in self._threads:
            thread.start()
        # Wait on target_system rather than on any heartbeat: pymavlink
        # latches it from the first *vehicle* heartbeat and ignores
        # heartbeats from other ground stations, so a running QGroundControl
        # cannot be mistaken for the drone.
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self.conn.target_system != 0 and self.latest("HEARTBEAT"):
                break
            time.sleep(0.1)
        else:
            self.close()
            raise DroneError(
                f"no heartbeat from PX4 at {self.px4_addr[0]}:{self.px4_addr[1]} "
                f"within {timeout_s}s. Is the simulator running?"
            )
        # Ask for the telemetry the tools rely on at a useful rate. PX4's GCS
        # stream already sends most of these; asking again is harmless.
        for msg_id, hz in (
            (mav.MAVLINK_MSG_ID_GLOBAL_POSITION_INT, 10),
            (mav.MAVLINK_MSG_ID_EXTENDED_SYS_STATE, 2),
            (mav.MAVLINK_MSG_ID_HOME_POSITION, 1),
        ):
            self.send(
                self.conn.mav.command_long_encode(
                    self.target_system, self.target_component,
                    mav.MAV_CMD_SET_MESSAGE_INTERVAL, 0,
                    msg_id, 1e6 / hz, 0, 0, 0, 0, 0,
                )
            )

    @property
    def camera(self):
        """The drone's camera (a ``camera.Camera``), connected on first use."""
        if self._camera is None:
            from .camera import Camera

            self._camera = Camera(self, self.camera_addr)
        return self._camera

    def close(self) -> None:
        """Stop the background threads and release the socket."""
        if self._camera is not None:
            self._camera.close()
        self._stop.set()
        for thread in self._threads:
            if thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=2.0)
        self.conn.close()

    def __enter__(self) -> Drone:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- background threads ----------------------------------------------

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            try:
                msg = self.conn.recv_match(blocking=True, timeout=0.5)
            except ConnectionResetError:
                time.sleep(0.1)  # see note 3
                continue
            except OSError:
                if self._stop.is_set():
                    return
                time.sleep(0.1)
                continue
            if msg is None:
                continue
            msg_type = msg.get_type()
            if msg_type == "BAD_DATA":
                continue
            # Only trust telemetry from the vehicle, not other GCSes.
            if self.conn.target_system and msg.get_srcSystem() != self.conn.target_system:
                continue
            if msg_type == "HEARTBEAT" and msg.type == mav.MAV_TYPE_GCS:
                continue
            with self._cond:
                self._latest[msg_type] = (time.time(), msg)
                if msg_type == "COMMAND_ACK":
                    self._acks.append(msg)
                elif msg_type == "STATUSTEXT":
                    self._add_status_text(msg)
                self._cond.notify_all()

    def _add_status_text(self, msg: Any) -> None:
        # MAVLink 2 splits long texts into 50-character chunks that share a
        # non-zero id; glue them back together. Caller holds self._cond.
        text = msg.text.rstrip("\x00")
        last = self._status_texts[-1] if self._status_texts else None
        if msg.id and last is not None and last[2] == msg.id:
            self._status_texts[-1] = (last[0], last[1] + text, msg.id)
        else:
            self._status_texts.append((time.time(), text, msg.id))

    def _heartbeat_loop(self) -> None:
        # PX4 drops a ground station that goes quiet, so keep talking.
        while not self._stop.is_set():
            try:
                self.send(
                    self.conn.mav.heartbeat_encode(
                        mav.MAV_TYPE_GCS, mav.MAV_AUTOPILOT_INVALID, 0, 0, 0
                    )
                )
            except OSError:
                pass
            self._stop.wait(1.0)

    # --- sending ----------------------------------------------------------

    @property
    def target_system(self) -> int:
        return self.conn.target_system

    @property
    def target_component(self) -> int:
        # pymavlink never learns the component id, leaving it 0 (broadcast).
        # Address the autopilot explicitly.
        return self.conn.target_component or mav.MAV_COMP_ID_AUTOPILOT1

    def send(self, msg: Any) -> None:
        """Send an encoded MAVLink message to PX4 (see note 2)."""
        with self._send_lock:
            self.conn.port.sendto(msg.pack(self.conn.mav), self.px4_addr)

    def command_long(
        self, command: int, *params: float, timeout_s: float = 5.0, retries: int = 2
    ) -> None:
        """Send a COMMAND_LONG and block until PX4 accepts it.

        ``params`` are param1..param7; missing ones are sent as 0.

        Raises:
            DroneError: PX4 rejected the command or never acknowledged it.
        """
        p = list(params) + [0.0] * (7 - len(params))

        def encode(confirmation: int) -> Any:
            return self.conn.mav.command_long_encode(
                self.target_system, self.target_component, command,
                confirmation, *p,
            )

        self._send_and_wait_ack(command, encode, timeout_s, retries)

    def command_int(
        self,
        command: int,
        param1: float = 0.0,
        param2: float = 0.0,
        param3: float = 0.0,
        param4: float = 0.0,
        lat: float | None = None,
        lon: float | None = None,
        alt_amsl_m: float = float("nan"),
        timeout_s: float = 5.0,
        retries: int = 2,
    ) -> None:
        """Send a COMMAND_INT and block until PX4 accepts it.

        Use this, not ``command_long``, for anything with a latitude and
        longitude: COMMAND_LONG carries them as float32, which rounds them to
        roughly half a metre. COMMAND_INT carries them as degrees * 1e7.
        """
        x = round(lat * 1e7) if lat is not None else 2**31 - 1
        y = round(lon * 1e7) if lon is not None else 2**31 - 1

        def encode(_confirmation: int) -> Any:
            return self.conn.mav.command_int_encode(
                self.target_system, self.target_component,
                mav.MAV_FRAME_GLOBAL, command, 0, 0,
                param1, param2, param3, param4, x, y, alt_amsl_m,
            )

        self._send_and_wait_ack(command, encode, timeout_s, retries)

    def _send_and_wait_ack(self, command, encode, timeout_s, retries) -> None:
        entry = mav.enums["MAV_CMD"].get(command)
        name = entry.name if entry else f"MAV_CMD {command}"
        sent_at = time.time()
        for attempt in range(retries + 1):
            with self._cond:
                self._acks.clear()
            self.send(encode(attempt))
            deadline = time.time() + timeout_s / (retries + 1)
            with self._cond:
                while time.time() < deadline:
                    ack = next((a for a in self._acks if a.command == command), None)
                    if ack is None:
                        self._cond.wait(timeout=max(0.0, deadline - time.time()))
                        continue
                    self._acks.remove(ack)
                    if ack.result == mav.MAV_RESULT_ACCEPTED:
                        return
                    # IN_PROGRESS is not a verdict, so keep waiting.
                    if ack.result == mav.MAV_RESULT_IN_PROGRESS:
                        deadline = max(deadline, time.time() + timeout_s)
                        continue
                    result = mav.enums["MAV_RESULT"][ack.result].name
                    # PX4 explains a refusal in a STATUSTEXT that usually
                    # arrives just after the ack, so give it a moment.
                    self._cond.wait(timeout=0.5)
                    reasons = self.status_texts(since=sent_at)
                    why = f" PX4 said: {'; '.join(reasons)}" if reasons else ""
                    raise DroneError(f"{name} rejected ({result}).{why}")
        raise DroneError(f"{name} was never acknowledged within {timeout_s}s")

    # --- receiving --------------------------------------------------------

    def latest(self, msg_type: str, max_age_s: float = 3.0) -> Any | None:
        """The newest message of this type, or None if none is that fresh."""
        with self._cond:
            entry = self._latest.get(msg_type)
        if entry is None or time.time() - entry[0] > max_age_s:
            return None
        return entry[1]

    def wait_for(self, msg_type: str, timeout_s: float = 5.0) -> Any:
        """Block until a message of this type arrives *after* this call.

        Raises:
            DroneError: none arrived in time.
        """
        start = time.time()
        deadline = start + timeout_s
        with self._cond:
            while time.time() < deadline:
                entry = self._latest.get(msg_type)
                if entry is not None and entry[0] >= start:
                    return entry[1]
                self._cond.wait(timeout=max(0.0, deadline - time.time()))
        raise DroneError(f"no {msg_type} from PX4 within {timeout_s}s")

    def require(self, msg_type: str, timeout_s: float = 5.0) -> Any:
        """The latest message of this type, waiting for one if none is fresh."""
        return self.latest(msg_type) or self.wait_for(msg_type, timeout_s)

    def status_texts(self, since: float = 0.0) -> list[str]:
        """Recent STATUSTEXT messages from PX4 (warnings, failure reasons)."""
        with self._cond:
            return [text.strip() for t, text, _ in self._status_texts if t >= since]

    # --- vehicle state shortcuts -------------------------------------------

    @property
    def armed(self) -> bool:
        hb = self.require("HEARTBEAT")
        return bool(hb.base_mode & mav.MAV_MODE_FLAG_SAFETY_ARMED)

    @property
    def flight_mode(self) -> str:
        """PX4 flight mode, e.g. "LOITER", "TAKEOFF", "RTL", "LAND", "POSCTL"."""
        self.require("HEARTBEAT")
        return self.conn.flightmode

    @property
    def landed_state(self) -> str:
        """"ON_GROUND", "TAKEOFF", "IN_AIR", "LANDING" or "UNDEFINED"."""
        ext = self.latest("EXTENDED_SYS_STATE")
        if ext is None:
            return "UNDEFINED"
        return mav.enums["MAV_LANDED_STATE"][ext.landed_state].name.removeprefix(
            "MAV_LANDED_STATE_"
        )

    def wait_armed(self, armed: bool, timeout_s: float = 5.0) -> None:
        """Block until a fresh heartbeat shows the motors armed (or not).

        ``armed`` reads the latest heartbeat, which can predate a command
        PX4 just accepted. Call this after arming or disarming.
        """
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            hb = self.wait_for("HEARTBEAT", timeout_s=max(0.1, deadline - time.time()))
            if bool(hb.base_mode & mav.MAV_MODE_FLAG_SAFETY_ARMED) == armed:
                return
        raise DroneError(f"PX4 accepted the command but never reported {'armed' if armed else 'disarmed'}.")

    def sleep(self, seconds: float) -> None:
        """Sleep, but wake early if the link is closed."""
        self._stop.wait(seconds)


def connect(
    host: str = DEFAULT_PX4_ADDR[0],
    port: int = DEFAULT_PX4_ADDR[1],
    timeout_s: float = 15.0,
    camera_port: int = 7002,
) -> Drone:
    """Open a link to PX4 and wait until it answers.

    Args:
        host: Where PX4's GCS MAVLink port is reachable.
        port: That port (18570 for the bv_bevy simulator).
        timeout_s: How long to wait for PX4's first heartbeat.
        camera_port: Where Bevy streams the drone camera (on ``host``).

    Raises:
        DroneError: PX4 did not answer in time.
    """
    drone = Drone((host, port), (host, camera_port))
    drone._start(timeout_s)
    return drone
