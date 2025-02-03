from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Optional

import aiosqlite
from models import FeedItem


class Database:
    def __init__(self, db_path: str | Path = "cache/feeds.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(exist_ok=True)

    async def init(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS feeds (
                    id TEXT PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    payload TEXT NOT NULL
                )
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_feeds_updated_at ON feeds(updated_at)"
            )
            await db.commit()

    async def upsert_item(self, item: FeedItem) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO feeds (id, payload) VALUES (?, ?)
                ON CONFLICT(id) DO UPDATE SET 
                    updated_at = CURRENT_TIMESTAMP,
                    payload = excluded.payload
                """,
                (item.id, item.model_dump_json()),
            )
            await db.commit()

    async def get_item(self, item_id: str) -> Optional[FeedItem]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT payload FROM feeds WHERE id = ?", (item_id,)
            ) as cursor:
                if row := await cursor.fetchone():
                    return FeedItem.model_validate_json(row[0])
        return None
