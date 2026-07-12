from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

import aiosqlite
import httpx

from feedforger.log import logger
from feedforger.models import FailureReport, FailureReportEntry

MAX_CONSECUTIVE_FAILURES = 30

_USER_AGENT = "FeedForger/1.0 (+https://github.com/RoCry/feedforger)"

Clock = Callable[[], datetime]


class ContentStore(Protocol):
    async def get(self, url: str, *, ttl: timedelta) -> str | None: ...
    async def cleanup(self, *, retention: timedelta) -> int: ...
    async def failure_report(self) -> FailureReport: ...
    async def persistently_failing_urls(self) -> set[str]: ...


@dataclass(frozen=True, slots=True)
class _CacheRecord:
    url: str
    content: str | None
    created_at: int
    updated_at: int
    continue_fail_count: int
    error_reason: str | None


class _Records(Protocol):
    async def get(self, url: str) -> _CacheRecord | None: ...

    async def record_success(
        self,
        url: str,
        content: str,
        *,
        timestamp: int,
    ) -> None: ...

    async def record_failure(
        self,
        url: str,
        error_reason: str,
        *,
        timestamp: int,
    ) -> None: ...

    async def cleanup(self, *, cutoff: int) -> int: ...

    async def failure_entries(self) -> list[FailureReportEntry]: ...


class _Origin(Protocol):
    async def fetch(self, url: str) -> tuple[str | None, str | None]: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


class _FetchThroughCache:
    def __init__(
        self,
        records: _Records,
        origin: _Origin,
        *,
        now: Clock,
    ) -> None:
        self._records = records
        self._origin = origin
        self._now = now

    async def get(self, url: str, *, ttl: timedelta) -> str | None:
        if not url:
            raise ValueError("ContentStore URL must not be empty")
        if ttl <= timedelta(0):
            raise ValueError("ContentStore TTL must be positive")

        record = await self._records.get(url)
        if (
            record is not None
            and record.continue_fail_count >= MAX_CONSECUTIVE_FAILURES
        ):
            logger.debug(
                f"Skipping {url}: {record.continue_fail_count} consecutive failures"
            )
            return None

        cutoff = int((self._now() - ttl).timestamp())
        if (
            record is not None
            and record.content is not None
            and record.updated_at > cutoff
        ):
            return record.content

        content, error_reason = await self._origin.fetch(url)
        timestamp = int(self._now().timestamp())
        if content:
            await self._records.record_success(
                url,
                content,
                timestamp=timestamp,
            )
            return content

        await self._records.record_failure(
            url,
            error_reason or "empty response",
            timestamp=timestamp,
        )
        return None

    async def cleanup(self, *, retention: timedelta) -> int:
        if retention < timedelta(0):
            raise ValueError("ContentStore retention must not be negative")
        cutoff = int((self._now() - retention).timestamp())
        deleted = await self._records.cleanup(cutoff=cutoff)
        logger.info(f"Cleaned up {deleted} cached Content records")
        return deleted

    async def failure_report(self) -> FailureReport:
        entries = await self._records.failure_entries()
        generated_at = int(self._now().timestamp())
        return {
            "generated_at": generated_at,
            "generated_at_iso": datetime.fromtimestamp(generated_at, UTC).isoformat(),
            "total": len(entries),
            "failing": sum(1 for entry in entries if entry["continue_fail_count"] > 0),
            "entries": entries,
        }

    async def persistently_failing_urls(self) -> set[str]:
        return {
            entry["url"]
            for entry in await self._records.failure_entries()
            if entry["continue_fail_count"] >= MAX_CONSECUTIVE_FAILURES
        }


