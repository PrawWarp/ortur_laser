from __future__ import annotations

import json
import os
import platform
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app import runtime
from app.config import SETTINGS_DEFAULTS, _ENV, settings
from app.device import GrblSerial
from app.gcode import (
    ENGRAVE_MODES,
    HOME_EST_SECONDS,
    GridSettings,
    PRESET_ORDER,
    PRESETS,
    RasterSettings,
    estimate_gcode_seconds,
    fit_image,
    grid_to_gcode,
    image_from_upload,
    list_fonts,
    raster_to_gcode,
    render_canvas,
    should_invert_for_burn,
)

router = APIRouter()

device = GrblSerial(baud=settings.serial_baud)
jobs: dict[str, dict[str, Any]] = {}
_job_thread: threading.Thread | None = None
_SERVER_DIR = Path(__file__).resolve().parents[2]
_REPO_ROOT = _SERVER_DIR.parent
_UPDATE_BRANCH = "main"
_GITHUB_REPO = "PrawWarp/ortur_laser"


def _job_timing(s) -> dict[str, float | None]:
    """Elapsed / remaining seconds for the active or last job."""
    est = float(s.job_est_seconds or 0.0)
    started = s.job_started_at
    if not s.job_running or started is None:
        return {
            "job_est_seconds": est if est > 0 else None,
            "job_elapsed_seconds": None,
            "job_remaining_seconds": None,
        }
    elapsed = max(0.0, time.monotonic() - started)
    total = max(1, int(s.job_lines_total or 0))
    sent = max(0, int(s.job_lines_sent or 0))
    frac = min(1.0, sent / total)
    if frac >= 0.05 and elapsed >= 2.0:
        # Observed stream rate once we have a bit of history
        remaining = elapsed * (1.0 - frac) / frac
    elif est > 0:
        remaining = max(0.0, est - elapsed)
    else:
        remaining = None
    return {
        "job_est_seconds": est if est > 0 else None,
        "job_elapsed_seconds": round(elapsed, 1),
        "job_remaining_seconds": round(remaining, 1) if remaining is not None else None,
    }


def _status_dict():
    s = device.snapshot()
    return {
        "connected": s.connected,
        "port": s.port,
        "armed": s.armed,
        "state": s.state,
        "mpos": {"x": s.mpos[0], "y": s.mpos[1], "z": s.mpos[2]},
        "identity": s.identity,
        "job_running": s.job_running,
        "job_lines_total": s.job_lines_total,
        "job_lines_sent": s.job_lines_sent,
        "job_error": s.job_error,
        "last_message": s.last_message,
        "bed": {"width": settings.bed_width_mm, "height": settings.bed_height_mm},
        **_job_timing(s),
    }


class ConnectBody(BaseModel):
    port: str | None = None


class JogBody(BaseModel):
    axis: str = Field(pattern="(?i)^[xyz]$")
    distance_mm: float = 10.0
    feed: float = 2000.0


class FrameBody(BaseModel):
    width_mm: float | None = None
    height_mm: float | None = None
    origin_x: float = 0.0
    origin_y: float = 0.0
    feed: float = 3000.0


class SendBody(BaseModel):
    armed_confirm: bool = False
    home_first: bool = True


class DrySendBody(BaseModel):
    home_first: bool = True


class GridJobBody(BaseModel):
    minor_mm: float = 50.0
    major_mm: float = 100.0
    power_pct: float = 30.0
    feed: float = 900.0
    home_first: bool = False
    inset_mm: float = 0.0
    width_mm: float | None = None
    height_mm: float | None = None
    origin_x: float = 0.0
    origin_y: float = 0.0


@router.get("/device/ports")
def list_ports():
    ports = GrblSerial.list_ports()
    preferred = (settings.serial_port or "auto").strip()
    return {
        "ports": ports,
        "default": preferred,
        "hint": settings.serial_port_display,
        "platform": platform.system(),
    }


