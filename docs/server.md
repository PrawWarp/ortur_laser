# Ortur Engraver (FastAPI)

## Easiest (Windows)

```powershell
irm https://raw.githubusercontent.com/PrawWarp/ortur_laser/main/get.ps1 | iex
```

Needs Git + Python 3 on PATH. Starts at http://127.0.0.1:8000

Later: `~\ortur_laser\run.ps1`

## Manual (already cloned)

```powershell
.\scripts\install.ps1
.\run.ps1
```

In the UI: **Find** probes USB serial ports for GRBL/Ortur, or leave **Auto — find laser** and hit **Connect**.

To reach the UI from a phone/tablet on the same Wi‑Fi: **Settings** or **Misc → Available on local network** (restarts the server), or set `LAN_ACCESS=true` in `.env`.

Close LaserGRBL / LightBurn first (exclusive serial port).

## Raspberry Pi / Linux

```bash
curl -fsSL https://raw.githubusercontent.com/PrawWarp/ortur_laser/main/get.sh | bash
```

Details: [pi.md](pi.md).

### Safe testing (no laser)

1. Find / Connect to the port
2. Unlock if Alarm
3. Use Jog / Frame 50×50 @ S0
4. Create a job → **Dry run (laser off)** — forces M5/S0 while streaming

Live laser send requires ARM + Send confirm.