class _MemoryRecords:
    def __init__(self) -> None:
        self._records: dict[str, _CacheRecord] = {}

    async def get(self, url: str) -> _CacheRecord | None:
        return self._records.get(url)

    async def record_success(
        self,
        url: str,
        content: str,
        *,
        timestamp: int,
    ) -> None:
        existing = self._records.get(url)
        self._records[url] = _CacheRecord(
            url=url,
            content=content,
            created_at=existing.created_at if existing else timestamp,
            updated_at=timestamp,
            continue_fail_count=0,
            error_reason=None,
        )

    async def record_failure(
        self,
        url: str,
        error_reason: str,
        *,
        timestamp: int,
    ) -> None:
        existing = self._records.get(url)
        self._records[url] = _CacheRecord(
            url=url,
            content=None,
            created_at=existing.created_at if existing else timestamp,
            updated_at=timestamp,
            continue_fail_count=(existing.continue_fail_count if existing else 0) + 1,
            error_reason=error_reason,
        )

    async def cleanup(self, *, cutoff: int) -> int:
        expired = [
            url for url, record in self._records.items() if record.updated_at < cutoff
        ]
        for url in expired:
            del self._records[url]
        return len(expired)

    async def failure_entries(self) -> list[FailureReportEntry]:
        records = sorted(
            self._records.values(),
            key=lambda record: (
                record.continue_fail_count,
                record.updated_at,
            ),
            reverse=True,
        )
        return [
            {
                "url": record.url,
                "continue_fail_count": record.continue_fail_count,
                "error_reason": record.error_reason,
                "updated_at": record.updated_at,
                "created_at": record.created_at,
                "has_content": record.content is not None,
            }
            for record in records
        ]


class _ScriptedOrigin:
    def __init__(self, responses: Mapping[str, Sequence[str | None]]) -> None:
        self._responses = {
            url: deque(url_responses) for url, url_responses in responses.items()
        }

    async def fetch(self, url: str) -> tuple[str | None, str | None]:
        if not (responses := self._responses.get(url)):
            return None, "unavailable"
        if content := responses.popleft():
            return content, None
        return None, "unavailable"


class InMemoryContentStore:
    def __init__(
        self,
        responses: Mapping[str, Sequence[str | None]],
        *,
        now: Clock = _utc_now,
    ) -> None:
        self._engine = _FetchThroughCache(
            _MemoryRecords(),
            _ScriptedOrigin(responses),
            now=now,
        )

    async def get(self, url: str, *, ttl: timedelta) -> str | None:
        return await self._engine.get(url, ttl=ttl)

    async def cleanup(self, *, retention: timedelta) -> int:
        return await self._engine.cleanup(retention=retention)

    async def failure_report(self) -> FailureReport:
        return await self._engine.failure_report()

    async def persistently_failing_urls(self) -> set[str]:
        return await self._engine.persistently_failing_urls()