@router.post("/device/find")
def find_laser(body: ConnectBody = ConnectBody()):
    """Probe serial ports for a GRBL/Ortur engraver (Windows COM* and Linux /dev/ttyUSB*)."""
    preferred = body.port if body.port is not None else settings.serial_port
    try:
        result = device.find_laser(preferred=preferred, baud=settings.serial_baud)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return result


@router.get("/device/status")
def device_status():
    if device.connected:
        try:
            # Always safe: during jobs the streamer updates MPos; otherwise poll `?`
            if not device.status.job_running:
                device.refresh_status()
        except Exception as exc:
            return {**_status_dict(), "last_message": f"Status error: {exc}"}
    return _status_dict()


@router.post("/device/connect")
def connect(body: ConnectBody):
    port = body.port if body.port is not None else settings.serial_port
    try:
        device.connect(port)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return _status_dict()


@router.post("/device/disconnect")
def disconnect():
    device.disconnect()
    return _status_dict()


@router.post("/device/arm")
def arm():
    try:
        device.arm()
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return _status_dict()


@router.post("/device/disarm")
def disarm():
    device.disarm()
    return _status_dict()


@router.post("/device/home")
def home():
    try:
        device.home()
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return _status_dict()


@router.post("/device/unlock")
def unlock():
    try:
        device.unlock()
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return _status_dict()


@router.post("/device/reset")
def reset():
    try:
        device.soft_reset()
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return _status_dict()


@router.post("/device/laser_off")
def laser_off():
    try:
        device.laser_off()
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return _status_dict()


@router.post("/device/jog")
def jog(body: JogBody):
    try:
        device.jog(body.axis, body.distance_mm, body.feed)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return _status_dict()


@router.post("/device/frame")
def frame(body: FrameBody):
    w = body.width_mm if body.width_mm is not None else min(50.0, settings.bed_width_mm)
    h = body.height_mm if body.height_mm is not None else min(50.0, settings.bed_height_mm)
    try:
        device.frame(w, h, body.origin_x, body.origin_y, body.feed)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return _status_dict()


@router.post("/device/abort")
def abort():
    device.abort()
    return _status_dict()


@router.get("/presets")
def presets():
    items = []
    for key in PRESET_ORDER:
        p = PRESETS[key]
        items.append(
            {
                "id": key,
                "label": p.get("label", key),
                "feed": p["feed"],
                "max_power": p["max_power"],
                "power_pct": round(p["max_power"] / 10, 1),
                "line_interval_mm": p["line_interval_mm"],
            }
        )
    return {"presets": items, "default": "cardboard"}


@router.get("/engrave-modes")
def engrave_modes():
    return ENGRAVE_MODES


@router.get("/fonts")
def fonts():
    return {"fonts": list_fonts()}


@router.post("/jobs/from-upload")
async def job_from_upload(
    file: UploadFile = File(...),
    width_mm: float = Form(50.0),
    height_mm: float = Form(50.0),
    origin_x: float = Form(0.0),
    origin_y: float = Form(0.0),
    preset: str = Form("cardboard"),
    mode: str = Form("fill"),
    fit: str = Form("fill"),
    home_first: bool = Form(False),
    invert: str = Form("auto"),
    power_pct: float | None = Form(None),
    feed: float | None = Form(None),
    passes: int = Form(1),
):
    data = await file.read()
    try:
        raw = image_from_upload(data)
    except Exception as exc:
        raise HTTPException(400, f"Invalid image: {exc}") from exc
    if mode not in ENGRAVE_MODES:
        mode = "fill"
    invert_raw = str(invert).strip().lower()
    if invert_raw in ("auto", ""):
        do_invert = should_invert_for_burn(raw)
    else:
        do_invert = invert_raw in ("1", "true", "yes", "on")
    px_w = max(40, int(width_mm * 10))
    px_h = max(40, int(height_mm * 10))
    img = fit_image(raw, px_w, px_h, fit)
    p = PRESETS.get(preset, PRESETS["cardboard"])
    max_power = p["max_power"]
    feed_v = p["feed"]
    if power_pct is not None:
        max_power = int(max(1, min(100, power_pct)) * 10)  # 1%..100% → S10..S1000
    if feed is not None and feed > 0:
        feed_v = float(feed)
    n_passes = max(1, min(5, int(passes or 1)))
    rs = RasterSettings(
        width_mm=width_mm,
        height_mm=height_mm,
        origin_x=origin_x,
        origin_y=origin_y,
        line_interval_mm=p["line_interval_mm"],
        feed=feed_v,
        max_power=max_power,
        invert=do_invert,
        home_first=home_first,
        mode=mode,
        passes=n_passes,
    )
    gcode = raster_to_gcode(img, rs)
    est = estimate_gcode_seconds(gcode)
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "id": job_id,
        "name": file.filename or "upload",
        "gcode": gcode,
        "settings": rs.__dict__,
        "preset": preset,
        "mode": mode,
        "fit": fit,
        "invert": do_invert,
        "power_pct": round(max_power / 10, 1),
        "passes": n_passes,
        "est_seconds": est,
    }
    return {
        "id": job_id,
        "name": jobs[job_id]["name"],
        "lines": gcode.count("\n"),
        "mode": mode,
        "invert": do_invert,
        "power_pct": round(max_power / 10, 1),
        "feed": feed_v,
        "passes": n_passes,
        "est_seconds": round(est, 1),
    }


