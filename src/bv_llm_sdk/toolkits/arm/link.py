from __future__ import annotations

import time

from ...registry import ToolError

BAUD = 115200
ARDUINO_USB_VENDORS = {0x2341, 0x2A03, 0x1A86, 0x0403}


class ArmError(ToolError):
    pass


def find_port() -> str:
    try:
        from serial.tools.list_ports import comports
    except ImportError as exc:
        raise ArmError("the arm needs pyserial. Run: uv sync --extra agent --extra arm") from exc
    ports = [port.device for port in comports() if port.vid in ARDUINO_USB_VENDORS]
    if not ports:
        raise ArmError("no Arduino found. Plug in the arm, or set BV_ARM_PORT to its port. "
                       "Set BV_ARM_SIM=1 for the fake arm.")
    if len(ports) > 1:
        raise ArmError(f"found more than one Arduino ({', '.join(ports)}). "
                       "Set BV_ARM_PORT to the arm's port.")
    return ports[0]


class Link:
    simulated = False

    def __init__(self, port: str):
        try:
            import serial
        except ImportError as exc:
            raise ArmError("the arm needs pyserial. Run: uv sync --extra arm") from exc
        self._serial_error = serial.SerialException
        try:
            self.ser = serial.Serial(port, BAUD, timeout=0.1)
        except serial.SerialException as exc:
            raise ArmError(f"could not open {port}: {exc}") from exc
        self._handshake()

    def _handshake(self) -> None:
        self.ser.reset_input_buffer()
        self.ser.write(b"P\n")
        lines = self._read_until(("ok", "ready"), timeout=8.0)
        if not any(line.startswith("pos") for line in lines):
            self.resync()

    def close(self) -> None:
        self.ser.close()

    def _read_until(self, enders: tuple[str, ...], timeout: float, strict: bool = False) -> list[str]:
        lines, warnings, end = [], [], time.time() + timeout
        while time.time() < end:
            raw = self.ser.readline()
            if not raw:
                continue
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            if line.startswith("err"):
                raise ArmError(line[4:])
            if line.startswith("warn"):
                warnings.append(line)
            if line in enders:
                if strict and any("clamped" in w or "was limp" in w for w in warnings):
                    raise ArmError(f"the Arduino overrode a move ({warnings[0][5:]}). Job stopped.")
                return lines
            lines.append(line)
        raise ArmError(f"no reply within {timeout:.0f} s (expected {'/'.join(enders)})")

    def send(self, line: str, timeout: float = 5.0, strict: bool = False) -> list[str]:
        try:
            self.ser.write((line + "\n").encode())
            return self._read_until(("ok",), timeout, strict)
        except self._serial_error as exc:
            raise ArmError(f"lost the serial connection: {exc}") from exc

    def resync(self) -> None:
        time.sleep(0.3)
        self.ser.reset_input_buffer()
        try:
            self.send("P", timeout=3.0)
        except ArmError:
            pass

    def status(self) -> tuple[list[float], str]:
        for line in self.send("P"):
            if line.startswith("pos"):
                parts = line.split()
                angles = [float(x) for x in parts[1:5]]
                attached = parts[6] if len(parts) > 6 else "0000"
                return angles, attached
        raise ArmError("no position reply")

    def read_calibration(self) -> dict[int, dict]:
        out = {}
        for line in self.send("G"):
            parts = line.split()
            if parts and parts[0] == "cfg" and len(parts) >= 8:
                out[int(parts[1]) - 1] = {
                    "min": float(parts[2]), "max": float(parts[3]), "offset": float(parts[4]),
                    "dir": int(float(parts[5])), "travel": float(parts[6]), "enabled": parts[7] == "1",
                }
        if len(out) != 4:
            raise ArmError("calibration reply incomplete")
        return out


class SimLink(Link):
    simulated = True

    def __init__(self, servo: list[float], base_enabled: bool = True):
        self.position = list(servo)
        self.base_enabled = base_enabled
        self.attached = False
        self.sent: list[str] = []

    def close(self) -> None:
        pass

    def resync(self) -> None:
        pass

    def send(self, line: str, timeout: float = 5.0, strict: bool = False) -> list[str]:
        self.sent.append(line)
        command = line[:1].upper()
        if command == "M":
            self.position = [float(x) for x in line.split()[1:5]]
        elif command == "A":
            self.attached = True
        elif command == "P":
            held = ("1" if self.attached and self.base_enabled else "0") + ("111" if self.attached else "000")
            return ["pos " + " ".join(f"{a:.1f}" for a in self.position) + f" att {held} mov 0"]
        return []
