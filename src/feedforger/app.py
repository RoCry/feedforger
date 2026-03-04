from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import feedparser

from feedforger.content import ExtractedContent, extract_main_content, parse_date
from feedforger.db import Database
from feedforger.filters import should_include_item
from feedforger.log import logger, setup_logging
from feedforger.models import Feed, FeedConfig, FeedItem
from feedforger.network import FeedFetcher
from feedforger.recipes import load_recipes


async def _process_feed_entries(
    content: str, feed_config: FeedConfig, ignore_before: datetime
) -> list[FeedItem]:
    items = []
    feed = feedparser.parse(content)
    feed_language = feed.feed.get("language", "").split("-")[0].lower()

    for entry in feed.entries:
        dt = entry.get("published", "") or entry.get("updated", "")
        if not dt:
            logger.warning(f"No date found for {entry.link}")
            continue

        published = parse_date(dt)
        if published is None:
            logger.warning(f"Failed to parse date: '{dt}' for {entry.link}")
            continue

        if published < ignore_before:
            continue

        if not should_include_item(entry, feed_config.filters):
            continue

        item = FeedItem.from_feed_entry(entry, feed.feed, published, feed_language)
        items.append(item)

    return items


def _has_substantial_content(item: FeedItem) -> bool:
    if item.content_html and len(item.content_html) > 700:
        return True
    if item.content_text and len(item.content_text) > 400:
        return True
    return False


def _apply_extracted_content(item: FeedItem, extracted: ExtractedContent) -> None:
    """Apply extracted content to a feed item."""
    if extracted.content_html or extracted.content_text:
        item.content_html = extracted.content_html or item.content_html
        item.content_text = extracted.content_text or item.content_text
        if (
            extracted.title
            and len(item.title) < 20
            and len(extracted.title) > len(item.title)
        ):
            item.title = extracted.title


async def _fulfill_items_content(
    fetcher: FeedFetcher, db: Database, feed_name: str, items: list[FeedItem]
) -> list[FeedItem]:
    if not items:
        return items

    items_needing_content = [
        item for item in items if not _has_substantial_content(item)
    ]
    if not items_needing_content:
        logger.info(f"{feed_name}: All {len(items)} items have substantial content")
        return items

    logger.info(
        f"{feed_name}: {len(items_needing_content)}/{len(items)} items need content"
    )

    urls_to_fetch: list[str] = []
    for item in items_needing_content:
        url = str(item.url)
        if cached_content := await db.get_content(url):
            try:
                extracted = extract_main_content(cached_content, url)
                _apply_extracted_content(item, extracted)
                logger.debug(f"{feed_name}: Used cached content for {item.url}")
            except Exception as e:
                logger.error(f"{feed_name}: Error processing cached item {url}: {e}")
                urls_to_fetch.append(url)
        else:
            urls_to_fetch.append(url)

    if urls_to_fetch:
        logger.info(f"{feed_name}: Fetching content for {len(urls_to_fetch)} items")
        results = await fetcher.fetch_urls(feed_name, urls_to_fetch)

        for url, content, error in results:
            item = next((i for i in items_needing_content if str(i.url) == url), None)
            if not item:
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
                extracted = extract_main_content(content, url)
                _apply_extracted_content(item, extracted)
            except Exception as e:
                logger.error(f"{feed_name}: Error extracting content from {url}: {e}")

    return items


async def _process_cached_feeds(
    db: Database, feed_name: str, feed_config: FeedConfig, ignore_before: datetime
) -> tuple[list[FeedItem], list[str]]:
    items: list[FeedItem] = []
    cached_map = await db.batch_get_content(feed_config.urls)
    urls_to_fetch = []

    for url in feed_config.urls:
        if cached := cached_map.get(url):
            try:
                feed_items = await _process_feed_entries(
                    cached, feed_config, ignore_before
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
) -> list[FeedItem]:
    items: list[FeedItem] = []
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
                content, feed_config, ignore_before
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

    return all_items


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

        for feed_name, feed_config in recipes.items():
            items = await process_feeds(fetcher, db, feed_name, feed_config, since)

            feed = Feed.create_from_items(feed_name, items)
            output_path = output_dir / f"{feed_name}.json"
            output_path.write_text(feed.model_dump_json(indent=2, exclude_none=True))

            logger.info(f"{feed_name}: generated {output_path}, {len(items)} items")
