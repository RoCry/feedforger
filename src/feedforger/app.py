from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import feedparser

from feedforger.content import build_item_content, needs_fulfillment, parse_date
from feedforger.db import Database
from feedforger.filters import should_include_item
from feedforger.log import logger, setup_logging
from feedforger.models import Feed, FeedConfig, FeedItem
from feedforger.network import FeedFetcher
from feedforger.recipes import load_recipes

# Article HTML rarely changes — cache for 30 days. Source feeds keep the short default TTL.
ARTICLE_CACHE_TTL = 60 * 60 * 24 * 30
# Skip feeds that have failed this many times in a row (saves Actions minutes & log noise).
MAX_CONSECUTIVE_FAILURES = 30


@dataclass(frozen=True, slots=True)
class _ItemSource:
    entry: Mapping[str, Any]
    feed_meta: Mapping[str, Any]
    published: datetime
    feed_language: str | None
    source_url: str | None


@dataclass(slots=True)
class _BuiltItem:
    source: _ItemSource
    item: FeedItem


def _build_item(
    source: _ItemSource,
    page_html: str | None = None,
) -> FeedItem | None:
    return build_item_content(
        entry=source.entry,
        feed_meta=source.feed_meta,
        published=source.published,
        feed_language=source.feed_language,
        source_url=source.source_url,
        page_html=page_html,
    )


async def _process_feed_entries(
    content: str,
    feed_config: FeedConfig,
    ignore_before: datetime,
    source_url: str | None = None,
) -> list[_BuiltItem]:
    items: list[_BuiltItem] = []
    feed = feedparser.parse(content)
    feed_language = feed.feed.get("language", "").split("-")[0].lower()

    for entry in feed.entries:
        dt = entry.get("published", "") or entry.get("updated", "")
        entry_url = entry.get("link") or entry.get("id") or "<unknown>"
        if not dt:
            logger.warning(f"No date found for {entry_url}")
            continue

        published = parse_date(dt)
        if published is None:
            logger.warning(f"Failed to parse date: '{dt}' for {entry_url}")
            continue

        if published < ignore_before:
            continue

        if not should_include_item(entry, feed_config.filters):
            continue

        source = _ItemSource(
            entry=entry,
            feed_meta=feed.feed,
            published=published,
            feed_language=feed_language,
            source_url=source_url,
        )
        item = _build_item(source)
        if item is None:
            logger.warning(f"Skipping entry without title/link in feed: {entry!r:.120}")
            continue
        items.append(_BuiltItem(source=source, item=item))

    return items


async def _fulfill_items_content(
    fetcher: FeedFetcher,
    db: Database,
    feed_name: str,
    items: list[_BuiltItem],
) -> list[_BuiltItem]:
    if not items:
        return items

    items_needing_content = [built for built in items if needs_fulfillment(built.item)]
    if not items_needing_content:
        logger.info(f"{feed_name}: All {len(items)} items have substantial content")
        return items

    logger.info(
        f"{feed_name}: {len(items_needing_content)}/{len(items)} items need content"
    )

    urls_to_fetch: list[str] = []
    for built in items_needing_content:
        url = str(built.item.url)
        if cached_content := await db.get_content(url, ttl=ARTICLE_CACHE_TTL):
            try:
                if rebuilt := _build_item(built.source, page_html=cached_content):
                    built.item = rebuilt
                logger.debug(f"{feed_name}: Used cached content for {built.item.url}")
            except Exception as e:
                logger.error(f"{feed_name}: Error processing cached item {url}: {e}")
                urls_to_fetch.append(url)
        else:
            urls_to_fetch.append(url)

    if urls_to_fetch:
        logger.info(f"{feed_name}: Fetching content for {len(urls_to_fetch)} items")
        results = await fetcher.fetch_urls(feed_name, urls_to_fetch)

        for url, content, error in results:
            built = next(
                (item for item in items_needing_content if str(item.item.url) == url),
                None,
            )
            if not built:
                continue

            await db.set_content(
                url, content, success=error is None, error_reason=error
            )

            if not content:
                logger.warning(
                    f"{feed_name}: Failed to fetch content for {url}: {error}"
                )
                continue

            try:
                if rebuilt := _build_item(built.source, page_html=content):
                    built.item = rebuilt
            except Exception as e:
                logger.error(f"{feed_name}: Error extracting content from {url}: {e}")

    return items


