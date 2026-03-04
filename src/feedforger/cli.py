from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import typer

app = typer.Typer(help="FeedForger — RSS feed aggregator")


@app.command()
def build(
    recipes: Path = typer.Option(
        Path("recipes"),
        help="Path to recipes file (.toml/.opml) or directory",
    ),
    output: Path = typer.Option(
        Path("outputs"),
        help="Output directory for generated feeds",
    ),
    since_days: int = typer.Option(7, help="Include items from the last N days"),
    db_path: Path = typer.Option(
        Path("cache/feeds.sqlite"),
        help="Path to SQLite cache database",
    ),
) -> None:
    """Build all feeds from recipes (supports .toml, .opml, and directories)."""
    from feedforger.app import run_build

    asyncio.run(
        run_build(
            recipes_path=recipes,
            output_dir=output,
            since=timedelta(days=since_days),
            db_path=db_path,
        )
    )


@app.command()
def cleanup(
    days: int = typer.Option(7, help="Delete entries older than N days"),
    db_path: Path = typer.Option(
        Path("cache/feeds.sqlite"),
        help="Path to SQLite cache database",
    ),
) -> None:
    """Clean up old database entries."""
    from feedforger.db import Database
    from feedforger.log import setup_logging

    async def _cleanup() -> None:
        setup_logging()
        async with Database(db_path) as db:
            deleted = await db.cleanup(days=days)
            typer.echo(f"Cleaned up {deleted} entries")

    asyncio.run(_cleanup())
