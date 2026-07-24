import requests

b = "http://127.0.0.1:8000"
for mode in ("fill", "outline"):
    j = requests.post(
        f"{b}/api/jobs/from-canvas",
        data={
            "text": "Demo",
            "font_name": "Arial",
            "width_mm": "54",
            "height_mm": "34",
            "origin_x": "77",
            "origin_y": "64",
            "preset": "cardboard_test",
            "mode": mode,
        },
    ).json()
    g = requests.get(f"{b}/api/jobs/{j['id']}/gcode").text
    burns = sum(
        1
        for ln in g.splitlines()
        if ln.startswith("G1") and " S" in ln and not ln.rstrip().endswith("S0")
    )
    print(mode, "total_lines", j["lines"], "burn_moves", burns, "mode_field", j.get("mode"))