@router.post("/jobs/from-canvas")
async def job_from_canvas(
    text: str = Form(""),
    font_name: str = Form("Arial"),
    width_mm: float = Form(50.0),
    height_mm: float = Form(50.0),
    origin_x: float = Form(0.0),
    origin_y: float = Form(0.0),
    preset: str = Form("cardboard"),
    mode: str = Form("fill"),
    home_first: bool = Form(False),
    power_pct: float | None = Form(None),
    feed: float | None = Form(None),
    passes: int = Form(1),
    file: UploadFile | None = File(None),
):
    px_w = max(40, int(width_mm * 10))
    px_h = max(40, int(height_mm * 10))
    overlay = None
    if file is not None and file.filename:
        overlay = image_from_upload(await file.read())
    img = render_canvas(px_w, px_h, text=text, font_name=font_name, overlay=overlay)
    if mode not in ENGRAVE_MODES:
        mode = "fill"
    p = PRESETS.get(preset, PRESETS["cardboard"])
    max_power = p["max_power"]
    feed_v = p["feed"]
    if power_pct is not None:
        max_power = int(max(1, min(100, power_pct)) * 10)
    if feed is not None and feed > 0:
        feed_v = float(feed)
    n_passes = max(1, min(5, int(passes or 1)))
    rs = RasterSettings(
        width_mm=width_mm,
        height_mm=height_mm,
        origin_x=origin_x,
        origin_y=origin_y,
        line_interval_mm=p["line_interval_mm"],
        feed=feed_v,
        max_power=max_power,
        home_first=home_first,
        mode=mode,
        invert=False,  # canvas text is black ink on white
        passes=n_passes,
    )
    gcode = raster_to_gcode(img, rs)
    est = estimate_gcode_seconds(gcode)
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "id": job_id,
        "name": text.strip() or "canvas",
        "gcode": gcode,
        "settings": rs.__dict__,
        "preset": preset,
        "font_name": font_name,
        "mode": mode,
        "invert": False,
        "power_pct": round(max_power / 10, 1),
        "passes": n_passes,
        "est_seconds": est,
    }
    return {
        "id": job_id,
        "name": jobs[job_id]["name"],
        "lines": gcode.count("\n"),
        "mode": mode,
        "invert": False,
        "power_pct": round(max_power / 10, 1),
        "feed": feed_v,
        "passes": n_passes,
        "est_seconds": round(est, 1),
    }


