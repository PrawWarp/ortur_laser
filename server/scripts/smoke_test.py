import time
import requests

base = "http://127.0.0.1:8000"

s = requests.get(f"{base}/api/device/status").json()
print("connected", s["connected"], "state", s["state"])
print("identity", s["identity"][:80])

print("FRAME 20x20 S0...")
r = requests.post(
    f"{base}/api/device/frame",
    json={"width_mm": 20, "height_mm": 20, "origin_x": 0, "origin_y": 0, "feed": 2000},
)
print(r.status_code, r.json().get("last_message"))
for i in range(90):
    s = requests.get(f"{base}/api/device/status").json()
    print(
        f"  frame {i}: running={s['job_running']} "
        f"{s['job_lines_sent']}/{s['job_lines_total']} {s['last_message']} mpos={s['mpos']}"
    )
    if not s["job_running"] and s["job_lines_sent"] > 0:
        break
    if s.get("job_error"):
        raise SystemExit(f"frame error: {s['job_error']}")
    time.sleep(0.4)

print("CREATE JOB...")
r = requests.post(
    f"{base}/api/jobs/from-canvas",
    data={
        "text": "T",
        "width_mm": "5",
        "height_mm": "5",
        "origin_x": "0",
        "origin_y": "0",
        "preset": "cardboard_test",
    },
)
r.raise_for_status()
job = r.json()
print(job)
g = requests.get(f"{base}/api/jobs/{job['id']}/gcode").text
print("GCODE sample:\n" + "\n".join(g.splitlines()[:12]))
assert "M5" in g and "G1" in g

print("DRY RUN...")
r = requests.post(f"{base}/api/jobs/{job['id']}/send-dry")
print(r.status_code, r.json().get("last_message"))
for i in range(300):
    s = requests.get(f"{base}/api/device/status").json()
    if i % 15 == 0 or not s["job_running"]:
        print(
            f"  dry {i}: running={s['job_running']} "
            f"{s['job_lines_sent']}/{s['job_lines_total']} {s['last_message']}"
        )
    if not s["job_running"] and s["job_lines_sent"] > 0:
        break
    if s.get("job_error"):
        raise SystemExit(f"dry error: {s['job_error']}")
    time.sleep(0.4)

s = requests.get(f"{base}/api/device/status").json()
print("FINAL", s["state"], s["last_message"], s["mpos"], "error", s["job_error"])
print("UI", requests.get(base).status_code)
print("OK")
