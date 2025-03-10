from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Dict, List, Optional, Set
import itertools

import aiosqlite
from recipes import get_recipes
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
                    continue_fail_count INTEGER NOT NULL DEFAULT 0,
                    error_reason TEXT
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

    async def batch_get_content(
        self, urls: List[str], ttl: int = 1800
    ) -> Dict[str, Optional[str]]:
        """Get cached feed content for multiple URLs if not expired."""
        if not urls:
            return {}

        result = {url: None for url in urls}  # Initialize all URLs with None
        cutoff = int((datetime.now(UTC) - timedelta(seconds=ttl)).timestamp())

        # Create placeholders for the SQL query
        placeholders = ",".join(["?"] * len(urls))

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                f"""
                SELECT id, content
                FROM feeds 
                WHERE id IN ({placeholders}) AND updated_at > ?
                """,
                (*urls, cutoff),
            ) as cursor:
                async for row in cursor:
                    url, content = row
                    if content is not None:  # Only include valid content
                        result[url] = content

        return result

    async def set_content(
        self,
        url: str,
        content: Optional[str],
        success: bool = True,
        error_reason: Optional[str] = None,
    ) -> None:
        """
        Update cache with new content.

        Args:
            url: Feed URL
            content: Feed content, None if fetch failed
            success: Whether the fetch was successful
            error_reason: Error message if fetch failed
        """
        now = int(datetime.now(UTC).timestamp())
        async with aiosqlite.connect(self.db_path) as db:
            if success:
                # Reset fail count and error on success
                await db.execute(
                    """
                    INSERT INTO feeds (id, content, created_at, updated_at, continue_fail_count, error_reason) 
                    VALUES (?, ?, ?, ?, 0, NULL)
                    ON CONFLICT(id) DO UPDATE SET 
                        updated_at = excluded.updated_at,
                        content = excluded.content,
                        continue_fail_count = 0,
                        error_reason = NULL
                    """,
                    (url, content, now, now),
                )
            else:
                # Increment fail count and set error on failure
                await db.execute(
                    """
                    INSERT INTO feeds (id, content, created_at, updated_at, continue_fail_count, error_reason) 
                    VALUES (?, NULL, ?, ?, 1, ?)
                    ON CONFLICT(id) DO UPDATE SET 
                        updated_at = excluded.updated_at,
                        content = excluded.content,
                        continue_fail_count = continue_fail_count + 1,
                        error_reason = excluded.error_reason
                    """,
                    (url, now, now, error_reason),
                )
            await db.commit()

    async def get_all_feed_ids(self) -> Set[str]:
        """Get all feed IDs from database."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT id FROM feeds") as cursor:
                return {row[0] for row in await cursor.fetchall()}

    async def get_failed_feed_ids(self, min_fail_count: int = 30) -> Set[str]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id FROM feeds WHERE continue_fail_count >= ?",
                (min_fail_count,),
            ) as cursor:
                return {row[0] for row in await cursor.fetchall()}

    async def cleanup(self, days: int = 7) -> int:
        """
        Delete entries that are:
        1. Older than specified days AND successful (continue_fail_count = 0)
        2. No longer in recipes (orphaned entries)
        """
        cutoff = int((datetime.now(UTC) - timedelta(days=days)).timestamp())
        deleted = 0

        async with aiosqlite.connect(self.db_path) as db:
            # Delete old successful entries
            async with db.execute(
                "DELETE FROM feeds WHERE updated_at < ? AND continue_fail_count = 0",
                (cutoff,),
            ) as cursor:
                deleted += cursor.rowcount

            # Get current URLs from recipes and database
            recipe_urls = {
                url for feed in get_recipes().values() for url in feed["urls"]
            }
            existing_urls = await self.get_all_feed_ids()

            # Find URLs to remove
            urls_to_remove = existing_urls - recipe_urls
            if urls_to_remove:
                logger.info(f"Feeds to remove: {len(urls_to_remove)}")

                # Delete orphaned entries in batches
                batch_size = 500  # SQLite has a limit on query parameters
                for i in range(0, len(urls_to_remove), batch_size):
                    url_batch = list(
                        itertools.islice(urls_to_remove, i, i + batch_size)
                    )
                    placeholders = ",".join("?" * len(url_batch))
                    async with db.execute(
                        f"DELETE FROM feeds WHERE id IN ({placeholders})",
                        tuple(url_batch),
                    ) as cursor:
                        deleted += cursor.rowcount

            # Log new feeds that will be added
            new_urls = recipe_urls - existing_urls
            if new_urls:
                logger.info(f"New feeds to add: {len(new_urls)}")

            await db.commit()
            logger.info(f"Cleaned up {deleted} entries (old or orphaned)")
            return deleted
