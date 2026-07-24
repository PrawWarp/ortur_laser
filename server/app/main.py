from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.routes import router
from app.config import APP_VERSION, settings

BASE = Path(__file__).resolve().parent.parent

app = FastAPI(title="Ortur Engraver", version=APP_VERSION)
app.include_router(router, prefix="/api")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "default_port": settings.serial_port,
            "bed_w": settings.bed_width_mm,
            "bed_h": settings.bed_height_mm,
            "app_version": APP_VERSION,
        },
    )
