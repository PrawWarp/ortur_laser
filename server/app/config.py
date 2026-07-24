from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV), env_file_encoding="utf-8", extra="ignore")

    serial_port: str = "COM6"
    serial_baud: int = 115200
    bed_width_mm: float = 400.0
    bed_height_mm: float = 430.0
    host: str = "127.0.0.1"
    port: int = 8000


settings = Settings()
