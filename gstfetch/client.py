"""Authenticated HTTP client that replays the GST portal's own JSON APIs."""

from __future__ import annotations

import time
from typing import Any, Protocol

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


class WafBlockedError(RuntimeError):
    """The portal's web-application firewall rejected the request.

    The GST portal sits behind an F5 BIG-IP ASM firewall that serves a
    ``Request Rejected`` HTML page (with an HTTP 200 status) when a request
    does not look like it came from a real browser session. This is fatal for
    the run — copied cookies cannot satisfy it — so we stop and tell the user
    to fetch from inside the live browser session instead.
    """


class TransientPortalError(RuntimeError):
    """A retryable portal/network error."""


class Fetcher(Protocol):
    """Anything that can fetch one resource's payload.

    Implemented by :class:`GstClient` (httpx replay) and ``BrowserClient``
    (page-context fetch); the orchestrator only needs this method.
    """

    def fetch(self, spec: ResourceSpec, ctx: dict[str, str]) -> Any: ...


def looks_like_waf_block(text: str) -> bool:
    """True if ``text`` is the F5 ``Request Rejected`` firewall page.

    The portal returns this with a 200 status, so it cannot be distinguished
    by HTTP code alone — we have to sniff the body. The page reliably contains
    both a "Request Rejected" title and a numeric "support ID".
    """
    if not text:
        return False
    lowered = text.lower()
    return "request rejected" in lowered and "support id" in lowered


_BROWSER_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://services.gst.gov.in/services/auth/dashboard",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "X-Requested-With": "XMLHttpRequest",
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

    @staticmethod
    def render_path(spec: ResourceSpec, ctx: dict[str, str]) -> str:
        """Render a spec's path + query string for ``ctx`` (no base URL)."""
        from urllib.parse import urlencode

        path = spec.path.format(**ctx)
        params = {k: v.format(**ctx) for k, v in spec.params.items()}
        query = urlencode(params)
        return f"{path}?{query}" if query else path

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

        # The F5 firewall serves its "Request Rejected" page with a 200 status,
        # so guard against silently storing it as if it were real data.
        if looks_like_waf_block(resp.text):
            raise WafBlockedError(
                f"firewall rejected {path}; copied-cookie replay is being blocked. "
                "Use the in-browser fetch (`gstfetch fetch` drives the logged-in browser)."
            )

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
