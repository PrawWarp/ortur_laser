from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app import runtime
from app.config import _ENV, settings
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
    return {"ports": GrblSerial.list_ports(), "default": settings.serial_port}


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
    port = body.port or settings.serial_port
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


def _write_env_value(key: str, value: str) -> None:
    path = _ENV
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    replaced = False
    out: list[str] = []
    for line in lines:
        if pattern.match(line):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        if out and out[-1].strip():
            out.append("")
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


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


class NetworkBody(BaseModel):
    lan_access: bool
    restart: bool = True


@router.get("/server/network")
def get_network():
    return _network_payload()


@router.post("/server/network")
def set_network(body: NetworkBody):
    _write_env_value("LAN_ACCESS", "true" if body.lan_access else "false")
    # Drop legacy HOST override so bind follows LAN_ACCESS
    if _ENV.exists():
        text = _ENV.read_text(encoding="utf-8")
        if re.search(r"^\s*HOST\s*=", text, re.M):
            lines = [
                ln
                for ln in text.splitlines()
                if not re.match(r"^\s*HOST\s*=", ln)
            ]
            _ENV.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    settings.lan_access = body.lan_access
    payload = {**_network_payload(), "ok": True, "restarting": False}
    if body.restart:
        payload["restarting"] = True
        payload["restart_required"] = False
        threading.Thread(target=_restart_with_run_py, daemon=True).start()
    return payload
