"""Browser automation for the Microvista Notice Alert portal.

Flow mirrors the manual clicks a user makes in the UI:

1. **login** — open the portal, sign in with email + password (or by hand),
   and save the authenticated browser session for reuse.
2. **fetch** — reopen that session, list every client GSTIN on the GST
   dashboard, open each one's *Notices & Orders* table, and download the notice
   PDFs (the red Adobe / PDF icon in the Action column). Row metadata is written
   to a ``notices.json`` manifest per client.

The portal is a single-page app, so navigation is driven through the visible UI
(text + role selectors) rather than a documented API. The CSS/text selectors are
collected as module constants near the top so they are easy to adjust if the
portal's markup changes.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from playwright.sync_api import (
    Download,
    Locator,
    Page,
    sync_playwright,
)
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
)

from microvista.logging import get_logger

log = get_logger(__name__)

# A GSTIN: 2-digit state code, 5 letters, 4 digits, a letter, an entity digit,
# the literal 'Z', and a checksum char.
GSTIN_RE = re.compile(r"\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]\b")
# Microvista notice reference ids look like ZD2711250732895.
REF_ID_RE = re.compile(r"\b[A-Z]{2}[0-9A-Z]{8,}\b")

# --- Selectors (adjust here if the portal markup changes) -------------------
# Login form.
EMAIL_SELECTORS = (
    "input[type='email']",
    "input[name='email']",
    "input[name='username']",
    "input[placeholder*='mail' i]",
    "input[placeholder*='user' i]",
)
PASSWORD_SELECTORS = (
    "input[type='password']",
    "input[name='password']",
    "input[placeholder*='password' i]",
)
LOGIN_BUTTON_SELECTORS = (
    "button[type='submit']",
    "button:has-text('Login')",
    "button:has-text('Log in')",
    "button:has-text('Sign in')",
    "input[type='submit']",
)

# The "Notice" item in the top navigation of a company view.
NOTICE_NAV_SELECTORS = (
    "a:has-text('Notice')",
    "button:has-text('Notice')",
    "[href*='notice' i]",
)

# Within a table row's Action cell, anything that downloads / opens a PDF.
PDF_TRIGGER_SELECTORS = (
    "a[href$='.pdf']",
    "a[href*='.pdf?']",
    "a[href*='.pdf#']",
    "img[src*='pdf' i]",
    "img[alt*='pdf' i]",
    "[class*='pdf' i]",
    "[title*='pdf' i]",
    "[title*='download' i]",
    "[aria-label*='download' i]",
)

# The pager's "next page" control on the notices table.
NEXT_PAGE_SELECTORS = (
    "button[aria-label*='next' i]",
    "[aria-label='Go to next page']",
    "button:has-text('>')",
)


@dataclass(slots=True)
class NoticeRow:
    """One row of a client's Notices & Orders table."""

    ref_id: str
    status: str = ""
    section: str = ""
    notice_type: str = ""
    issued_date: str = ""
    due_date: str = ""
    cells: list[str] = field(default_factory=list)
    pdf_file: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sanitize(name: str) -> str:
    """Make a string safe to use as a filename."""
    cleaned = re.sub(r"[^\w.\-]+", "_", name).strip("_")
    return cleaned or "notice"


def _first_visible(page_or_locator: Any, selectors: tuple[str, ...]) -> Locator | None:
    """Return the first selector in *selectors* that resolves to a visible node."""
    for sel in selectors:
        loc: Locator = page_or_locator.locator(sel).first
        try:
            if loc.count() > 0 and loc.is_visible():
                return loc
        except PlaywrightTimeoutError:
            continue
    return None


# --------------------------------------------------------------------------- #
# Login
# --------------------------------------------------------------------------- #
def login(
    *,
    base_url: str,
    session_file: Path,
    email: str,
    password: str,
    headed: bool = True,
    timeout_ms: int = 60_000,
) -> None:
    """Sign in and persist the authenticated session to *session_file*.

    If *email*/*password* are provided the form is filled automatically;
    otherwise (or if auto-login can't confirm success) you finish logging in by
    hand in the opened window and press Enter in the terminal.
    """
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(timeout_ms)
        page.goto(base_url, wait_until="domcontentloaded")

        logged_in = False
        if email and password:
            logged_in = _auto_login(page, email, password, timeout_ms)

        if not logged_in:
            log.info(
                "Finish logging in the opened browser window "
                "(and pick your account if prompted)."
            )
            input("Press Enter here once you are on the dashboard... ")

        session_file.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(session_file))
        log.info("Saved session -> %s", session_file)
        browser.close()