async def _process_cached_feeds(
    db: Database, feed_name: str, feed_config: FeedConfig, ignore_before: datetime
) -> tuple[list[_BuiltItem], list[str]]:
    items: list[_BuiltItem] = []
    cached_map = await db.batch_get_content(feed_config.urls)
    urls_to_fetch = []

    for url in feed_config.urls:
        if cached := cached_map.get(url):
            try:
                feed_items = await _process_feed_entries(
                    cached, feed_config, ignore_before, source_url=url
                )
                items.extend(feed_items)
            except Exception as e:
                logger.error(
                    f"Error processing cached content from {url}: {e}", exc_info=True
                )
        else:
            urls_to_fetch.append(url)

    if not urls_to_fetch:
        logger.info(f"{feed_name}: all {len(feed_config.urls)} feeds were cached")
    else:
        logger.info(f"{feed_name}: fetching {len(urls_to_fetch)} uncached feeds")

    return items, urls_to_fetch


async def _process_uncached_feeds(
    fetcher: FeedFetcher,
    db: Database,
    feed_name: str,
    urls_to_fetch: list[str],
    feed_config: FeedConfig,
    ignore_before: datetime,
) -> list[_BuiltItem]:
    items: list[_BuiltItem] = []
    if not urls_to_fetch:
        return items

    results = await fetcher.fetch_urls(feed_name, urls_to_fetch)

    for processed, (url, content, error) in enumerate(results, 1):
        await db.set_content(url, content, success=error is None, error_reason=error)

        if not content:
            logger.warning(
                f"{feed_name}: skipping {url} ({processed}/{len(urls_to_fetch)})"
            )
            continue

        try:
            feed_items = await _process_feed_entries(
                content, feed_config, ignore_before, source_url=url
            )
            items.extend(feed_items)
            logger.info(
                f"{feed_name}: processed {len(feed_items)} entries from {url} ({processed}/{len(urls_to_fetch)})"
            )
        except Exception as e:
            logger.error(f"{feed_name}: error processing {url}: {e}", exc_info=True)

    return items


async def process_feeds(
    fetcher: FeedFetcher,
    db: Database,
    feed_name: str,
    feed_config: FeedConfig,
    since: timedelta,
) -> list[FeedItem]:
    ignore_before = datetime.now(UTC) - since
    logger.info(f"{feed_name}: processing {len(feed_config.urls)} feeds")

    cached_items, urls_to_fetch = await _process_cached_feeds(
        db, feed_name, feed_config, ignore_before
    )
    uncached_items = await _process_uncached_feeds(
        fetcher, db, feed_name, urls_to_fetch, feed_config, ignore_before
    )

    all_items = cached_items + uncached_items

    if feed_config.fulfill and all_items:
        logger.info(f"{feed_name}: fulfilling content for {len(all_items)} items")
        all_items = await _fulfill_items_content(fetcher, db, feed_name, all_items)

    return [built.item for built in all_items]


async def run_build(
    recipes_path: Path,
    output_dir: Path,
    since: timedelta = timedelta(days=7),
    db_path: Path = Path("cache/feeds.sqlite"),
) -> None:
    setup_logging()
    logger.info("Starting feed forging")
    output_dir.mkdir(parents=True, exist_ok=True)

    recipes = load_recipes(recipes_path)
    logger.info(f"Loaded {len(recipes)} recipes from {recipes_path}")

    async with Database(db_path) as db, FeedFetcher() as fetcher:
        await db.cleanup()

        skip_urls = await db.get_failed_feed_ids(MAX_CONSECUTIVE_FAILURES)
        if skip_urls:
            logger.info(
                f"Skipping {len(skip_urls)} URLs with >={MAX_CONSECUTIVE_FAILURES} consecutive failures"
            )

        for feed_name, feed_config in recipes.items():
            active_urls = [u for u in feed_config.urls if u not in skip_urls]
            skipped = len(feed_config.urls) - len(active_urls)
            if skipped:
                logger.info(
                    f"{feed_name}: skipping {skipped} persistently-failing URLs"
                )
            if not active_urls:
                logger.warning(
                    f"{feed_name}: all URLs are skipped, no items will be generated"
                )
                continue
            effective_config = feed_config.model_copy(update={"urls": active_urls})
            items = await process_feeds(fetcher, db, feed_name, effective_config, since)

            feed = Feed.create_from_items(feed_name, items)
            output_path = output_dir / f"{feed_name}.json"
            output_path.write_text(
                feed.model_dump_json(indent=2, exclude_none=True, by_alias=True)
            )

            logger.info(f"{feed_name}: generated {output_path}, {len(items)} items")