@router.post("/jobs/from-grid")
def job_from_grid(body: GridJobBody):
    """Vector bed-alignment grid covering the configured (or requested) work area."""
    bed_w = float(settings.bed_width_mm)
    bed_h = float(settings.bed_height_mm)
    width = float(body.width_mm) if body.width_mm is not None else bed_w
    height = float(body.height_mm) if body.height_mm is not None else bed_h
    ox = max(0.0, float(body.origin_x))
    oy = max(0.0, float(body.origin_y))
    width = max(1.0, min(width, bed_w - ox))
    height = max(1.0, min(height, bed_h - oy))
    minor = max(1.0, float(body.minor_mm))
    major = max(minor, float(body.major_mm))
    max_power = int(max(1, min(100, body.power_pct)) * 10)
    feed_v = max(100.0, float(body.feed))
    inset = max(0.0, float(body.inset_mm))
    gs = GridSettings(
        width_mm=width,
        height_mm=height,
        origin_x=ox,
        origin_y=oy,
        minor_mm=minor,
        major_mm=major,
        feed=feed_v,
        max_power=max_power,
        home_first=bool(body.home_first),
        inset_mm=inset,
    )
    gcode = grid_to_gcode(gs)
    est = estimate_gcode_seconds(gcode)
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "id": job_id,
        "name": "bed-grid",
        "gcode": gcode,
        "settings": gs.__dict__,
        "mode": "grid",
        "invert": False,
        "power_pct": round(max_power / 10, 1),
        "feed": feed_v,
        "passes": 1,
        "est_seconds": est,
    }
    return {
        "id": job_id,
        "name": "bed-grid",
        "lines": gcode.count("\n"),
        "mode": "grid",
        "invert": False,
        "power_pct": round(max_power / 10, 1),
        "feed": feed_v,
        "passes": 1,
        "est_seconds": round(est, 1),
    }


@router.get("/jobs/{job_id}/gcode")
def get_gcode(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return PlainTextResponse(job["gcode"], media_type="text/plain")


@router.post("/jobs/{job_id}/send")
def send_job(job_id: str, body: SendBody):
    global _job_thread
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if not body.armed_confirm:
        raise HTTPException(400, "armed_confirm must be true")
    if not device.status.armed:
        raise HTTPException(400, "Arm the machine in the control panel first")
    if device.status.job_running:
        raise HTTPException(400, "A job is already running")

    lines = job["gcode"].splitlines()
    home_first = bool(body.home_first)
    stream_est = float(job.get("est_seconds") or estimate_gcode_seconds(job["gcode"]))
    total_est = stream_est + (HOME_EST_SECONDS if home_first else 0.0)

    def runner():
        try:
            with device._lock:
                device.status.job_running = True
                device.status.job_error = None
                device.status.job_est_seconds = total_est
                device.status.job_started_at = time.monotonic()
                device.status.job_lines_total = max(1, len(lines))
                device.status.job_lines_sent = 0
                device.status.last_message = "Homing…" if home_first else "Job started"
            if home_first:
                device.home()
            device.send_job(
                lines,
                require_armed=True,
                force_laser_off=False,
                est_seconds=total_est,
                reset_timer=False,
            )
        except Exception:
            pass

    _job_thread = threading.Thread(target=runner, daemon=True)
    _job_thread.start()
    msg = "Job started (homing first)" if home_first else "Job started"
    return {"ok": True, "message": msg, **_status_dict()}


@router.post("/jobs/{job_id}/send-dry")
def send_job_dry(job_id: str, body: DrySendBody | None = None):
    """Stream job with laser forced off (S0/M5) — for no-laser testing."""
    global _job_thread
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if device.status.job_running:
        raise HTTPException(400, "A job is already running")
    lines = job["gcode"].splitlines()
    home_first = True if body is None else bool(body.home_first)
    stream_est = float(job.get("est_seconds") or estimate_gcode_seconds(job["gcode"]))
    total_est = stream_est + (HOME_EST_SECONDS if home_first else 0.0)

    def runner():
        try:
            with device._lock:
                device.status.job_running = True
                device.status.job_error = None
                device.status.job_est_seconds = total_est
                device.status.job_started_at = time.monotonic()
                device.status.job_lines_total = max(1, len(lines))
                device.status.job_lines_sent = 0
                device.status.last_message = "Homing…" if home_first else "Dry-run started"
            if home_first:
                device.home()
            device.send_job(
                lines,
                require_armed=False,
                force_laser_off=True,
                est_seconds=total_est,
                reset_timer=False,
            )
        except Exception:
            pass

    _job_thread = threading.Thread(target=runner, daemon=True)
    _job_thread.start()
    msg = "Dry-run started (homing first, laser off)" if home_first else "Dry-run started (laser forced off)"
    return {"ok": True, "message": msg, **_status_dict()}


def _lan_ips() -> list[str]:
    found: set[str] = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
                found.add(ip)
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.3)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
            found.add(ip)
    except OSError:
        pass

    def score(ip: str) -> tuple[int, str]:
        if ip.startswith("192.168."):
            return (0, ip)
        if ip.startswith("10."):
            return (1, ip)
        if ip.startswith("172."):
            return (2, ip)
        return (3, ip)

    ordered = sorted(found, key=score)
    # Prefer home/LAN ranges; hide Hyper-V/WSL 172.x when a better IP exists.
    preferred = [ip for ip in ordered if ip.startswith(("192.168.", "10."))]
    return preferred or ordered


