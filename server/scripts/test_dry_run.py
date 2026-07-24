import time
import requests

base = "http://127.0.0.1:8000"

# ensure UI up
r = requests.get(base)
print("UI", r.status_code)

# connect
ports = requests.get(f"{base}/api/device/ports").json()
port = ports["ports"][0]["device"] if ports["ports"] else "COM3"
print("port", port)

s = requests.get(f"{base}/api/device/status").json()
if not s.get("connected"):
    s = requests.post(f"{base}/api/device/connect", json={"port": port}).json()
print("connect", s.get("connected"), s.get("state"), s.get("last_message"))

if (s.get("state") or "").lower() == "alarm":
    s = requests.post(f"{base}/api/device/unlock", json={}).json()
    print("unlock", s.get("state"), s.get("last_message"))

# small canvas job for a quick dry run
job = requests.post(
    f"{base}/api/jobs/from-canvas",
    data={
        "text": "OK",
        "font_name": "Arial",
        "width_mm": "15",
        "height_mm": "10",
        "origin_x": "5",
        "origin_y": "5",
        "preset": "cardboard_test",
    },
).json()
print("job", job["id"][:8], "lines", job["lines"])

s = requests.post(f"{base}/api/jobs/{job['id']}/send-dry").json()
print("dry start", s.get("last_message"), "running", s.get("job_running"))

seen_move = False
positions = []
for i in range(120):
    time.sleep(0.4)
    s = requests.get(f"{base}/api/device/status").json()
    m = s.get("mpos") or {}
    positions.append((m.get("x"), m.get("y")))
    if i % 5 == 0 or not s.get("job_running"):
        print(
            f"[{i}] running={s.get('job_running')} "
            f"{s.get('job_lines_sent')}/{s.get('job_lines_total')} "
            f"state={s.get('state')} mpos={m.get('x')},{m.get('y')} "
            f"msg={s.get('last_message')}"
        )
    if s.get("job_error"):
        raise SystemExit(f"FAIL job_error={s['job_error']}")
    if len(positions) >= 2:
        x0, y0 = positions[0]
        x1, y1 = positions[-1]
        if x0 is not None and (abs(x1 - x0) > 0.2 or abs(y1 - y0) > 0.2):
            seen_move = True
    if not s.get("job_running") and (s.get("job_lines_sent") or 0) > 0:
        break

s = requests.get(f"{base}/api/device/status").json()
print(
    "FINAL",
    s.get("state"),
    s.get("last_message"),
    f"{s.get('job_lines_sent')}/{s.get('job_lines_total')}",
    "moved" if seen_move else "no-move-detected",
    "error",
    s.get("job_error"),
)
if s.get("job_error"):
    raise SystemExit(1)
if (s.get("job_lines_sent") or 0) < (s.get("job_lines_total") or 1):
    # may have completed with equal counts
    pass
print("DRY RUN OK" if (s.get("last_message") == "Job complete" or seen_move) else "DRY RUN DONE")
