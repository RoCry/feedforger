from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Optional

import aiosqlite
from utils import logger


class Database:
    def __init__(self, db_path: str | Path = "cache/feeds.sqlite"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(exist_ok=True)

    async def init(self):
        async with aiosqlite.connect(self.db_path, detect_types=True) as db:
            # Enable foreign keys and WAL mode for better performance
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS feeds (
                    id TEXT PRIMARY KEY,
                    created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
                    updated_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
                    content TEXT NOT NULL
                )
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_feeds_updated_at ON feeds(updated_at)"
            )
            await db.commit()

    async def get_content(self, url: str, ttl: int = 1800) -> Optional[str]:
        """Get cached feed content if not expired."""
        cutoff = int((datetime.now(UTC) - timedelta(seconds=ttl)).timestamp())
        # logger.debug(f"Checking cache for {url} with cutoff {cutoff}")
        
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT content 
                FROM feeds 
                WHERE id = ? AND updated_at > ?
                """,
                (url, cutoff),
            ) as cursor:
                if row := await cursor.fetchone():
                    # logger.debug(f"Using cached content for {url}")
                    return row[0]
        return None

    async def set_content(self, url: str, content: str) -> None:
        """Update cache with new content."""
        now = int(datetime.now(UTC).timestamp())
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO feeds (id, content, created_at, updated_at) 
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET 
                    updated_at = excluded.updated_at,
                    content = excluded.content
                """,
                (url, content, now, now),
            )
            await db.commit()
            # logger.debug(f"Updated cache for {url}")

    async def cleanup(self, days: int = 30) -> int:
        """Delete entries older than specified days."""
        cutoff = int((datetime.now(UTC) - timedelta(days=days)).timestamp())
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "DELETE FROM feeds WHERE updated_at < ?",
                (cutoff,),
            ) as cursor:
                deleted = cursor.rowcount
                await db.commit()
                logger.info(f"Cleaned up {deleted} old entries")
                return deleted