_ENV_KEY_MAP: dict[str, str] = {
    "serial_port": "SERIAL_PORT",
    "serial_baud": "SERIAL_BAUD",
    "bed_width_mm": "BED_WIDTH_MM",
    "bed_height_mm": "BED_HEIGHT_MM",
    "lan_access": "LAN_ACCESS",
    "port": "PORT",
}


def _write_env_value(key: str, value: str) -> None:
    _write_env_values({key: value})


def _write_env_values(updates: dict[str, str]) -> None:
    path = _ENV
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    # Drop legacy HOST override so bind follows LAN_ACCESS only.
    lines = [ln for ln in lines if not re.match(r"^\s*HOST\s*=", ln)]
    pending = dict(updates)
    out: list[str] = []
    for line in lines:
        matched_key = None
        for key in list(pending):
            if re.match(rf"^\s*{re.escape(key)}\s*=", line):
                matched_key = key
                break
        if matched_key is not None:
            out.append(f"{matched_key}={pending.pop(matched_key)}")
        else:
            out.append(line)
    if pending:
        if out and out[-1].strip():
            out.append("")
        for key, value in pending.items():
            out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _format_env_value(field: str, value: Any) -> str:
    if field == "lan_access":
        return "true" if bool(value) else "false"
    if field in ("serial_baud", "port"):
        return str(int(value))
    if field in ("bed_width_mm", "bed_height_mm"):
        n = float(value)
        return str(int(n)) if n == int(n) else str(n)
    return str(value).strip()


def _settings_values() -> dict[str, Any]:
    return {
        "serial_port": (settings.serial_port or "auto").strip() or "auto",
        "serial_baud": int(settings.serial_baud),
        "bed_width_mm": float(settings.bed_width_mm),
        "bed_height_mm": float(settings.bed_height_mm),
        "lan_access": bool(settings.lan_access),
        "port": int(settings.port),
    }


def _settings_payload() -> dict[str, Any]:
    return {
        "values": _settings_values(),
        "defaults": dict(SETTINGS_DEFAULTS),
        "env_file": str(_ENV),
        "fields": [
            {
                "key": "serial_port",
                "label": "Serial port",
                "env": "SERIAL_PORT",
                "hint": "auto, COM3, /dev/ttyUSB0, /dev/ttyACM0",
            },
            {
                "key": "serial_baud",
                "label": "Baud rate",
                "env": "SERIAL_BAUD",
                "hint": "Usually 115200 for GRBL",
            },
            {
                "key": "bed_width_mm",
                "label": "Bed width (mm)",
                "env": "BED_WIDTH_MM",
                "hint": "Work area width",
            },
            {
                "key": "bed_height_mm",
                "label": "Bed height (mm)",
                "env": "BED_HEIGHT_MM",
                "hint": "Work area height",
            },
            {
                "key": "port",
                "label": "HTTP port",
                "env": "PORT",
                "hint": "UI listen port (changing redirects after restart)",
            },
            {
                "key": "lan_access",
                "label": "LAN access",
                "env": "LAN_ACCESS",
                "hint": "Bind 0.0.0.0 so Wi‑Fi devices can open the UI",
            },
        ],
    }


