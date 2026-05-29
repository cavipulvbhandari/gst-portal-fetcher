"""Manual-login session handoff via Playwright.

The GST portal requires CAPTCHA + OTP login that must be solved by a human.
This module launches a real browser, lets *you* log in, then captures the
authenticated session (cookies + the ``authtoken`` header the portal sends on
its XHR calls) so the rest of the tool can replay the portal's own JSON APIs.

Nothing here automates CAPTCHA solving or credential entry — the human is in
the loop, which is exactly what keeps this on the right side of the portal's
terms when run by an authorised user against their own client's account.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.sync_api import Request, sync_playwright

from gstfetch.client import (
    SessionExpiredError,
    WafBlockedError,
    looks_like_waf_block,
)
from gstfetch.endpoints import ResourceSpec
from gstfetch.logging import get_logger

log = get_logger(__name__)

LOGIN_URL = "https://services.gst.gov.in/services/login"
DASHBOARD_HINT = "/services/auth/dashboard"
GST_ORIGIN = "https://services.gst.gov.in"


@dataclass(frozen=True, slots=True)
class CapturedSession:
    storage_state_path: Path
    auth_token: str | None


def _attach_token_sniffer(context: Any, token_box: dict[str, str]) -> None:
    """Record the ``authtoken`` header from the first authenticated XHR."""

    def on_request(request: Request) -> None:
        if "authtoken" in token_box:
            return
        token = request.headers.get("authtoken")
        if token:
            token_box["authtoken"] = token
            log.info("captured authtoken from %s", request.url)

    context.on("request", on_request)


def login(session_file: Path, *, headed: bool = True, base_url: str = LOGIN_URL) -> CapturedSession:
    """Open a browser, wait for the user to log in, then persist the session.

    Detection of "logged in" is best-effort: we watch for navigation to the
    authenticated dashboard. If that heuristic misses, the user simply presses
    Enter in the terminal to snapshot the session manually.
    """
    token_box: dict[str, str] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        context = browser.new_context()
        _attach_token_sniffer(context, token_box)
        page = context.new_page()
        page.goto(base_url, wait_until="domcontentloaded")

        log.info("Log in to the GST portal in the opened browser (CAPTCHA + OTP).")
        log.info("Waiting for the dashboard to load...")
        try:
            page.wait_for_url(f"**{DASHBOARD_HINT}**", timeout=300_000)
            log.info("Dashboard detected.")
        except Exception:
            log.warning("Dashboard not auto-detected within timeout.")

        # Give the user a final chance to navigate so a token-bearing XHR fires.
        input("Press Enter here once you are fully logged in to snapshot the session... ")

        session_file.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(session_file))
        log.info("Saved session state -> %s", session_file)
        browser.close()

    token = token_box.get("authtoken")
    if token:
        token_file = session_file.with_name("auth_token.txt")
        token_file.write_text(token, encoding="utf-8")
        log.info("Saved authtoken -> %s", token_file)
    else:
        log.warning(
            "No authtoken captured. Run `gstfetch capture` and open a few pages, "
            "or the fetch step will retry token capture."
        )
    return CapturedSession(storage_state_path=session_file, auth_token=token)


def capture(
    session_file: Path, har_path: Path, *, base_url: str = DASHBOARD_HINT
) -> dict[str, str]:
    """Replay a saved session and record all API traffic to a HAR file.

    Use this to discover/verify the portal's internal endpoints (they are
    undocumented and change over time). Navigate through the screens whose
    data you want; every XHR is logged to ``har_path`` and the distinct API
    paths are returned.
    """
    if not session_file.exists():
        raise FileNotFoundError(f"no session file at {session_file}; run `gstfetch login` first")

    endpoints: dict[str, str] = {}
    token_box: dict[str, str] = {}
    har_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(
            storage_state=str(session_file),
            record_har_path=str(har_path),
        )
        _attach_token_sniffer(context, token_box)

        def on_request(request: Request) -> None:
            if "/services/api/" in request.url or request.resource_type == "xhr":
                path = request.url.split("?")[0]
                endpoints[path] = request.method

        context.on("request", on_request)
        page = context.new_page()
        page.goto("https://services.gst.gov.in" + DASHBOARD_HINT, wait_until="domcontentloaded")
        input("Navigate the portal, then press Enter to stop capture... ")
        context.close()
        browser.close()

    if token_box.get("authtoken"):
        session_file.with_name("auth_token.txt").write_text(
            token_box["authtoken"], encoding="utf-8"
        )
    catalog_path = har_path.with_name("discovered_endpoints.json")
    catalog_path.write_text(json.dumps(endpoints, indent=2, sort_keys=True), encoding="utf-8")
    log.info("Wrote HAR -> %s and %d endpoints -> %s", har_path, len(endpoints), catalog_path)
    return endpoints


class BrowserClient:
    """Fetch the portal's JSON APIs from inside the live logged-in browser.

    Copied cookies replayed over httpx are rejected by the portal's F5
    firewall (it fingerprints the TLS handshake and relies on short-lived
    JavaScript-refreshed bot cookies). Instead we reopen the saved session in
    a real Chromium page and issue each API call with the page's own
    ``fetch()`` — so every request carries the genuine browser fingerprint,
    the live Akamai/F5 cookies, and the real header set, exactly as the
    portal's own SPA does. No fingerprint spoofing, no CAPTCHA automation.

    Use as a context manager so the browser is always closed::

        with BrowserClient(session_file, headed=True) as client:
            payload = client.fetch(spec, ctx)
    """

    def __init__(
        self,
        session_file: Path,
        *,
        headed: bool = True,
        request_delay: float = 1.5,
        origin: str = GST_ORIGIN,
    ) -> None:
        if not session_file.exists():
            raise FileNotFoundError(
                f"no session file at {session_file}; run `gstfetch login` first"
            )
        self._session_file = session_file
        self._headed = headed
        self._delay = request_delay
        self._origin = origin
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

    def __enter__(self) -> BrowserClient:
        self._pw = sync_playwright().start()
        # Headed by default: the portal's bot management is far likelier to
        # trust a visible browser, and the user is already in the loop.
        self._browser = self._pw.chromium.launch(headless=not self._headed)
        self._context = self._browser.new_context(storage_state=str(self._session_file))
        self._page = self._context.new_page()
        # Land on the authenticated dashboard so in-page JS sets/refreshes the
        # firewall cookies before we start issuing API calls.
        self._page.goto(self._origin + DASHBOARD_HINT, wait_until="domcontentloaded")
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        for closer in (self._context, self._browser):
            if closer is not None:
                try:
                    closer.close()
                except Exception:  # noqa: BLE001 — best-effort teardown
                    pass
        if self._pw is not None:
            self._pw.stop()
        self._context = self._browser = self._pw = self._page = None

    def fetch(self, spec: ResourceSpec, ctx: dict[str, str]) -> Any:
        """Issue one API call from the page context and return parsed JSON."""
        from gstfetch.client import GstClient

        if self._page is None:
            raise RuntimeError("BrowserClient must be used as a context manager")
        time.sleep(self._delay)
        url = self._origin + GstClient.render_path(spec, ctx)
        result = self._page.evaluate(_PAGE_FETCH_JS, {"url": url, "method": spec.method})

        status = int(result.get("status", 0))
        body = result.get("body", "") or ""
        if status in (401, 403):
            raise SessionExpiredError(f"portal returned {status} for {url}; session expired")
        if looks_like_waf_block(body):
            raise WafBlockedError(
                f"firewall rejected {url} even from the live browser; "
                "the session may be stale — run `gstfetch login` again."
            )
        try:
            return json.loads(body)
        except (ValueError, TypeError):
            return {"_raw_text": body}


# Runs in the page: same-origin fetch with the portal's own credentials/cookies.
_PAGE_FETCH_JS = """
async ({url, method}) => {
    const resp = await fetch(url, {
        method: method || 'GET',
        credentials: 'include',
        headers: {
            'Accept': 'application/json, text/plain, */*',
            'X-Requested-With': 'XMLHttpRequest',
        },
    });
    const body = await resp.text();
    return {status: resp.status, body};
}
"""


def load_cookies(session_file: Path) -> dict[str, str]:
    """Read the Playwright storage-state cookies into a name->value dict."""
    if not session_file.exists():
        raise FileNotFoundError(f"no session file at {session_file}; run `gstfetch login` first")
    state = json.loads(session_file.read_text(encoding="utf-8"))
    return {c["name"]: c["value"] for c in state.get("cookies", [])}


def load_auth_token(session_file: Path) -> str | None:
    token_file = session_file.with_name("auth_token.txt")
    if token_file.exists():
        return token_file.read_text(encoding="utf-8").strip() or None
    return None