class _SQLiteRecords:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS feeds (
                id TEXT PRIMARY KEY,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                content TEXT,
                continue_fail_count INTEGER NOT NULL DEFAULT 0,
                error_reason TEXT
            )
        """)
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_feeds_updated_at ON feeds(updated_at)"
        )
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SQLite ContentStore is not open")
        return self._db

    async def get(self, url: str) -> _CacheRecord | None:
        async with self.db.execute(
            """
            SELECT id, content, created_at, updated_at,
                   continue_fail_count, error_reason
            FROM feeds
            WHERE id = ?
            """,
            (url,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return _CacheRecord(
            url=row[0],
            content=row[1],
            created_at=row[2],
            updated_at=row[3],
            continue_fail_count=row[4],
            error_reason=row[5],
        )

    async def record_success(
        self,
        url: str,
        content: str,
        *,
        timestamp: int,
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO feeds (
                id, content, created_at, updated_at,
                continue_fail_count, error_reason
            )
            VALUES (?, ?, ?, ?, 0, NULL)
            ON CONFLICT(id) DO UPDATE SET
                content = excluded.content,
                updated_at = excluded.updated_at,
                continue_fail_count = 0,
                error_reason = NULL
            """,
            (url, content, timestamp, timestamp),
        )
        await self.db.commit()

    async def record_failure(
        self,
        url: str,
        error_reason: str,
        *,
        timestamp: int,
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO feeds (
                id, content, created_at, updated_at,
                continue_fail_count, error_reason
            )
            VALUES (?, NULL, ?, ?, 1, ?)
            ON CONFLICT(id) DO UPDATE SET
                content = NULL,
                updated_at = excluded.updated_at,
                continue_fail_count = continue_fail_count + 1,
                error_reason = excluded.error_reason
            """,
            (url, timestamp, timestamp, error_reason),
        )
        await self.db.commit()

    async def cleanup(self, *, cutoff: int) -> int:
        async with self.db.execute(
            "DELETE FROM feeds WHERE updated_at < ?",
            (cutoff,),
        ) as cursor:
            deleted = cursor.rowcount
        await self.db.commit()
        return deleted

    async def failure_entries(self) -> list[FailureReportEntry]:
        async with self.db.execute(
            """
            SELECT id, continue_fail_count, error_reason,
                   updated_at, created_at,
                   CASE WHEN content IS NOT NULL THEN 1 ELSE 0 END AS has_content
            FROM feeds
            ORDER BY continue_fail_count DESC, updated_at DESC
            """
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            {
                "url": row[0],
                "continue_fail_count": row[1],
                "error_reason": row[2],
                "updated_at": row[3],
                "created_at": row[4],
                "has_content": bool(row[5]),
            }
            for row in rows
        ]


class _HttpOrigin:
    def __init__(
        self,
        *,
        max_concurrent: int,
        timeout: float,
        retries: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            max_redirects=3,
            headers={"User-Agent": _USER_AGENT},
            transport=transport,
        )
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._retries = retries

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch(self, url: str) -> tuple[str | None, str | None]:
        async with self._semaphore:
            last_error = "unavailable"
            for attempt in range(self._retries + 1):
                try:
                    response = await self._client.get(url)
                    response.raise_for_status()
                    return (
                        (response.text, None)
                        if response.text
                        else (None, "empty response")
                    )
                except httpx.TimeoutException as error:
                    last_error = f"Timeout: {error}"
                except httpx.HTTPStatusError as error:
                    status = error.response.status_code
                    if status < 500:
                        return None, f"HTTP {status}"
                    last_error = f"HTTP {status}"
                except httpx.RequestError as error:
                    last_error = f"{type(error).__name__}: {error}"

                if attempt < self._retries:
                    delay = float(attempt + 1)
                    logger.debug(f"Retrying {url} in {delay}s (attempt {attempt + 1})")
                    await asyncio.sleep(delay)

            logger.error(f"Failed to fetch '{url}': {last_error}")
            return None, last_error


class SQLiteHttpContentStore:
    def __init__(
        self,
        db_path: str | Path,
        *,
        max_concurrent: int,
        timeout: float,
        retries: int,
        now: Clock = _utc_now,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._records = _SQLiteRecords(db_path)
        self._origin = _HttpOrigin(
            max_concurrent=max_concurrent,
            timeout=timeout,
            retries=retries,
            transport=transport,
        )
        self._engine = _FetchThroughCache(
            self._records,
            self._origin,
            now=now,
        )

    async def __aenter__(self) -> SQLiteHttpContentStore:
        await self._records.open()
        return self

    async def __aexit__(self, *exc: object) -> None:
        try:
            await self._origin.close()
        finally:
            await self._records.close()

    async def get(self, url: str, *, ttl: timedelta) -> str | None:
        return await self._engine.get(url, ttl=ttl)

    async def cleanup(self, *, retention: timedelta) -> int:
        return await self._engine.cleanup(retention=retention)

    async def failure_report(self) -> FailureReport:
        return await self._engine.failure_report()

    async def persistently_failing_urls(self) -> set[str]:
        return await self._engine.persistently_failing_urls()
