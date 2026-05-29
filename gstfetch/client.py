"""Authenticated HTTP client that replays the GST portal's own JSON APIs."""

from __future__ import annotations

import time
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from gstfetch.endpoints import ResourceSpec
from gstfetch.logging import get_logger

log = get_logger(__name__)


class SessionExpiredError(RuntimeError):
    """Raised when the portal rejects the session — re-run `gstfetch login`."""


class TransientPortalError(RuntimeError):
    """A retryable portal/network error."""


_BROWSER_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://services.gst.gov.in/services/auth/dashboard",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}


class GstClient:
    def __init__(
        self,
        base_url: str,
        cookies: dict[str, str],
        auth_token: str | None,
        *,
        request_delay: float = 1.5,
    ) -> None:
        headers = dict(_BROWSER_HEADERS)
        if auth_token:
            headers["authtoken"] = auth_token
        self._client = httpx.Client(
            base_url=base_url,
            cookies=cookies,
            headers=headers,
            timeout=httpx.Timeout(30.0),
            follow_redirects=False,
        )
        self._delay = request_delay

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GstClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _render(template: str, ctx: dict[str, str]) -> str:
        return template.format(**ctx)

    @retry(
        retry=retry_if_exception_type(TransientPortalError),
        wait=wait_exponential(multiplier=2, min=2, max=16),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def fetch(self, spec: ResourceSpec, ctx: dict[str, str]) -> Any:
        """Call one endpoint and return parsed JSON (or raw text fallback)."""
        path = self._render(spec.path, ctx)
        params = {k: self._render(v, ctx) for k, v in spec.params.items()}
        time.sleep(self._delay)
        try:
            resp = self._client.request(spec.method, path, params=params)
        except httpx.TransportError as exc:
            raise TransientPortalError(f"transport error: {exc}") from exc

        if resp.status_code in (401, 403):
            raise SessionExpiredError(
                f"portal returned {resp.status_code} for {path}; session expired"
            )
        if resp.status_code in (429, 500, 502, 503, 504):
            raise TransientPortalError(f"portal returned {resp.status_code} for {path}")
        if resp.status_code == 302:
            # Portal redirects unauthenticated API calls to the login page.
            raise SessionExpiredError(f"portal redirected {path} to login; session expired")
        resp.raise_for_status()

        try:
            return resp.json()
        except ValueError:
            return {"_raw_text": resp.text}


def count_records(payload: Any, records_key: str | None) -> int | None:
    """Best-effort record count following a dotted ``records_key``."""
    if records_key is None or not isinstance(payload, dict):
        return None
    cur: Any = payload
    for part in records_key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    if isinstance(cur, list):
        return len(cur)
    if isinstance(cur, dict):
        return len(cur)
    return None
