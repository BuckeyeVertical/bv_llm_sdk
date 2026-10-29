"""One MAVLink link to PX4 shared by every tool.

A Drone owns the socket and two background threads. One sends heartbeats so PX4 keeps
the connection and the other reads every incoming message into a cache. Tools read from
the cache or wait for new messages and never read the socket directly. That way no tool
can take a message another tool is waiting for.
"""

from __future__ import annotations

import collections
import os
import threading
import time
from typing import Any

from ...registry import ToolError

# PX4 uses MAVLink 2 and the common dialect. pymavlink defaults to MAVLink 1 and the
# ArduPilot dialect.
os.environ.setdefault("MAVLINK20", "1")
from pymavlink import mavutil  # noqa: E402

mavutil.set_dialect("common")

# PX4 SITL ground station port. Port 14580 is the onboard instance and expects a single
# client like MAVROS.
DEFAULT_PX4_ADDR = ("127.0.0.1", 18570)

# PX4 only sends to this port so it must be exact. Only one Drone can run per machine.
LOCAL_PORT = 14550

# Our MAVLink ids as a ground station. PX4 checks them so they cannot be 0.
GCS_SYSTEM_ID = 255
GCS_COMPONENT_ID = 190  # MAV_COMP_ID_MISSIONPLANNER

mav = mavutil.mavlink

# PX4 supports this but newer pymavlink versions removed it so it is defined here.
MAV_CMD_DO_ORBIT = 34


class DroneError(ToolError):
    """Raised when a drone tool cannot do what it was asked."""


