from __future__ import annotations

import asyncio
import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Annotated

import typer

from feedforger.content_store import SQLiteHttpContentStore
from feedforger.log import setup_logging
from feedforger.settings import Settings

app = typer.Typer(help="FeedForger — RSS feed aggregator")

DbPathOption = Annotated[
    Path | None,
    typer.Option(help="Path to SQLite cache database"),
]


def _store(settings: Settings) -> SQLiteHttpContentStore:
    return SQLiteHttpContentStore(
        db_path=settings.db_path,
        max_concurrent=settings.max_concurrent,
        timeout=settings.request_timeout,
        retries=settings.request_retries,
    )


@app.command()
def build(
    recipes: Annotated[
        Path | None,
        typer.Option(help="Path to recipes file (.toml/.opml) or directory"),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(help="Output directory for generated feeds"),
    ] = None,
    since_days: Annotated[
        int | None,
        typer.Option(help="Include items from the last N days"),
    ] = None,
    db_path: DbPathOption = None,
    base_url: Annotated[
        str | None,
        typer.Option(help="Base URL for published feed links"),
    ] = None,
) -> None:
    """Build all feeds from recipes (supports .toml, .opml, and directories)."""
    from feedforger.app import run_build

    settings = Settings.from_sources(
        env=os.environ,
        recipes_path=recipes,
        output_dir=output,
        db_path=db_path,
        since=timedelta(days=since_days) if since_days is not None else None,
        base_url=base_url,
    )

    async def _run() -> None:
        async with _store(settings) as store:
            await run_build(store=store, settings=settings)

    asyncio.run(_run())


@app.command()
def report(
    output: Annotated[
        Path | None,
        typer.Option(help="Output JSON file path"),
    ] = None,
    db_path: DbPathOption = None,
) -> None:
    """Dump per-URL failure stats from the cache DB as JSON.

    Use the artifact to identify URLs that have been failing for a long time
    so you can prune them from recipes.toml.
    """
    settings = Settings.from_sources(
        env=os.environ,
        report_path=output,
        db_path=db_path,
    )

    async def _run() -> None:
        setup_logging()
        async with _store(settings) as store:
            payload = await store.failure_report()

        settings.report_path.parent.mkdir(parents=True, exist_ok=True)
        settings.report_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False)
        )
        typer.echo(
            f"Wrote {len(payload['entries'])} entries "
            f"({payload['failing']} failing) → {settings.report_path}"
        )

    asyncio.run(_run())


@app.command()
def cleanup(
    days: Annotated[
        int | None,
        typer.Option(help="Delete entries older than N days"),
    ] = None,
    db_path: DbPathOption = None,
) -> None:
    """Clean up old database entries."""
    settings = Settings.from_sources(
        env=os.environ,
        db_path=db_path,
        cleanup_retention=timedelta(days=days) if days is not None else None,
    )

    async def _cleanup() -> None:
        setup_logging()
        async with _store(settings) as store:
            deleted = await store.cleanup(retention=settings.cleanup_retention)
            typer.echo(f"Cleaned up {deleted} entries")

    asyncio.run(_cleanup())
