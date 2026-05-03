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
def report(
    output: Path = typer.Option(
        Path("cache/failure_report.json"),
        help="Output JSON file path",
    ),
    db_path: Path = typer.Option(
        Path("cache/feeds.sqlite"),
        help="Path to SQLite cache database",
    ),
) -> None:
    """Dump per-URL failure stats from the cache DB as JSON.

    Use the artifact to identify URLs that have been failing for a long time
    so you can prune them from recipes.toml.
    """
    import json
    from datetime import UTC, datetime

    from feedforger.db import Database
    from feedforger.log import setup_logging

    async def _run() -> None:
        setup_logging()
        async with Database(db_path) as db:
            entries = await db.get_failure_report()

        now_ts = int(datetime.now(UTC).timestamp())
        payload = {
            "generated_at": now_ts,
            "generated_at_iso": datetime.fromtimestamp(now_ts, UTC).isoformat(),
            "total": len(entries),
            "failing": sum(1 for e in entries if e["continue_fail_count"] > 0),
            "entries": entries,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        typer.echo(
            f"Wrote {len(entries)} entries "
            f"({payload['failing']} failing) → {output}"
        )

    asyncio.run(_run())


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
