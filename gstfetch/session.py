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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.sync_api import Request, sync_playwright

from gstfetch.logging import get_logger

log = get_logger(__name__)

LOGIN_URL = "https://services.gst.gov.in/services/login"
DASHBOARD_HINT = "/services/auth/dashboard"


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
