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
                    content TEXT,
                    continue_fail_count INTEGER NOT NULL DEFAULT 0
                )
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_feeds_updated_at ON feeds(updated_at)"
            )
            await db.commit()

    async def get_content(self, url: str, ttl: int = 1800) -> Optional[str]:
        """Get cached feed content if not expired."""
        cutoff = int((datetime.now(UTC) - timedelta(seconds=ttl)).timestamp())
        
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT content, continue_fail_count
                FROM feeds 
                WHERE id = ? AND updated_at > ?
                """,
                (url, cutoff),
            ) as cursor:
                if row := await cursor.fetchone():
                    if row[0] is not None:  # Has valid content
                        return row[0]
        return None

    async def set_content(self, url: str, content: Optional[str], success: bool = True) -> None:
        """
        Update cache with new content.
        
        Args:
            url: Feed URL
            content: Feed content, None if fetch failed
            success: Whether the fetch was successful
        """
        now = int(datetime.now(UTC).timestamp())
        async with aiosqlite.connect(self.db_path) as db:
            if success:
                # Reset fail count on success
                await db.execute(
                    """
                    INSERT INTO feeds (id, content, created_at, updated_at, continue_fail_count) 
                    VALUES (?, ?, ?, ?, 0)
                    ON CONFLICT(id) DO UPDATE SET 
                        updated_at = excluded.updated_at,
                        content = excluded.content,
                        continue_fail_count = 0
                    """,
                    (url, content, now, now),
                )
            else:
                # Increment fail count on failure
                await db.execute(
                    """
                    INSERT INTO feeds (id, content, created_at, updated_at, continue_fail_count) 
                    VALUES (?, NULL, ?, ?, 1)
                    ON CONFLICT(id) DO UPDATE SET 
                        updated_at = excluded.updated_at,
                        content = excluded.content,
                        continue_fail_count = continue_fail_count + 1
                    """,
                    (url, now, now),
                )
            await db.commit()

    async def cleanup(self, days: int = 30) -> int:
        """Delete entries older than specified days."""
        cutoff = int((datetime.now(UTC) - timedelta(days=days)).timestamp())
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                # only delete success feeds
                "DELETE FROM feeds WHERE updated_at < ? AND continue_fail_count = 0",
                (cutoff,),
            ) as cursor:
                deleted = cursor.rowcount
                await db.commit()
                logger.info(f"Cleaned up {deleted} old entries")
                return deleted
