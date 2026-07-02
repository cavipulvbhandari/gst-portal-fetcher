"""Command-line interface for the Microvista Notice Alert fetcher."""

from __future__ import annotations

import typer
from rich.console import Console

from microvista import portal
from microvista.config import get_settings
from microvista.logging import configure_logging, get_logger

app = typer.Typer(
    add_completion=False,
    help="Log in to the Microvista Notice Alert portal and download GST notice PDFs.",
)
console = Console()
log = get_logger(__name__)


@app.callback()
def _main() -> None:
    configure_logging()


@app.command()
def login() -> None:
    """Sign in (email + password, or by hand) and save the browser session."""
    s = get_settings()
    portal.login(
        base_url=s.base_url,
        session_file=s.session_file,
        email=s.email,
        password=s.password,
        headed=s.headed,
        timeout_ms=s.timeout_ms,
    )
    console.print(f"[green]Session saved[/] -> {s.session_file}")


@app.command()
def fetch(
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="List clients and notices and what would download, without saving PDFs.",
    ),
) -> None:
    """Download notice PDFs for every client GSTIN on the portal."""
    s = get_settings()
    try:
        stats = portal.fetch(
            base_url=s.base_url,
            session_file=s.session_file,
            data_dir=s.data_dir,
            gstin_filter=s.gstin_filter,
            headed=s.headed,
            request_delay=s.request_delay,
            timeout_ms=s.timeout_ms,
            dry_run=dry_run,
        )
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=2) from exc

    console.print(
        f"[green]Done.[/] clients={stats['clients']} "
        f"notices={stats['notices']} pdfs={stats['pdfs']}"
        + ("  [yellow](dry run — nothing saved)[/]" if dry_run else "")
    )
    if not dry_run:
        console.print(f"Output under [bold]{s.data_dir}[/]")


@app.command()
def inspect() -> None:
    """Diagnose what the tool sees on the GST dashboard (screenshot + HTML + GSTINs)."""
    s = get_settings()
    try:
        gstins, debug_dir = portal.inspect(
            base_url=s.base_url,
            session_file=s.session_file,
            data_dir=s.data_dir,
            headed=s.headed,
            request_delay=s.request_delay,
            timeout_ms=s.timeout_ms,
        )
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    if gstins:
        console.print(f"[green]Detected {len(gstins)} GSTIN(s):[/] {', '.join(gstins)}")
    else:
        console.print("[yellow]No GSTINs detected on the dashboard.[/]")
    console.print(
        f"Saved a screenshot and HTML to [bold]{debug_dir}[/] — "
        "open the PNG to see what the browser rendered."
    )


if __name__ == "__main__":
    app()
