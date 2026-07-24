# Ortur Engraver

Local web UI for driving an Ortur (GRBL) diode laser engraver over USB serial. Build jobs from image uploads or a simple canvas, generate G-code, dry-run safely, then arm and send when ready.

![Ortur Engraver UI](docs/screenshot.png)

## Features

- Connect / disconnect to a serial port (close LaserGRBL / LightBurn first)
- Machine status, unlock, home, reset, laser-off
- Jog and frame at `S0` (no burn)
- **ARM / DISARM** gate — live laser jobs are blocked until armed
- Studio workflow: upload or canvas → size/position → material presets → G-code
- Dry run (forces laser off while streaming) and live send with confirm
- Job progress with elapsed / remaining estimates

Default bed size is **400 × 430 mm** (configurable).

## Requirements

- Windows PC with the engraver on USB (Linux/macOS may work with the right serial device path)
- Python 3.11+ recommended
- Ortur / GRBL controller reachable as a COM port (e.g. `COM3`)

## Quick start

```powershell
cd server
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# edit .env — set SERIAL_PORT to your COM port
python run.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

By default the server is localhost-only. In the UI, open **Misc → Available on local network** to bind on your LAN (or set `LAN_ACCESS=true` in `.env` and restart). Prefer `python run.py` so that toggle can restart cleanly.

## Safety

Lasers can injure eyes and start fires. Use eyewear rated for your wavelength, never leave a running job unattended, and keep a fire extinguisher nearby.

**Recommended first session (no burn):**

1. Connect to the COM port
2. Unlock if the controller is in Alarm
3. Jog / **Frame** a small area at `S0`
4. Create a job → **Dry run (laser off)**
5. Only then **ARM** and **Send** for a live job

Dry run never requires arming. Live send does.

## Configuration

Copy `server/.env.example` to `server/.env` (gitignored):

| Variable | Default | Meaning |
|----------|---------|---------|
| `SERIAL_PORT` | `COM3` | USB serial device |
| `SERIAL_BAUD` | `115200` | GRBL baud rate |
| `BED_WIDTH_MM` | `400` | Work area width |
| `BED_HEIGHT_MM` | `430` | Work area height |
| `LAN_ACCESS` | `false` | `true` binds `0.0.0.0` for LAN devices |
| `PORT` | `8000` | HTTP port |

## Layout

```
server/
  run.py         Start server (honors LAN_ACCESS)
  app/           FastAPI app, GRBL serial, G-code generators
  static/        Frontend JS/CSS
  templates/     HTML shell
  scripts/       Smoke / UI test helpers
  .env.example   Config template
docs/
  server.md      Short Windows setup notes
```