def _apply_settings_values(
    *,
    serial_port: str,
    serial_baud: int,
    bed_width_mm: float,
    bed_height_mm: float,
    lan_access: bool,
    port: int,
) -> None:
    env_updates = {
        _ENV_KEY_MAP["serial_port"]: _format_env_value("serial_port", serial_port),
        _ENV_KEY_MAP["serial_baud"]: _format_env_value("serial_baud", serial_baud),
        _ENV_KEY_MAP["bed_width_mm"]: _format_env_value("bed_width_mm", bed_width_mm),
        _ENV_KEY_MAP["bed_height_mm"]: _format_env_value("bed_height_mm", bed_height_mm),
        _ENV_KEY_MAP["lan_access"]: _format_env_value("lan_access", lan_access),
        _ENV_KEY_MAP["port"]: _format_env_value("port", port),
    }
    _write_env_values(env_updates)
    settings.serial_port = serial_port
    settings.serial_baud = serial_baud
    settings.bed_width_mm = bed_width_mm
    settings.bed_height_mm = bed_height_mm
    settings.lan_access = lan_access
    settings.port = port


def _network_payload() -> dict[str, Any]:
    bind = runtime.bind_host
    port = runtime.port or settings.port
    lan_on = bind in ("0.0.0.0", "::")
    local_urls = [f"http://127.0.0.1:{port}"]
    lan_urls = [f"http://{ip}:{port}" for ip in _lan_ips()] if lan_on else []
    return {
        "lan_access": settings.lan_access,
        "active": lan_on,
        "bind_host": bind,
        "port": port,
        "local_urls": local_urls,
        "lan_urls": lan_urls,
        "restart_required": bool(settings.lan_access) != lan_on,
    }


def _restart_with_run_py() -> None:
    time.sleep(0.4)
    run_py = _SERVER_DIR / "run.py"
    kwargs: dict[str, Any] = {
        "cwd": str(_SERVER_DIR),
        "close_fds": True,
    }
    if sys.platform == "win32":
        # New console so the restarted server stays visible after this process exits.
        kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE  # type: ignore[attr-defined]
    else:
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
        kwargs["stdin"] = subprocess.DEVNULL
        kwargs["start_new_session"] = True
    subprocess.Popen([sys.executable, str(run_py)], **kwargs)
    os._exit(0)


def _refuse_if_job_running(action: str = "change settings") -> None:
    if device.status.job_running:
        raise HTTPException(400, f"Cannot {action} while a job is running")


class NetworkBody(BaseModel):
    lan_access: bool
    restart: bool = True


class SettingsBody(BaseModel):
    serial_port: str = Field(min_length=1, max_length=128)
    serial_baud: int = Field(ge=1200, le=921600)
    bed_width_mm: float = Field(gt=0, le=5000)
    bed_height_mm: float = Field(gt=0, le=5000)
    lan_access: bool
    port: int = Field(ge=1, le=65535)
    restart: bool = True


class SettingsResetBody(BaseModel):
    restart: bool = True


@router.get("/server/network")
def get_network():
    return _network_payload()


@router.post("/server/network")
def set_network(body: NetworkBody):
    _refuse_if_job_running()
    _write_env_value("LAN_ACCESS", "true" if body.lan_access else "false")
    settings.lan_access = body.lan_access
    payload = {**_network_payload(), "ok": True, "restarting": False}
    if body.restart:
        payload["restarting"] = True
        payload["restart_required"] = False
        threading.Thread(target=_restart_with_run_py, daemon=True).start()
    return payload


@router.get("/server/settings")
def get_settings():
    return _settings_payload()


@router.put("/server/settings")
def put_settings(body: SettingsBody):
    _refuse_if_job_running()
    serial_port = (body.serial_port or "auto").strip() or "auto"
    _apply_settings_values(
        serial_port=serial_port,
        serial_baud=int(body.serial_baud),
        bed_width_mm=float(body.bed_width_mm),
        bed_height_mm=float(body.bed_height_mm),
        lan_access=bool(body.lan_access),
        port=int(body.port),
    )
    payload = {**_settings_payload(), "ok": True, "restarting": False}
    if body.restart:
        payload["restarting"] = True
        threading.Thread(target=_restart_with_run_py, daemon=True).start()
    return payload