def _auto_login(page: Page, email: str, password: str, timeout_ms: int) -> bool:
    """Best-effort automatic credential login. Returns True if it looks signed in."""
    email_box = _first_visible(page, EMAIL_SELECTORS)
    pw_box = _first_visible(page, PASSWORD_SELECTORS)
    if email_box is None or pw_box is None:
        log.warning("Could not find the login form automatically.")
        return False

    email_box.fill(email)
    pw_box.fill(password)
    button = _first_visible(page, LOGIN_BUTTON_SELECTORS)
    if button is not None:
        button.click()
    else:
        pw_box.press("Enter")

    # Treat leaving the login screen (a dashboard URL appears) as success.
    try:
        page.wait_for_url("**/dashboard/**", timeout=timeout_ms)
        log.info("Logged in.")
        return True
    except PlaywrightTimeoutError:
        log.warning("Auto-login did not reach the dashboard in time.")
        return False


# --------------------------------------------------------------------------- #
# Fetch
# --------------------------------------------------------------------------- #
def fetch(
    *,
    base_url: str,
    session_file: Path,
    data_dir: Path,
    gstin_filter: set[str],
    headed: bool = True,
    request_delay: float = 1.0,
    timeout_ms: int = 60_000,
    dry_run: bool = False,
) -> dict[str, int]:
    """Download notice PDFs for every (filtered) client GSTIN.

    Returns a summary dict: ``{clients, notices, pdfs}``.
    """
    if not session_file.exists():
        raise FileNotFoundError(
            f"no session at {session_file}; run `microvista login` first"
        )

    summary = {"clients": 0, "notices": 0, "pdfs": 0}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        context = browser.new_context(
            storage_state=str(session_file), accept_downloads=True
        )
        page = context.new_page()
        page.set_default_timeout(timeout_ms)

        page.goto(f"{base_url}/gst-dashboard", wait_until="domcontentloaded")
        _pause(request_delay)
        companies = _collect_companies(page)
        if not companies:
            raise RuntimeError(
                "No client GSTINs found on the GST dashboard. The session may "
                "have expired — run `microvista login` again."
            )

        if gstin_filter:
            companies = [c for c in companies if c[0] in gstin_filter]
        log.info("Found %d client(s) to process.", len(companies))

        for gstin, href in companies:
            summary["clients"] += 1
            rows = _process_company(
                page,
                base_url=base_url,
                gstin=gstin,
                href=href,
                out_dir=data_dir / gstin,
                request_delay=request_delay,
                timeout_ms=timeout_ms,
                dry_run=dry_run,
            )
            summary["notices"] += len(rows)
            summary["pdfs"] += sum(1 for r in rows if r.pdf_file)

        browser.close()

    return summary


def _collect_companies(page: Page) -> list[tuple[str, str]]:
    """Return ``(gstin, href)`` for each client link on the GST dashboard."""
    page.wait_for_load_state("networkidle")
    anchors = page.locator("a").all()
    seen: dict[str, str] = {}
    for a in anchors:
        try:
            text = (a.inner_text() or "").strip()
        except PlaywrightTimeoutError:
            continue
        m = GSTIN_RE.search(text)
        if not m:
            continue
        gstin = m.group(0)
        if gstin in seen:
            continue
        href = a.get_attribute("href") or ""
        seen[gstin] = href
    return list(seen.items())


