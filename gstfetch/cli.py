"""Command-line interface."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from gstfetch.client import SessionExpiredError, WafBlockedError
from gstfetch.config import get_settings
from gstfetch.endpoints import load_catalog
from gstfetch.export import export_all
from gstfetch.logging import configure_logging, get_logger
from gstfetch.orchestrator import run
from gstfetch.periods import Period
from gstfetch.session import BrowserClient
from gstfetch.session import (
    capture as capture_session,
)
from gstfetch.session import (
    login as do_login,
)
from gstfetch.state import StateStore
from gstfetch.storage import Storage

app = typer.Typer(add_completion=False, help="Incrementally fetch GST portal data for one client.")
console = Console()
log = get_logger(__name__)


@app.callback()
def _main() -> None:
    configure_logging()


@app.command()
def login() -> None:
    """Open a browser to log in manually (CAPTCHA + OTP) and save the session."""
    s = get_settings()
    do_login(s.session_file, headed=s.headed)


@app.command()
def capture() -> None:
    """Record live portal API traffic to a HAR file to discover/verify endpoints."""
    s = get_settings()
    har = s.data_dir / "capture.har"
    capture_session(s.session_file, har)
    catalog_file = har.with_name("discovered_endpoints.json")
    console.print(f"[green]Endpoints discovered.[/] See {catalog_file}")


@app.command()
def fetch(
    force: bool = typer.Option(False, "--force", help="Re-fetch even sealed/closed periods."),
    endpoints_file: Path = typer.Option(
        Path("endpoints.yaml"), "--endpoints", help="Optional endpoint override YAML."
    ),
) -> None:
    """Fetch all catalog resources for every applicable period, incrementally.

    Calls run from inside the logged-in browser session (see BrowserClient),
    which is what gets past the portal's firewall — a window will open and
    drive itself; leave it alone until the run finishes.
    """
    s = get_settings()
    catalog = load_catalog(endpoints_file if endpoints_file.exists() else None)

    store = StateStore(s.db_path)
    storage = Storage(s.client_dir)
    start = Period.parse(s.start_period)

    console.print(
        f"[bold]GSTIN[/] {s.gstin}  [bold]from[/] {s.start_period}  "
        f"[bold]data[/] {s.client_dir}"
    )
    try:
        with BrowserClient(
            s.session_file, headed=s.headed, request_delay=s.request_delay
        ) as client:
            stats = run(
                catalog=catalog,
                client=client,
                store=store,
                storage=storage,
                gstin=s.gstin,
                start=start,
                force=force,
            )
    except SessionExpiredError as exc:
        console.print(f"[red]Session expired:[/] {exc}\nRun [bold]gstfetch login[/] again.")
        raise typer.Exit(code=2) from exc
    except WafBlockedError as exc:
        console.print(
            f"[red]Firewall block:[/] {exc}\n"
            "Your session is likely stale — run [bold]gstfetch login[/] again, "
            "then re-run [bold]gstfetch fetch[/] (it resumes from the last checkpoint)."
        )
        raise typer.Exit(code=3) from exc
    finally:
        store.close()

    console.print(
        f"[green]Done.[/] fetched={stats.fetched} empty={stats.empty} "
        f"skipped={stats.skipped} errors={stats.errors}"
    )


@app.command()
def export(
    fmt: str = typer.Option("csv", "--format", help="Output format: csv | jsonl."),
    out: Path = typer.Option(None, "--out", help="Output dir (default: <data>/<gstin>/_export)."),
) -> None:
    """Flatten fetched JSON into CSV or JSONL, one file per resource."""
    s = get_settings()
    if not s.client_dir.exists():
        console.print(f"[red]No data at {s.client_dir}.[/] Run [bold]gstfetch fetch[/] first.")
        raise typer.Exit(code=1)
    out_dir = out or (s.client_dir / "_export")
    try:
        result = export_all(s.client_dir, out_dir, fmt)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    table = Table(title=f"Exported ({fmt}) -> {out_dir}")
    table.add_column("Resource")
    table.add_column("Rows", justify="right")
    for name, rows in sorted(result.items()):
        table.add_row(name, str(rows))
    if result:
        console.print(table)
    else:
        console.print("[yellow]Nothing to export — no resource folders found.[/]")


@app.command()
def status() -> None:
    """Show checkpoint summary for the configured client."""
    s = get_settings()
    store = StateStore(s.db_path)
    try:
        summary = store.summary(s.gstin)
        table = Table(title=f"Checkpoints — {s.gstin}")
        table.add_column("Status")
        table.add_column("Count", justify="right")
        for k, v in sorted(summary.items()):
            table.add_row(k, str(v))
        if not summary:
            console.print("No checkpoints yet. Run [bold]gstfetch fetch[/].")
        else:
            console.print(table)
    finally:
        store.close()


if __name__ == "__main__":
    app()
