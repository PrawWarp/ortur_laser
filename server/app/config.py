from pathlib import Path
import platform

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV = Path(__file__).resolve().parent.parent / ".env"

# Bump when shipping user-visible changes (shown in the UI header).
APP_VERSION = "0.2.1"


def _default_serial_hint() -> str:
    system = platform.system().lower()
    if system == "windows":
        return "COM3"
    if system == "darwin":
        return "/dev/cu.usbserial-0001"
    return "/dev/ttyUSB0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV), env_file_encoding="utf-8", extra="ignore")

    # "auto" = probe serial ports for a GRBL/Ortur laser (Windows + Linux/Pi)
    serial_port: str = "auto"
    serial_baud: int = 115200
    bed_width_mm: float = 400.0
    bed_height_mm: float = 430.0
    # When true, bind 0.0.0.0 so phones/tablets on the LAN can open the UI.
    lan_access: bool = False
    port: int = 8000

    @property
    def bind_host(self) -> str:
        return "0.0.0.0" if self.lan_access else "127.0.0.1"

    @property
    def serial_port_display(self) -> str:
        raw = (self.serial_port or "auto").strip()
        if raw.lower() == "auto":
            return _default_serial_hint()
        return raw


settings = Settings()

# Canonical defaults for UI reset (matches field defaults above / .env.example).
SETTINGS_DEFAULTS: dict[str, str | int | float | bool] = {
    "serial_port": "auto",
    "serial_baud": 115200,
    "bed_width_mm": 400.0,
    "bed_height_mm": 430.0,
    "lan_access": False,
    "port": 8000,
}