@router.post("/server/settings/reset")
def reset_settings(body: SettingsResetBody | None = None):
    _refuse_if_job_running()
    restart = True if body is None else bool(body.restart)
    d = SETTINGS_DEFAULTS
    _apply_settings_values(
        serial_port=str(d["serial_port"]),
        serial_baud=int(d["serial_baud"]),
        bed_width_mm=float(d["bed_width_mm"]),
        bed_height_mm=float(d["bed_height_mm"]),
        lan_access=bool(d["lan_access"]),
        port=int(d["port"]),
    )
    payload = {**_settings_payload(), "ok": True, "restarting": False, "reset": True}
    if restart:
        payload["restarting"] = True
        threading.Thread(target=_restart_with_run_py, daemon=True).start()
    return payload


def _git(*args: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(_REPO_ROOT), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _git_ok(proc: subprocess.CompletedProcess[str]) -> bool:
    return proc.returncode == 0


def _git_commit_info(ref: str) -> dict[str, str] | None:
    fmt = "%H%x1f%h%x1f%ci%x1f%s"
    proc = _git("log", "-1", f"--format={fmt}", ref, timeout=15)
    if not _git_ok(proc) or not proc.stdout.strip():
        return None
    parts = proc.stdout.strip().split("\x1f", 3)
    if len(parts) < 4:
        return None
    return {
        "sha": parts[0],
        "short": parts[1],
        "date": parts[2],
        "subject": parts[3],
    }


def _git_is_repo() -> bool:
    return (_REPO_ROOT / ".git").exists() and _git_ok(_git("rev-parse", "--is-inside-work-tree", timeout=10))


def _git_dirty_files() -> list[str]:
    proc = _git("status", "--porcelain", timeout=15)
    if not _git_ok(proc) or not proc.stdout.strip():
        return []
    return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]


def _git_dirty() -> bool:
    return bool(_git_dirty_files())


def _git_remote_url() -> str:
    proc = _git("remote", "get-url", "origin", timeout=10)
    if _git_ok(proc) and proc.stdout.strip():
        return proc.stdout.strip()
    return f"https://github.com/{_GITHUB_REPO}.git"


def _update_payload(*, fetch: bool) -> dict[str, Any]:
    base: dict[str, Any] = {
        "repo": _GITHUB_REPO,
        "repo_url": f"https://github.com/{_GITHUB_REPO}",
        "branch": _UPDATE_BRANCH,
        "root": str(_REPO_ROOT),
        "git": False,
        "current": None,
        "latest": None,
        "update_available": False,
        "commits_behind": 0,
        "commits": [],
        "dirty": False,
        "dirty_files": [],
        "fetched": False,
        "error": None,
    }
    if not _git_is_repo():
        base["error"] = "Not a git checkout — reinstall with get.ps1 / get.sh to enable updates."
        return base

    base["git"] = True
    base["remote_url"] = _git_remote_url()
    dirty_files = _git_dirty_files()
    base["dirty"] = bool(dirty_files)
    base["dirty_files"] = dirty_files[:20]
    current = _git_commit_info("HEAD")
    base["current"] = current

    if fetch:
        fetch_proc = _git("fetch", "--prune", "origin", timeout=90)
        if not _git_ok(fetch_proc):
            err = (fetch_proc.stderr or fetch_proc.stdout or "git fetch failed").strip()
            base["error"] = err[-400:]
            # Still report local vs last-known origin/main if present.
        else:
            base["fetched"] = True

    remote_ref = f"origin/{_UPDATE_BRANCH}"
    latest = _git_commit_info(remote_ref)
    if latest is None:
        # Fallback: query GitHub without requiring a successful fetch.
        latest = _github_latest_commit()
        if latest:
            base["latest"] = latest
            if current and latest["sha"] != current["sha"]:
                base["update_available"] = True
                base["commits_behind"] = 1
            if base["error"] is None and not base["fetched"]:
                base["error"] = None
        elif base["error"] is None:
            base["error"] = f"Could not resolve {remote_ref}. Check network / git remote."
        return base

    base["latest"] = latest
    if current and latest["sha"] != current["sha"]:
        count_proc = _git("rev-list", "--count", f"HEAD..{remote_ref}", timeout=15)
        behind = int(count_proc.stdout.strip() or "0") if _git_ok(count_proc) else 1
        base["commits_behind"] = max(behind, 1)
        base["update_available"] = behind > 0
        log_proc = _git(
            "log",
            "--oneline",
            "--no-decorate",
            "-8",
            f"HEAD..{remote_ref}",
            timeout=15,
        )
        if _git_ok(log_proc) and log_proc.stdout.strip():
            base["commits"] = [ln.strip() for ln in log_proc.stdout.splitlines() if ln.strip()]
    return base


