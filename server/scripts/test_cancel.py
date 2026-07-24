import time
import requests

base = "http://127.0.0.1:8000"

assert requests.get(base).status_code == 200
fonts = requests.get(f"{base}/api/fonts").json()["fonts"]
print("fonts", fonts[:5], "...", len(fonts))

# reconnect
ports = requests.get(f"{base}/api/device/ports").json()
port = ports["ports"][0]["device"] if ports["ports"] else "COM3"
s = requests.post(f"{base}/api/device/connect", json={"port": port}).json()
print("connected", s.get("connected"), (s.get("identity") or "")[:50], s)
if not s.get("connected"):
    raise SystemExit(f"connect failed: {s}")
# unlock if soft-reset left alarm
requests.post(f"{base}/api/device/unlock", json={})

# create larger dry job then cancel mid-way
r = requests.post(
    f"{base}/api/jobs/from-canvas",
    data={
        "text": "Demo",
        "font_name": "Arial",
        "width_mm": "30",
        "height_mm": "20",
        "origin_x": "0",
        "origin_y": "0",
        "preset": "cardboard_test",
    },
)
r.raise_for_status()
job = r.json()
print("job lines", job["lines"])

requests.post(f"{base}/api/jobs/{job['id']}/send-dry")
time.sleep(1.5)
s = requests.get(f"{base}/api/device/status").json()
print("before cancel running", s["job_running"], f"{s['job_lines_sent']}/{s['job_lines_total']}")
assert s["job_running"], "expected job to be running before cancel"

t0 = time.time()
s = requests.post(f"{base}/api/device/abort").json()
elapsed = time.time() - t0
print("abort response", elapsed, "s", s["last_message"], "running", s["job_running"])
assert elapsed < 3.0, "abort should return quickly"
assert not s["job_running"]

time.sleep(1)
s = requests.get(f"{base}/api/device/status").json()
print("after", s["state"], s["last_message"], s["job_running"])
print("OK")
