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

        _open_gst_dashboard(page, base_url, request_delay, timeout_ms)
        gstins = _company_gstins(page, timeout_ms)
        if not gstins:
            dump = _dump_debug(page, data_dir)
            raise RuntimeError(
                "No client GSTINs found on the GST dashboard.\n"
                f"Saved what the browser sees to {dump} — check that a client "
                "list is actually visible there. If the session looks logged "
                "out, run `microvista login` again; otherwise the page markup "
                "may differ from the built-in selectors."
            )

        if gstin_filter:
            gstins = [g for g in gstins if g in gstin_filter]
        log.info("Found %d client(s) to process: %s", len(gstins), ", ".join(gstins))

        for gstin in gstins:
            summary["clients"] += 1
            rows = _process_company(
                page,
                base_url=base_url,
                gstin=gstin,
                out_dir=data_dir / gstin,
                request_delay=request_delay,
                timeout_ms=timeout_ms,
                dry_run=dry_run,
            )
            summary["notices"] += len(rows)
            summary["pdfs"] += sum(1 for r in rows if r.pdf_file)

        browser.close()

    return summary


def _open_gst_dashboard(
    page: Page, base_url: str, request_delay: float, timeout_ms: int
) -> None:
    """Land on the GST dashboard (the client list), by URL or by clicking through.

    Direct navigation usually works, but the single-page app may need the same
    click path a user takes — product picker -> *Notice Alert* -> *GST* — so we
    fall back to that if no client GSTIN shows up.
    """
    page.goto(f"{base_url}/gst-dashboard", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")
    _pause(request_delay)
    if _has_gstin(page):
        return

    log.info("Client list not visible yet — clicking through the product menu.")
    for label in ("Notice Alert", "GST"):
        target = _first_visible(page, (f"text={label}",))
        if target is not None:
            try:
                target.click()
                page.wait_for_load_state("networkidle")
                _pause(request_delay)
            except PlaywrightTimeoutError:
                pass
    if not _has_gstin(page):
        # Last resort: re-hit the dashboard URL now that product context is set.
        page.goto(f"{base_url}/gst-dashboard", wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")
        _pause(request_delay)


def _has_gstin(page: Page) -> bool:
    try:
        return bool(GSTIN_RE.search(page.inner_text("body")))
    except PlaywrightTimeoutError:
        return False


def _company_gstins(page: Page, timeout_ms: int) -> list[str]:
    """Every distinct client GSTIN visible on the dashboard, tag-agnostic.

    Scans the rendered page text rather than assuming the GSTIN sits in an
    ``<a>`` tag, and waits for the client table to populate (it loads via XHR
    after the page itself is 'idle').
    """
    try:
        page.wait_for_function(
            "() => /[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]/.test"
            "(document.body.innerText)",
            timeout=min(timeout_ms, 20_000),
        )
    except PlaywrightTimeoutError:
        return []
    body = page.inner_text("body")
    # Preserve first-seen order while de-duplicating.
    return list(dict.fromkeys(GSTIN_RE.findall(body)))


def _process_company(
    page: Page,
    *,
    base_url: str,
    gstin: str,
    out_dir: Path,
    request_delay: float,
    timeout_ms: int,
    dry_run: bool,
) -> list[NoticeRow]:
    log.info("[%s] opening company dashboard", gstin)
    # Re-open the dashboard, then click this client's GSTIN to open its
    # company view — exactly the clicks a user makes. Clicking (vs. following an
    # href) works whether the GSTIN is a link, a span, or a table cell.
    _open_gst_dashboard(page, base_url, request_delay, timeout_ms)
    link = page.get_by_text(gstin, exact=False).first
    try:
        link.click()
        page.wait_for_load_state("networkidle")
    except PlaywrightTimeoutError:
        log.warning("[%s] could not open company view", gstin)
        return []
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

    # Playwright can't wrap a built-in method (e.g. list.append) as a handler,
    # so use a plain function and keep the reference for remove_listener.
    def _on_page(new_page: Page) -> None:
        popups.append(new_page)

    context.on("page", _on_page)

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
            context.remove_listener("page", _on_page)
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


def _dump_debug(page: Page, data_dir: Path) -> Path:
    """Save a screenshot + HTML of the current page for troubleshooting."""
    debug_dir = data_dir / "_debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    shot = debug_dir / "gst-dashboard.png"
    html = debug_dir / "gst-dashboard.html"
    try:
        page.screenshot(path=str(shot), full_page=True)
        html.write_text(page.content(), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 — diagnostics are best-effort
        log.warning("could not write debug artifacts: %s", exc)
    return debug_dir


def inspect(
    *,
    base_url: str,
    session_file: Path,
    data_dir: Path,
    headed: bool = True,
    request_delay: float = 1.0,
    timeout_ms: int = 60_000,
) -> tuple[list[str], Path]:
    """Open the GST dashboard and report what the tool can see.

    Returns ``(gstins, debug_dir)`` and always writes a screenshot + HTML so
    selectors can be diagnosed without guessing.
    """
    if not session_file.exists():
        raise FileNotFoundError(
            f"no session at {session_file}; run `microvista login` first"
        )
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        context = browser.new_context(
            storage_state=str(session_file), accept_downloads=True
        )
        page = context.new_page()
        page.set_default_timeout(timeout_ms)
        _open_gst_dashboard(page, base_url, request_delay, timeout_ms)
        gstins = _company_gstins(page, timeout_ms)
        debug_dir = _dump_debug(page, data_dir)
        browser.close()
    return gstins, debug_dir


def _pause(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)
