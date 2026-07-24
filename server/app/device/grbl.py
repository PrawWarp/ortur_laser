from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import serial
from serial.tools import list_ports


STATUS_RE = re.compile(
    r"<(?P<state>\w+)\|MPos:(?P<mx>[-\d.]+),(?P<my>[-\d.]+),(?P<mz>[-\d.]+)"
)


@dataclass
class DeviceStatus:
    connected: bool = False
    port: str | None = None
    armed: bool = False
    state: str = "Disconnected"
    mpos: tuple[float, float, float] = (0.0, 0.0, 0.0)
    identity: str = ""
    job_running: bool = False
    job_lines_total: int = 0
    job_lines_sent: int = 0
    job_error: str | None = None
    job_est_seconds: float = 0.0
    job_started_at: float | None = None  # time.monotonic() when stream began
    last_message: str = ""


@dataclass
class GrblSerial:
    baud: int = 115200
    timeout: float = 1.0
    _ser: serial.Serial | None = field(default=None, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _abort: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    status: DeviceStatus = field(default_factory=DeviceStatus, init=False)
    _identity_cache: str = field(default="", init=False)

    @staticmethod
    def list_ports() -> list[dict]:
        ports = []
        for p in list_ports.comports():
            ports.append(
                {
                    "device": p.device,
                    "description": p.description or "",
                    "hwid": p.hwid or "",
                }
            )
        return ports

    @property
    def connected(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def connect(self, port: str) -> DeviceStatus:
        with self._lock:
            if self.connected:
                self.disconnect()
            self._abort.clear()
            ser = serial.Serial(port=port, baudrate=self.baud, timeout=self.timeout)
            ser.dtr = True
            ser.rts = True
            time.sleep(1.0)
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            self._ser = ser
            self.status.connected = True
            self.status.port = port
            self.status.armed = False
            self.status.job_running = False
            self.status.job_error = None
            identity = self._query_identity_unlocked()
            self._identity_cache = identity
            self.status.identity = identity
            self.refresh_status_unlocked()
            self.status.last_message = "Connected"
            return self.snapshot()

    def disconnect(self) -> DeviceStatus:
        with self._lock:
            self._abort.set()
            if self._ser and self._ser.is_open:
                try:
                    self._write_raw(b"M5\n")
                except Exception:
                    pass
                try:
                    self._ser.close()
                except Exception:
                    pass
            self._ser = None
            self.status = DeviceStatus()
            self.status.last_message = "Disconnected"
            return self.snapshot()

    def snapshot(self) -> DeviceStatus:
        return DeviceStatus(**self.status.__dict__)

    def arm(self) -> DeviceStatus:
        with self._lock:
            self._require()
            self.status.armed = True
            self.status.last_message = "ARMED — laser commands allowed"
            return self.snapshot()

    def disarm(self) -> DeviceStatus:
        with self._lock:
            self.status.armed = False
            if self.connected:
                try:
                    self._write_line_unlocked("M5")
                except Exception:
                    pass
            self.status.last_message = "DISARMED — laser blocked"
            return self.snapshot()

    def refresh_status(self) -> DeviceStatus:
        with self._lock:
            self._require()
            self.refresh_status_unlocked()
            return self.snapshot()

    def refresh_status_unlocked(self) -> None:
        resp = self._command_unlocked("?", wait_ok=False, read_ms=400)
        match = STATUS_RE.search(resp)
        if match:
            self.status.state = match.group("state")
            self.status.mpos = (
                float(match.group("mx")),
                float(match.group("my")),
                float(match.group("mz")),
            )
        self.status.identity = self._identity_cache
        self.status.connected = True

    def home(self) -> DeviceStatus:
        with self._lock:
            self._require()
            self._command_unlocked("$H", wait_ok=True, read_ms=60000)
            self.refresh_status_unlocked()
            self.status.last_message = "Homed"
            return self.snapshot()

    def unlock(self) -> DeviceStatus:
        with self._lock:
            self._require()
            self._abort.clear()
            self._command_unlocked("$X", wait_ok=True, read_ms=2000)
            self.status.job_running = False
            self.status.job_error = None
            self.status.last_message = "Unlocked"
            self.refresh_status_unlocked()
            return self.snapshot()

    def soft_reset(self) -> DeviceStatus:
        with self._lock:
            self._require()
            self._write_raw(b"\x18")
            time.sleep(1.0)
            self._drain(800)
            self.status.armed = False
            self.status.job_running = False
            identity = self._query_identity_unlocked()
            self._identity_cache = identity
            self.status.identity = identity
            self.refresh_status_unlocked()
            self.status.last_message = "Soft reset"
            return self.snapshot()

    def laser_off(self) -> DeviceStatus:
        with self._lock:
            self._require()
            self._command_unlocked("M5", wait_ok=True, read_ms=2000)
            self.status.last_message = "Laser off (M5)"
            return self.snapshot()

    def jog(self, axis: str, distance_mm: float, feed: float = 2000.0) -> DeviceStatus:
        axis = axis.upper()
        if axis not in {"X", "Y", "Z"}:
            raise ValueError("axis must be X, Y, or Z")
        with self._lock:
            self._require()
            # Laser off during jog
            self._command_unlocked("M5", wait_ok=True, read_ms=1500)
            cmd = f"$J=G91 G21 {axis}{distance_mm:.3f} F{feed:.1f}"
            self._command_unlocked(cmd, wait_ok=True, read_ms=10000)
            self.refresh_status_unlocked()
            self.status.last_message = f"Jog {axis}{distance_mm}"
            return self.snapshot()

    def frame(
        self,
        width_mm: float,
        height_mm: float,
        origin_x: float = 0.0,
        origin_y: float = 0.0,
        feed: float = 3000.0,
    ) -> DeviceStatus:
        """Move a rectangle with laser forced off (S0 / M5)."""
        lines = [
            "G21",
            "G90",
            "G94",
            "M5",
            "S0",
            f"G0 X{origin_x:.3f} Y{origin_y:.3f}",
            f"G1 X{origin_x + width_mm:.3f} Y{origin_y:.3f} F{feed:.1f} S0",
            f"G1 X{origin_x + width_mm:.3f} Y{origin_y + height_mm:.3f} F{feed:.1f} S0",
            f"G1 X{origin_x:.3f} Y{origin_y + height_mm:.3f} F{feed:.1f} S0",
            f"G1 X{origin_x:.3f} Y{origin_y:.3f} F{feed:.1f} S0",
            "M5",
        ]
        return self.send_job(lines, require_armed=False, force_laser_off=True)

    def send_job(
        self,
        lines: list[str],
        require_armed: bool = True,
        force_laser_off: bool = False,
        on_progress: Callable[[DeviceStatus], None] | None = None,
        est_seconds: float = 0.0,
        reset_timer: bool = True,
    ) -> DeviceStatus:
        cleaned = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith(";")]
        with self._lock:
            self._require()
            if require_armed and not self.status.armed:
                raise RuntimeError("Machine is DISARMED — arm before sending a laser job")
            if force_laser_off or not self.status.armed:
                cleaned = [self._strip_laser(ln) for ln in cleaned]
            self._abort.clear()
            self.status.job_running = True
            self.status.job_lines_total = len(cleaned)
            self.status.job_lines_sent = 0
            self.status.job_error = None
            if reset_timer or not self.status.job_started_at:
                self.status.job_est_seconds = max(0.0, float(est_seconds or 0.0))
                self.status.job_started_at = time.monotonic()
            elif est_seconds:
                self.status.job_est_seconds = max(0.0, float(est_seconds))
            self.status.last_message = "Job started"

        try:
            for i, line in enumerate(cleaned):
                if self._abort.is_set():
                    return self._finish_abort()
                # Write under lock; wait for ok WITHOUT holding lock so Abort can soft-reset.
                with self._lock:
                    self._require()
                    self._write_line_unlocked(line)
                self._read_until(wait_ok=True, read_ms=30000)
                # Realtime position while streaming (for live canvas cursor)
                if i % 2 == 0 or i + 1 == len(cleaned):
                    try:
                        with self._lock:
                            self._write_raw(b"?")
                        st = self._read_until(wait_ok=False, read_ms=250)
                        match = STATUS_RE.search(st)
                        if match:
                            with self._lock:
                                self.status.state = match.group("state")
                                self.status.mpos = (
                                    float(match.group("mx")),
                                    float(match.group("my")),
                                    float(match.group("mz")),
                                )
                    except Exception:
                        pass
                with self._lock:
                    self.status.job_lines_sent = i + 1
                    if on_progress and (i % 5 == 0 or i + 1 == len(cleaned)):
                        on_progress(self.snapshot())
            with self._lock:
                self._command_unlocked("M5", wait_ok=True, read_ms=2000)
                self.status.job_running = False
                self.status.last_message = "Job complete"
                self.refresh_status_unlocked()
                return self.snapshot()
        except Exception as exc:
            if self._abort.is_set() or "Aborted" in str(exc):
                return self._finish_abort()
            with self._lock:
                self.status.job_running = False
                self.status.job_error = str(exc)
                self.status.last_message = f"Job error: {exc}"
                try:
                    self._command_unlocked("M5", wait_ok=True, read_ms=2000)
                except Exception:
                    pass
                raise

    def abort(self) -> DeviceStatus:
        """Stop mid-job immediately: set flag + GRBL soft-reset (Ctrl-X) + M5."""
        self._abort.set()
        with self._lock:
            if self.connected and self._ser is not None:
                try:
                    self._write_raw(b"\x18")  # soft-reset stops motion buffer
                    time.sleep(0.25)
                    self._drain(400)
                    self._write_raw(b"M5\n")
                    time.sleep(0.1)
                    self._drain(300)
                except Exception:
                    pass
            # Allow subsequent commands/status after the interrupt
            self._abort.clear()
            self.status.job_running = False
            self.status.armed = False
            self.status.last_message = "CANCELLED — soft reset + laser off"
            try:
                if self.connected:
                    # Soft-reset leaves Alarm; unlock so machine is usable again
                    try:
                        self._command_unlocked("$X", wait_ok=True, read_ms=2000)
                    except Exception:
                        pass
                    self.refresh_status_unlocked()
            except Exception:
                pass
            return self.snapshot()

    def _finish_abort(self) -> DeviceStatus:
        with self._lock:
            self.status.job_running = False
            self.status.armed = False
            if not self.status.last_message.startswith("CANCELLED"):
                self.status.last_message = "Job cancelled"
            try:
                if self.connected:
                    self._write_raw(b"M5\n")
                    self._drain(300)
            except Exception:
                pass
            return self.snapshot()

    @staticmethod
    def _strip_laser(line: str) -> str:
        upper = line.upper()
        if upper.startswith("M3") or upper.startswith("M4"):
            return "M5"
        # Force S0 on motion lines
        line = re.sub(r"\bS[0-9.]+", "S0", line, flags=re.IGNORECASE)
        if re.match(r"^G[01]\b", line, re.IGNORECASE) and "S" not in line.upper():
            line = line + " S0"
        return line

    def _require(self) -> None:
        if not self.connected or self._ser is None:
            raise RuntimeError("Not connected to device")

    def _query_identity_unlocked(self) -> str:
        resp = self._command_unlocked("$I", wait_ok=True, read_ms=2000)
        lines = [ln for ln in resp.splitlines() if ln.strip() and ln.strip().lower() != "ok"]
        return " | ".join(lines) if lines else resp.strip()

    def _command_unlocked(self, cmd: str, wait_ok: bool, read_ms: int) -> str:
        self._write_line_unlocked(cmd)
        return self._read_until(wait_ok=wait_ok, read_ms=read_ms)

    def _write_line_unlocked(self, cmd: str) -> None:
        assert self._ser is not None
        payload = (cmd.strip() + "\n").encode("ascii", errors="ignore")
        self._ser.write(payload)
        self._ser.flush()

    def _write_raw(self, data: bytes) -> None:
        assert self._ser is not None
        self._ser.write(data)
        self._ser.flush()

    def _drain(self, ms: int) -> str:
        return self._read_until(wait_ok=False, read_ms=ms)

    def _read_until(self, wait_ok: bool, read_ms: int) -> str:
        assert self._ser is not None
        deadline = time.monotonic() + (read_ms / 1000.0)
        buf = ""
        while time.monotonic() < deadline:
            if self._abort.is_set():
                raise RuntimeError("Aborted")
            waiting = self._ser.in_waiting
            if not waiting:
                time.sleep(0.02)
                continue
            chunk = self._ser.read(waiting).decode("utf-8", errors="replace")
            buf += chunk
            lower = buf.lower()

            # Status query: return as soon as we have a full <...> report
            if not wait_ok and "<" in buf and ">" in buf:
                return buf

            if wait_ok:
                if re.search(r"(?m)^error:\d+", buf):
                    raise RuntimeError(buf.strip())
                if re.search(r"(?m)^ok\s*$", buf):
                    return buf
                # Soft-reset banners while aborting
                if "grbl" in lower or "[ver:" in lower:
                    # keep reading unless aborting
                    if self._abort.is_set():
                        raise RuntimeError("Aborted")

        if self._abort.is_set():
            raise RuntimeError("Aborted")
        if wait_ok and "ok" not in buf.lower():
            raise TimeoutError(f"Timeout waiting for ok. Got: {buf!r}")
        return buf
