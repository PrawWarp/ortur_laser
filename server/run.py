"""Start the Ortur Engraver UI (respects LAN_ACCESS in .env)."""
from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from app import runtime
from app.config import settings

SERVER_DIR = Path(__file__).resolve().parent


def main() -> None:
    os.chdir(SERVER_DIR)
    host = settings.bind_host
    port = settings.port
    runtime.bind_host = host
    runtime.port = port
    print(f"Ortur Engraver listening on http://{host}:{port}")
    if settings.lan_access:
        print("LAN access ON — other devices on your network can open this UI.")
    else:
        print("LAN access OFF — localhost only. Toggle in Misc, or set LAN_ACCESS=true in .env.")
    uvicorn.run("app.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
