from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite

from feedforger.log import logger


class Database:
    def __init__(self, db_path: str | Path = "cache/feeds.sqlite"):
        self.db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    async def __aenter__(self) -> Database:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path, detect_types=True)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS feeds (
                id TEXT PRIMARY KEY,
                created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
                updated_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
                content TEXT,
                continue_fail_count INTEGER NOT NULL DEFAULT 0,
                error_reason TEXT
            )
        """)
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_feeds_updated_at ON feeds(updated_at)"
        )
        await self._db.commit()
        return self

    async def __aexit__(self, *exc) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError(
                "Database not connected. Use 'async with Database() as db:'"
            )
        return self._db

    async def get_content(self, url: str, ttl: int = 1800) -> str | None:
        """Get cached feed content if not expired."""
        cutoff = int((datetime.now(UTC) - timedelta(seconds=ttl)).timestamp())

        async with self.db.execute(
            """
            SELECT content, continue_fail_count
            FROM feeds 
            WHERE id = ? AND updated_at > ?
            """,
            (url, cutoff),
        ) as cursor:
            if row := await cursor.fetchone():
                if row[0] is not None:
                    return row[0]
        return None

    async def batch_get_content(
        self, urls: list[str], ttl: int = 1800
    ) -> dict[str, str | None]:
        """Get cached feed content for multiple URLs if not expired."""
        if not urls:
            return {}

        result: dict[str, str | None] = {url: None for url in urls}
        cutoff = int((datetime.now(UTC) - timedelta(seconds=ttl)).timestamp())
        placeholders = ",".join(["?"] * len(urls))

        async with self.db.execute(
            f"""
            SELECT id, content
            FROM feeds 
            WHERE id IN ({placeholders}) AND updated_at > ?
            """,
            (*urls, cutoff),
        ) as cursor:
            async for row in cursor:
                url, content = row
                if content is not None:
                    result[url] = content
        return result

    async def set_content(
        self,
        url: str,
        content: str | None,
        success: bool = True,
        error_reason: str | None = None,
    ) -> None:
        """Update cache with new content."""
        now = int(datetime.now(UTC).timestamp())
        if success:
            await self.db.execute(
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
            await self.db.execute(
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
        await self.db.commit()

    async def get_all_feed_ids(self) -> set[str]:
        """Get all feed IDs from database."""
        async with self.db.execute("SELECT id FROM feeds") as cursor:
            return {row[0] for row in await cursor.fetchall()}

    async def get_failed_feed_ids(self, min_fail_count: int = 30) -> set[str]:
        async with self.db.execute(
            "SELECT id FROM feeds WHERE continue_fail_count >= ?",
            (min_fail_count,),
        ) as cursor:
            return {row[0] for row in await cursor.fetchall()}

    async def cleanup(self, days: int = 7) -> int:
        """Delete entries older than specified days."""
        cutoff = int((datetime.now(UTC) - timedelta(days=days)).timestamp())

        async with self.db.execute(
            "DELETE FROM feeds WHERE updated_at < ?",
            (cutoff,),
        ) as cursor:
            deleted = cursor.rowcount

        await self.db.commit()
        logger.info(f"Cleaned up {deleted} entries (old)")
        return deleted
