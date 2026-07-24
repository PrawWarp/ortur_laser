# Ortur Engraver (FastAPI)

## Dev (Windows PC)

```powershell
cd c:\code\ortur_laser\server
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # set SERIAL_PORT=COM6
uvicorn app.main:app --app-dir . --host 127.0.0.1 --port 8000 --reload
```

Open http://127.0.0.1:8000

Close LaserGRBL / LightBurn first (exclusive serial port).

### Safe testing (no laser)

1. Connect to COM port
2. Unlock if Alarm
3. Use Jog / Frame 50×50 @ S0
4. Create a job → **Dry run (laser off)** — forces M5/S0 while streaming

Live laser send requires ARM + Send confirm.