def _github_latest_commit() -> dict[str, str] | None:
    """Fallback when local origin/main is missing — public GitHub API."""
    try:
        import urllib.error
        import urllib.request

        url = f"https://api.github.com/repos/{_GITHUB_REPO}/commits/{_UPDATE_BRANCH}"
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "ortur-engraver-update-check",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        commit = data.get("commit") or {}
        return {
            "sha": data.get("sha") or "",
            "short": (data.get("sha") or "")[:7],
            "date": ((commit.get("committer") or {}).get("date")) or "",
            "subject": (commit.get("message") or "").split("\n", 1)[0],
        }
    except Exception:
        return None


def _pip_install_requirements() -> None:
    req = _SERVER_DIR / "requirements.txt"
    if not req.exists():
        raise HTTPException(500, "Missing server/requirements.txt")
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "-r", str(req)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "pip failed").strip()
        raise HTTPException(500, f"pip install failed: {err[-500:]}")


class UpdateBody(BaseModel):
    restart: bool = True


@router.get("/server/update")
def get_update(fetch: bool = Query(True)):
    try:
        return _update_payload(fetch=fetch)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Timed out contacting GitHub / git remote") from None
    except FileNotFoundError:
        raise HTTPException(500, "git is not installed or not on PATH") from None


@router.post("/server/update")
def apply_update(body: UpdateBody | None = None):
    _refuse_if_job_running("update")
    restart = True if body is None else bool(body.restart)
    if not _git_is_repo():
        raise HTTPException(400, "Not a git checkout — cannot update from GitHub")

    try:
        fetch_proc = _git("fetch", "--prune", "origin", timeout=90)
        if not _git_ok(fetch_proc):
            err = (fetch_proc.stderr or fetch_proc.stdout or "git fetch failed").strip()
            raise HTTPException(502, f"git fetch failed: {err[-400:]}")

        remote_ref = f"origin/{_UPDATE_BRANCH}"
        if _git_commit_info(remote_ref) is None:
            raise HTTPException(502, f"Missing {remote_ref} after fetch")

        checkout = _git("checkout", _UPDATE_BRANCH, timeout=30)
        if not _git_ok(checkout):
            err = (checkout.stderr or checkout.stdout or "checkout failed").strip()
            raise HTTPException(500, f"git checkout failed: {err[-400:]}")

        # Match install scripts: hard sync to origin/main. Keeps ignored files (.env, .venv).
        reset = _git("reset", "--hard", remote_ref, timeout=60)
        if not _git_ok(reset):
            err = (reset.stderr or reset.stdout or "reset failed").strip()
            raise HTTPException(500, f"git reset failed: {err[-400:]}")

        # Drop untracked junk only — never -x (that would delete .env / .venv).
        _git("clean", "-fd", timeout=30)

        _pip_install_requirements()
    except HTTPException:
        raise
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Update timed out during git/pip") from None
    except FileNotFoundError:
        raise HTTPException(500, "git is not installed or not on PATH") from None

    payload = {**_update_payload(fetch=False), "ok": True, "updated": True, "restarting": False}
    if restart:
        payload["restarting"] = True
        threading.Thread(target=_restart_with_run_py, daemon=True).start()
    return payload