class Drone:
    """A connected drone. Create one with connect."""

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
        # Ground speed for moves in meters per second. None uses the PX4 default.
        self.cruise_speed_m_s: float | None = None
        # Rough flight time on a full battery. Used for estimates since PX4 only reports
        # charge.
        self.full_battery_endurance_min: float = 20.0
        # Share of the battery to keep in reserve.
        self.battery_reserve_percent: float = 25.0
        # No move may end farther than this from home.
        self.max_distance_from_home_m: float = 500.0
        # Saved places by name. Filled by save_location and by detections.
        self.places: dict[str, tuple[float, float, float | None]] = {}

        self._send_lock = threading.Lock()
        self._cond = threading.Condition()
        # Latest message of each type and when it arrived.
        self._latest: dict[str, tuple[float, Any]] = {}
        self._acks: collections.deque = collections.deque(maxlen=50)
        self._status_texts: collections.deque = collections.deque(maxlen=20)
        self._stop = threading.Event()
        self._threads = [
            threading.Thread(target=self._read_loop, daemon=True),
            threading.Thread(target=self._heartbeat_loop, daemon=True),
        ]

    # Lifecycle.

    def _start(self, timeout_s: float) -> None:
        for thread in self._threads:
            thread.start()
        # Wait for a heartbeat from the vehicle so another ground station is not
        # mistaken for the drone.
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
        # Request the telemetry the tools need at a useful rate.
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
        """The drone camera. Connects on first use."""
        if self._camera is None:
            from .camera import Camera

            self._camera = Camera(self, self.camera_addr)
        return self._camera

    def close(self) -> None:
        """Stop the background threads and close the socket."""
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

    # Background threads.

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            try:
                msg = self.conn.recv_match(blocking=True, timeout=0.5)
            except ConnectionResetError:
                # Windows reports this when nobody was listening yet. Safe to ignore.
                time.sleep(0.1)
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
            # Only trust telemetry from the vehicle.
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
        # Long status texts arrive in chunks. Join them back together. Caller holds
        # self._cond.
        text = msg.text.rstrip("\x00")
        last = self._status_texts[-1] if self._status_texts else None
        if msg.id and last is not None and last[2] == msg.id:
            self._status_texts[-1] = (last[0], last[1] + text, msg.id)
        else:
            self._status_texts.append((time.time(), text, msg.id))

    def _heartbeat_loop(self) -> None:
        # PX4 drops a ground station that goes quiet.
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

    # Sending.

    @property
    def target_system(self) -> int:
        return self.conn.target_system

    @property
    def target_component(self) -> int:
        # pymavlink leaves the component id at 0. Address the autopilot directly.
        return self.conn.target_component or mav.MAV_COMP_ID_AUTOPILOT1

    def send(self, msg: Any) -> None:
        """Send a MAVLink message to PX4.

        Always sends to the PX4 address since the socket does not know it until
        telemetry arrives.
        """
        with self._send_lock:
            self.conn.port.sendto(msg.pack(self.conn.mav), self.px4_addr)

    def command_long(
        self, command: int, *params: float, timeout_s: float = 5.0, retries: int = 2
    ) -> None:
        """Send a COMMAND_LONG and wait until PX4 accepts it. Missing params are sent as 0.
        Raises DroneError if PX4 rejects the command or never answers.
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
        """Send a COMMAND_INT and wait until PX4 accepts it.

        Use this for anything with a latitude and longitude. COMMAND_LONG rounds them to
        about half a meter.
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
                    # Still in progress so keep waiting.
                    if ack.result == mav.MAV_RESULT_IN_PROGRESS:
                        deadline = max(deadline, time.time() + timeout_s)
                        continue
                    result = mav.enums["MAV_RESULT"][ack.result].name
                    # PX4 explains a refusal in a status text that arrives just after
                    # the ack.
                    self._cond.wait(timeout=0.5)
                    reasons = self.status_texts(since=sent_at)
                    why = f" PX4 said: {'; '.join(reasons)}" if reasons else ""
                    raise DroneError(f"{name} rejected ({result}).{why}")
        raise DroneError(f"{name} was never acknowledged within {timeout_s}s")

    # Receiving.

    def latest(self, msg_type: str, max_age_s: float = 3.0) -> Any | None:
        """Return the newest message of this type or None if none is fresh enough."""
        with self._cond:
            entry = self._latest.get(msg_type)
        if entry is None or time.time() - entry[0] > max_age_s:
            return None
        return entry[1]

    def wait_for(self, msg_type: str, timeout_s: float = 5.0) -> Any:
        """Wait for a message of this type that arrives after this call. Raises DroneError
        if none arrives in time.
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
        """Return the latest message of this type. Waits for one if none is fresh."""
        return self.latest(msg_type) or self.wait_for(msg_type, timeout_s)

    def status_texts(self, since: float = 0.0) -> list[str]:
        """Recent status texts from PX4 such as warnings and failure reasons."""
        with self._cond:
            return [text.strip() for t, text, _ in self._status_texts if t >= since]

    # Vehicle state.

    @property
    def armed(self) -> bool:
        hb = self.require("HEARTBEAT")
        return bool(hb.base_mode & mav.MAV_MODE_FLAG_SAFETY_ARMED)

    @property
    def flight_mode(self) -> str:
        """Current PX4 flight mode."""
        self.require("HEARTBEAT")
        return self.conn.flightmode

    @property
    def landed_state(self) -> str:
        """Whether the drone is on the ground or taking off or in the air or landing."""
        ext = self.latest("EXTENDED_SYS_STATE")
        if ext is None:
            return "UNDEFINED"
        return mav.enums["MAV_LANDED_STATE"][ext.landed_state].name.removeprefix(
            "MAV_LANDED_STATE_"
        )

    def wait_armed(self, armed: bool, timeout_s: float = 5.0) -> None:
        """Wait until a new heartbeat shows the motors armed or disarmed.

        Call this after arming or disarming since the last heartbeat may be older than
        the command.
        """
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            hb = self.wait_for("HEARTBEAT", timeout_s=max(0.1, deadline - time.time()))
            if bool(hb.base_mode & mav.MAV_MODE_FLAG_SAFETY_ARMED) == armed:
                return
        raise DroneError(f"PX4 accepted the command but never reported {'armed' if armed else 'disarmed'}.")

    def sleep(self, seconds: float) -> None:
        """Sleep but wake early if the link is closed."""
        self._stop.wait(seconds)


def connect(
    host: str = DEFAULT_PX4_ADDR[0],
    port: int = DEFAULT_PX4_ADDR[1],
    timeout_s: float = 15.0,
    camera_port: int = 7002,
) -> Drone:
    """Open a link to PX4 and wait until it answers.

    Sends heartbeats until PX4 replies since PX4 cannot reach us until we send first.
    Raises DroneError if PX4 does not answer in time.
    """
    drone = Drone((host, port), (host, camera_port))
    drone._start(timeout_s)
    return drone
