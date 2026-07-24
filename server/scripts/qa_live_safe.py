"""Safe live hardware smoke test via API (no ARM / no live burn)."""
from __future__ import annotations

import json
import sys
import time

import requests

BASE = "http://127.0.0.1:8000"
fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print("  OK ", msg)
    else:
        print(" FAIL", msg)
        fails.append(msg)


def get(path: str):
    r = requests.get(f"{BASE}{path}", timeout=30)
    r.raise_for_status()
    return r.json()


def post(path: str, body: dict | None = None):
    r = requests.post(f"{BASE}{path}", json=body or {}, timeout=120)
    if r.status_code >= 400:
        raise RuntimeError(f"{path} -> {r.status_code}: {r.text[:400]}")
    return r.json()


def main() -> int:
    print("=== PORTS / FIND ===")
    ports = get("/api/device/ports")
    print(" ports:", json.dumps(ports.get("ports"), indent=2))
    check(bool(ports.get("ports")), "at least one serial port")

    found = post("/api/device/find", {"port": "auto"})
    print(" find:", found)
    check(bool(found.get("found") or found.get("port")), f"find laser ({found})")
    port = found.get("port") or (ports["ports"][0]["device"] if ports.get("ports") else None)
    check(bool(port), f"using port {port}")

    print("=== CONNECT ===")
    try:
        st = post("/api/device/connect", {"port": port})
    except Exception as exc:
        check(False, f"connect failed: {exc}")
        return 1
    print(" status:", st.get("state"), st.get("identity"), st.get("last_message"))
    check(st.get("connected") is True, "connected")

    # Unlock if Alarm
    state = (st.get("state") or "").lower()
    if state == "alarm":
        print("=== UNLOCK (was Alarm) ===")
        st = post("/api/device/unlock", {})
        print(" status:", st.get("state"), st.get("last_message"))
        check((st.get("state") or "").lower() != "alarm", "unlocked out of Alarm")

    print("=== JOG +1mm X (laser off) ===")
    before = st.get("mpos") or {}
    try:
        st = post("/api/device/jog", {"axis": "X", "distance_mm": 1.0, "feed": 1000})
        print(" mpos:", st.get("mpos"), "msg:", st.get("last_message"))
        check("error:9" not in (st.get("last_message") or "").lower(), "no error:9 on jog")
        after = st.get("mpos") or {}
        dx = abs(float(after.get("x", 0)) - float(before.get("x", 0)))
        check(dx > 0.2, f"X moved ~{dx:.3f} mm")
    except Exception as exc:
        check(False, f"jog failed: {exc}")

    print("=== JOG back -1mm X ===")
    try:
        st = post("/api/device/jog", {"axis": "X", "distance_mm": -1.0, "feed": 1000})
        print(" mpos:", st.get("mpos"), "msg:", st.get("last_message"))
        check(True, "return jog accepted")
    except Exception as exc:
        check(False, f"return jog failed: {exc}")

    print("=== FRAME 20x20 @ S0 ===")
    try:
        st = post(
            "/api/device/frame",
            {"width_mm": 20, "height_mm": 20, "origin_x": 0, "origin_y": 0, "feed": 2000},
        )
        # frame may return immediately if async — poll
        for _ in range(40):
            st = get("/api/device/status")
            if not st.get("job_running"):
                break
            time.sleep(0.25)
        print(" status:", st.get("state"), "err:", st.get("job_error"), "msg:", st.get("last_message"))
        check(not st.get("job_error"), f"frame no job_error ({st.get('job_error')})")
        check(st.get("connected") is True, "still connected after frame")
    except Exception as exc:
        check(False, f"frame failed: {exc}")

    print("=== DRY RUN small canvas job ===")
    try:
        job = requests.post(
            f"{BASE}/api/jobs/from-canvas",
            data={
                "text": "T",
                "font_name": "Arial",
                "width_mm": "15",
                "height_mm": "15",
                "origin_x": "5",
                "origin_y": "5",
                "preset": "cardboard_test",
                "mode": "outline",
                "power_pct": "10",
                "feed": "1200",
            },
            timeout=60,
        )
        job.raise_for_status()
        jid = job.json()["id"]
        print(" job:", jid, "lines:", job.json().get("lines"))
        st = post(f"/api/jobs/{jid}/send-dry", {"home_first": False})
        for _ in range(120):
            st = get("/api/device/status")
            if not st.get("job_running"):
                break
            time.sleep(0.5)
        print(" dry done:", st.get("last_message"), "err:", st.get("job_error"))
        check(not st.get("job_error"), f"dry run clean ({st.get('job_error')})")
        check(st.get("armed") is False, "remained DISARMED")
    except Exception as exc:
        check(False, f"dry run failed: {exc}")

    print("=== DISCONNECT ===")
    try:
        st = post("/api/device/disconnect", {})
        check(st.get("connected") is False, "disconnected")
    except Exception as exc:
        check(False, f"disconnect failed: {exc}")

    print()
    if fails:
        print(f"FAILED {len(fails)}")
        for f in fails:
            print(" -", f)
        return 1
    print("ALL LIVE SAFE CHECKS PASSED (no ARM / no live burn)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
