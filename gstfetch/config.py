"""Configuration loaded from environment / .env."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Final

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

GSTIN_REGEX: Final[re.Pattern[str]] = re.compile(
    r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$"
)
PERIOD_REGEX: Final[re.Pattern[str]] = re.compile(r"^(0[1-9]|1[0-2])[0-9]{4}$")

GST_BASE_URL: Final[str] = "https://services.gst.gov.in"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GSTFETCH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gstin: str = "27AAACX1234F1Z5"
    start_period: str = "072017"
    data_dir: Path = Path("./data")
    session_file: Path = Path("./session_state.json")
    request_delay: float = 1.5
    headed: bool = True
    base_url: str = GST_BASE_URL

    @field_validator("gstin")
    @classmethod
    def _check_gstin(cls, v: str) -> str:
        v = v.strip().upper()
        if not GSTIN_REGEX.match(v):
            raise ValueError(f"invalid GSTIN: {v!r}")
        return v

    @field_validator("start_period")
    @classmethod
    def _check_period(cls, v: str) -> str:
        v = v.strip()
        if not PERIOD_REGEX.match(v):
            raise ValueError(f"start_period must be MMYYYY (e.g. 072017), got {v!r}")
        return v

    @property
    def state_code(self) -> str:
        return self.gstin[0:2]

    @property
    def db_path(self) -> Path:
        return self.data_dir / "checkpoints.sqlite"

    @property
    def client_dir(self) -> Path:
        return self.data_dir / self.gstin


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
