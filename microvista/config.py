"""Configuration for the Microvista Notice Alert fetcher.

Loaded from the environment / ``.env`` with the ``MICROVISTA_`` prefix. Unlike
the official GST portal, Microvista is a normal web app with email + password
login, so credentials live here (keep ``.env`` out of version control).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_URL: Final[str] = "https://noticealert.microvistatech.com"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MICROVISTA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Login credentials. Leave blank to log in manually in the opened browser
    # (useful if the portal ever adds OTP / a captcha).
    email: str = ""
    password: str = ""

    base_url: str = BASE_URL

    # Where PDFs and the per-client manifest are written.
    data_dir: Path = Path("./data/microvista")

    # Reusable authenticated browser session (Playwright storage state).
    session_file: Path = Path("./microvista_session.json")

    # Optional comma-separated GSTIN filter. Empty = every client on the portal.
    gstins: str = ""

    # Run the browser visibly. Recommended so you can watch / help it along.
    headed: bool = True

    # Polite pause (seconds) between page actions.
    request_delay: float = 1.0

    # Per-action navigation timeout in seconds.
    timeout: float = 60.0

    @field_validator("base_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def gstin_filter(self) -> set[str]:
        return {g.strip().upper() for g in self.gstins.split(",") if g.strip()}

    @property
    def timeout_ms(self) -> int:
        return int(self.timeout * 1000)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