def _process_company(
    page: Page,
    *,
    base_url: str,
    gstin: str,
    href: str,
    out_dir: Path,
    request_delay: float,
    timeout_ms: int,
    dry_run: bool,
) -> list[NoticeRow]:
    log.info("[%s] opening company dashboard", gstin)
    # Open the company dashboard (the href carries the encoded company token),
    # then click through to its Notices list exactly like a user would.
    if href:
        target = href if href.startswith("http") else f"{base_url}/{href.lstrip('/')}"
        page.goto(target, wait_until="domcontentloaded")
    _pause(request_delay)

    nav = _first_visible(page, NOTICE_NAV_SELECTORS)
    if nav is not None:
        nav.click()
    else:
        page.goto(f"{base_url}/notice", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")
    _pause(request_delay)

    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    all_rows: list[NoticeRow] = []
    page_num = 1
    while True:
        rows = _process_notice_page(
            page,
            gstin=gstin,
            out_dir=out_dir,
            request_delay=request_delay,
            timeout_ms=timeout_ms,
            dry_run=dry_run,
        )
        all_rows.extend(rows)
        log.info("[%s] page %d: %d notice row(s)", gstin, page_num, len(rows))
        if not _go_to_next_page(page, request_delay):
            break
        page_num += 1

    if not dry_run:
        manifest = out_dir / "notices.json"
        manifest.write_text(
            json.dumps([r.to_dict() for r in all_rows], indent=2), encoding="utf-8"
        )
        log.info("[%s] wrote manifest (%d rows) -> %s", gstin, len(all_rows), manifest)
    return all_rows


def _process_notice_page(
    page: Page,
    *,
    gstin: str,
    out_dir: Path,
    request_delay: float,
    timeout_ms: int,
    dry_run: bool,
) -> list[NoticeRow]:
    """Scrape and download every PDF on the currently-displayed notices page."""
    rows = _scrape_notice_rows(page)
    row_locators = _notice_row_locators(page)
    for row_data, row_loc in zip(rows, row_locators, strict=False):
        trigger = _first_visible(row_loc, PDF_TRIGGER_SELECTORS)
        if trigger is None:
            continue
        if dry_run:
            log.info("[%s] would download PDF for %s", gstin, row_data.ref_id)
            row_data.pdf_file = "(dry-run)"
            continue
        dest = out_dir / f"{_sanitize(row_data.ref_id or 'notice')}.pdf"
        if _capture_pdf(page, trigger, dest, timeout_ms):
            row_data.pdf_file = dest.name
            log.info("[%s] saved %s", gstin, dest.name)
        else:
            log.warning("[%s] could not download PDF for %s", gstin, row_data.ref_id)
        _pause(request_delay)
    return rows


def _go_to_next_page(page: Page, request_delay: float) -> bool:
    """Advance to the next page of the notices table; False when on the last page."""
    nxt = _first_visible(page, NEXT_PAGE_SELECTORS)
    if nxt is None:
        return False
    try:
        disabled = nxt.get_attribute("disabled") is not None or (
            (nxt.get_attribute("aria-disabled") or "").lower() == "true"
        )
    except PlaywrightTimeoutError:
        disabled = True
    if disabled or not nxt.is_enabled():
        return False
    nxt.click()
    page.wait_for_load_state("networkidle")
    _pause(request_delay)
    return True


def _notice_row_locators(page: Page) -> list[Locator]:
    """Table body rows of the notices table (data rows only)."""
    rows = page.locator("table tbody tr")
    if rows.count() == 0:
        # Some grids use role-based markup instead of a <table>.
        rows = page.locator("[role='row']")
    return rows.all()


def _scrape_notice_rows(page: Page) -> list[NoticeRow]:
    """Read each notice row's cell text into a :class:`NoticeRow`."""
    result: list[NoticeRow] = []
    for row in _notice_row_locators(page):
        cells = [c.strip() for c in row.locator("td, [role='cell']").all_inner_texts()]
        cells = [c for c in cells if c != ""]
        if not cells:
            continue
        joined = " ".join(cells)
        ref_m = REF_ID_RE.search(joined)
        status = next(
            (c for c in cells if c.lower() in {"open", "closed", "replied", "pending"}),
            "",
        )
        result.append(
            NoticeRow(
                ref_id=ref_m.group(0) if ref_m else "",
                status=status,
                section=cells[0] if cells else "",
                cells=cells,
            )
        )
    return result


def _capture_pdf(page: Page, trigger: Locator, dest: Path, timeout_ms: int) -> bool:
    """Click *trigger* and save the resulting PDF to *dest*.

    Handles the three ways the portal might serve a PDF: a real download, a
    popup/new tab, or the same tab navigating to the file.
    """
    context = page.context
    popups: list[Page] = []
    context.on("page", popups.append)

    download: Download | None = None
    dl_timeout = min(timeout_ms, 20_000)
    try:
        with page.expect_download(timeout=dl_timeout) as dl_info:
            trigger.click()
        download = dl_info.value
    except PlaywrightTimeoutError:
        download = None

    try:
        if download is not None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            download.save_as(str(dest))
            return True

        # No download fired — look for a popup or same-tab PDF view.
        page.wait_for_timeout(1500)
        for popup in list(popups):
            url = popup.url or ""
            if ".pdf" in url.lower() or "pdf" in url.lower():
                ok = _save_url_pdf(page, url, dest)
                try:
                    popup.close()
                except PlaywrightTimeoutError:
                    pass
                if ok:
                    return True
        if ".pdf" in (page.url or "").lower():
            ok = _save_url_pdf(page, page.url, dest)
            page.go_back(wait_until="domcontentloaded")
            return ok
        return False
    finally:
        try:
            context.remove_listener("page", popups.append)
        except (ValueError, KeyError):
            pass


def _save_url_pdf(page: Page, url: str, dest: Path) -> bool:
    """Fetch a PDF URL using the browser's own session and write it to disk."""
    try:
        resp = page.context.request.get(url)
        if not resp.ok:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.body())
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort save; report and move on
        log.warning("failed to fetch PDF %s: %s", url, exc)
        return False


def _pause(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)
