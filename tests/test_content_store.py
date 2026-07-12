import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from feedforger.app import process_feeds
from feedforger.content_store import (
    FEED_TTL,
    FailureReport,
    InMemoryContentStore,
    SQLiteHttpContentStore,
)
from feedforger.models import FeedConfig

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@dataclass(slots=True)
class FakeClock:
    current: datetime

    def __call__(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


def test_memory_store_refreshes_content_at_exact_ttl_boundary() -> None:
    clock = FakeClock(datetime(2026, 7, 12, tzinfo=UTC))
    store = InMemoryContentStore(
        responses={"https://example.com/feed": ["first", "second"]},
        now=clock,
    )

    async def scenario() -> None:
        assert (
            await store.get("https://example.com/feed", ttl=timedelta(minutes=30))
            == "first"
        )
        clock.advance(timedelta(minutes=29, seconds=59))
        assert (
            await store.get("https://example.com/feed", ttl=timedelta(minutes=30))
            == "first"
        )
        clock.advance(timedelta(seconds=1))
        assert (
            await store.get("https://example.com/feed", ttl=timedelta(minutes=30))
            == "second"
        )

    asyncio.run(scenario())


def test_memory_store_skips_persistent_failures_until_cleanup() -> None:
    clock = FakeClock(datetime(2026, 7, 12, tzinfo=UTC))
    url = "https://example.com/broken"
    store = InMemoryContentStore(
        responses={url: [None] * 30 + ["recovered"]},
        now=clock,
    )

    async def scenario() -> None:
        for _ in range(30):
            assert await store.get(url, ttl=FEED_TTL) is None

        report = await store.failure_report()
        assert report["entries"][0]["continue_fail_count"] == 30
        assert report["entries"][0]["error_reason"] == "unavailable"

        assert await store.get(url, ttl=FEED_TTL) is None
        assert (await store.failure_report())["entries"][0]["continue_fail_count"] == 30

        clock.advance(timedelta(days=8))
        assert await store.cleanup(retention=timedelta(days=7)) == 1
        assert await store.get(url, ttl=FEED_TTL) == "recovered"

    asyncio.run(scenario())


def test_memory_store_success_resets_failure_state() -> None:
    clock = FakeClock(datetime(2026, 7, 12, tzinfo=UTC))
    url = "https://example.com/flaky"
    store = InMemoryContentStore(
        responses={url: [None, "recovered"]},
        now=clock,
    )

    async def scenario() -> None:
        assert await store.get(url, ttl=FEED_TTL) is None
        assert await store.get(url, ttl=FEED_TTL) == "recovered"
        assert await store.failure_report() == {
            "generated_at": 1783814400,
            "generated_at_iso": "2026-07-12T00:00:00+00:00",
            "total": 1,
            "failing": 0,
            "entries": [
                {
                    "url": url,
                    "continue_fail_count": 0,
                    "error_reason": None,
                    "updated_at": 1783814400,
                    "created_at": 1783814400,
                    "has_content": True,
                }
            ],
        }

    asyncio.run(scenario())


def test_sqlite_store_returns_finished_failure_report(tmp_path: Path) -> None:
    now = datetime(2026, 7, 12, 12, 30, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "bad.example":
            return httpx.Response(404, request=request)
        return httpx.Response(200, text="feed content", request=request)

    async def get_report() -> FailureReport:
        async with SQLiteHttpContentStore(
            db_path=tmp_path / "feeds.sqlite",
            now=lambda: now,
            transport=httpx.MockTransport(handler),
        ) as store:
            await store.get("https://ok.example/feed", ttl=FEED_TTL)
            await store.get("https://bad.example/feed", ttl=FEED_TTL)
            return await store.failure_report()

    report = asyncio.run(get_report())

    assert report == {
        "generated_at": 1783859400,
        "generated_at_iso": "2026-07-12T12:30:00+00:00",
        "total": 2,
        "failing": 1,
        "entries": [
            {
                "url": "https://bad.example/feed",
                "continue_fail_count": 1,
                "error_reason": "HTTP 404",
                "updated_at": 1783859400,
                "created_at": 1783859400,
                "has_content": False,
            },
            {
                "url": "https://ok.example/feed",
                "continue_fail_count": 0,
                "error_reason": None,
                "updated_at": 1783859400,
                "created_at": 1783859400,
                "has_content": True,
            },
        ],
    }


def test_process_feeds_uses_memory_store_for_feed_and_fulfillment() -> None:
    feed_url = "https://fixture.example/feed.xml"
    store = InMemoryContentStore(
        responses={
            feed_url: [(FIXTURES_DIR / "characterization_feed.xml").read_text()],
            "https://source.example/html-item": [
                (
                    "<html><head><title>HTML item from the original page</title></head>"
                    "<body><article><p>Full HTML article.</p></article></body></html>"
                )
            ],
            "https://source.example/text-item": [
                (
                    "<html><head><title>Text item from the original page</title></head>"
                    "<body><main><p>Full text article.</p></main></body></html>"
                )
            ],
        }
    )

    items = asyncio.run(
        process_feeds(
            store=store,
            feed_name="Fixture",
            feed_config=FeedConfig(urls=[feed_url], fulfill=True),
            since=timedelta(days=36500),
        )
    )

    assert [item.title for item in items] == [
        "HTML item from the original page",
        "Text item from the original page",
    ]
    assert [item.content_text for item in items] == [
        "Full HTML article.",
        "Full text article.",
    ]
