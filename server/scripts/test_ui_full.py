"""Full UI + API sanity suite (no laser required)."""
from __future__ import annotations

import re
import sys

import requests

BASE = "http://127.0.0.1:8000"
fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print("  OK ", msg)
    else:
        print(" FAIL", msg)
        fails.append(msg)


def main() -> int:
    print("=== PAGE ===")
    r = requests.get(f"{BASE}/", timeout=10)
    check(r.status_code == 200, f"GET / -> {r.status_code}")
    html = r.text
    for needle in (
        "grid",
        "panel machine",
        "panel preview",
        "panel studio",
        "run-bar",
        "job-card",
        "studio-section",
        "jogStepChips",
        "arm-panel",
        "appModal",
        "btnCreate",
        "btnDry",
        "btnSend",
        "btnCancel",
        "runHint",
        "lockAspect",
        "homeBeforeRun",
        "fonts.googleapis.com",
        "JetBrains+Mono",
        "/static/style.css",
        "/static/app.js",
    ):
        check(needle in html, f"html contains {needle}")

    # Status buttons must be individually gated
    check('data-act="unlock" class="need-conn"' in html or 'class="need-conn" data-act="unlock"' in html
          or re.search(r'data-act="unlock"[^>]*need-conn', html) is not None,
          "Unlock has need-conn")
    check(re.search(r'data-act="home"[^>]*need-conn|need-conn"[^>]*data-act="home"', html) is not None
          or 'data-act="home" class="need-conn"' in html,
          "Home has need-conn")

    print("=== STATIC ===")
    css = requests.get(f"{BASE}/static/style.css", timeout=10)
    js = requests.get(f"{BASE}/static/app.js", timeout=10)
    check(css.status_code == 200 and "run-bar" in css.text, "style.css serves run-bar")
    check("DM Sans" in css.text or '"DM Sans"' in css.text, "style.css uses DM Sans")
    check(js.status_code == 200 and "updateRunUi" in js.text, "app.js has updateRunUi")
    check("askArmConfirm" in js.text, "app.js has askArmConfirm")
    check("ArrowUp" in js.text, "app.js has arrow jog")
    check("setJogStep" in js.text, "app.js has setJogStep")
    check("formatDuration" in js.text, "app.js has formatDuration")

    print("=== API STATUS ===")
    s = requests.get(f"{BASE}/api/device/status", timeout=10).json()
    check(s.get("connected") is False, "starts disconnected")
    check("job_est_seconds" in s, "status has job_est_seconds")
    check("job_remaining_seconds" in s, "status has job_remaining_seconds")

    print("=== JOBS ===")
    for mode in ("fill", "outline"):
        j = requests.post(
            f"{BASE}/api/jobs/from-canvas",
            data={
                "text": "UI",
                "font_name": "Arial",
                "width_mm": "30",
                "height_mm": "20",
                "origin_x": "10",
                "origin_y": "10",
                "preset": "cardboard_test",
                "mode": mode,
                "power_pct": "15",
                "feed": "1000",
            },
            timeout=30,
        )
        check(j.status_code == 200, f"from-canvas {mode} -> {j.status_code}")
        data = j.json()
        check(data.get("est_seconds", 0) > 0, f"{mode} est_seconds={data.get('est_seconds')}")
        check(data.get("lines", 0) > 10, f"{mode} lines={data.get('lines')}")
        g = requests.get(f"{BASE}/api/jobs/{data['id']}/gcode", timeout=10)
        check(g.status_code == 200 and "G1" in g.text, f"{mode} gcode download")

    print("=== FONTS / PRESETS ===")
    fonts = requests.get(f"{BASE}/api/fonts", timeout=10).json()
    check(isinstance(fonts.get("fonts"), list) and len(fonts["fonts"]) >= 1, "fonts list")
    presets = requests.get(f"{BASE}/api/presets", timeout=10).json()
    check(isinstance(presets.get("presets"), list) and len(presets["presets"]) >= 10, "many presets")
    check(presets.get("default") == "cardboard", "default cardboard")
    ids = {p["id"] for p in presets["presets"]}
    check("cardboard" in ids and "leather" in ids and "basswood" in ids, "key materials present")
    card = next(p for p in presets["presets"] if p["id"] == "cardboard")
    check(abs(card["power_pct"] - 25) < 0.1, f"cardboard ~25% (got {card['power_pct']})")

    print()
    if fails:
        print(f"FAILED {len(fails)} checks")
        for f in fails:
            print(" -", f)
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
