import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import List, Tuple

import feedparser

from db import Database
from filters import should_include_item
from models import Feed, FeedConfig, FeedItem
from network import FeedFetcher
from recipes import get_recipes
from utils import extract_main_content, logger, parse_date


async def _process_feed_entries(
    content: str, feed_config: FeedConfig, ignore_before_time: datetime
) -> list[FeedItem]:
    """Process a single feed's content and return items."""
    items = []
    feed = feedparser.parse(content)

    # Get feed language
    feed_language = feed.feed.get("language", "").split("-")[0].lower()

    for entry in feed.entries:
        # Parse and validate date
        dt = entry.get("published", "") or entry.get("updated", "")
        if not dt:
            logger.warning(f"No date found for {entry.link}")
            continue

        published = parse_date(dt)
        if published is None:
            logger.warning(f"Failed to parse date: '{dt}' for {entry.link}")
            continue

        if published < ignore_before_time:
            continue

        # Apply filters
        if not should_include_item(entry, feed_config.filters):
            continue

        # Create feed item from entry using the model's class method
        item = FeedItem.from_feed_entry(entry, feed.feed, published, feed_language)
        items.append(item)

    return items


def _has_substantial_content(item: FeedItem, min_length: int = 200) -> bool:
    """Check if an item has substantial content."""
    if item.content_html and len(item.content_html) > min_length:
        return True
    if item.content_text and len(item.content_text) > min_length:
        return True
    return False


async def _fulfill_items_content_if_needed(
    fetcher: FeedFetcher, db: Database, feed_name: str, items: List[FeedItem]
) -> List[FeedItem]:
    """Fetch and add content to feed items that lack substantial content."""
    if not items:
        return items

    # Step 1: Filter items that need content fulfillment
    items_needing_content = [
        item for item in items if not _has_substantial_content(item)
    ]
    if not items_needing_content:
        logger.info(f"{feed_name}: All {len(items)} items have substantial content")
        return items

    logger.info(
        f"{feed_name}: {len(items_needing_content)}/{len(items)} items need content"
    )

    # Step 2: Check cache for items
    urls_to_fetch = []
    for item in items_needing_content:
        url = str(item.url)
        # Check if content is in cache
        if cached_content := await db.get_content(url):
            try:
                # Use the utility function to extract content
                content_data = extract_main_content(cached_content, url)

                if content_data["content_html"] or content_data["content_text"]:
                    item.content_html = (
                        content_data["content_html"] or item.content_html
                    )
                    item.content_text = (
                        content_data["content_text"] or item.content_text
                    )
                    # Update title if the current one is very short and we found a better one
                    if (
                        content_data["title"]
                        and len(item.title) < 20
                        and len(content_data["title"]) > len(item.title)
                    ):
                        item.title = content_data["title"]
                    logger.debug(f"{feed_name}: Used cached content for {item.url}")
                else:
                    # Cache hit but failed to extract content, need to fetch again
                    urls_to_fetch.append(item.url)
            except Exception as e:
                logger.error(
                    f"{feed_name}: Error processing cached item {url}: {e}"
                )
                urls_to_fetch.append(url)
        else:
            urls_to_fetch.append(url)

    # Step 3: Fetch remaining items
    if urls_to_fetch:
        logger.info(f"{feed_name}: Fetching content for {len(urls_to_fetch)} items")
        results = await fetcher.fetch_urls(feed_name, urls_to_fetch)

        # Process and update items with fetched content
        for url, content, error in results:
            # Find the item with this URL
            item = next(
                (item for item in items_needing_content if str(item.url) == url), None
            )
            if not item:
                continue

            # Update cache first
            await db.set_content(
                url, content, success=error is None, error_reason=error
            )

            # Skip if fetch failed
            if not content:
                logger.warning(
                    f"{feed_name}: Failed to fetch content for {url}: {error}"
                )
                continue

            try:
                # Use the utility function to extract content
                content_data = extract_main_content(content, url)

                if content_data["content_html"] or content_data["content_text"]:
                    item.content_html = (
                        content_data["content_html"] or item.content_html
                    )
                    item.content_text = (
                        content_data["content_text"] or item.content_text
                    )
                    # Update title if the current one is very short and we found a better one
                    if (
                        content_data["title"]
                        and len(item.title) < 20
                        and len(content_data["title"]) > len(item.title)
                    ):
                        item.title = content_data["title"]
                    logger.debug(
                        f"{feed_name}: Successfully extracted content for {url}"
                    )
                else:
                    logger.warning(
                        f"{feed_name}: Fetched content for {url} but couldn't extract meaningful content"
                    )
            except Exception as e:
                logger.error(f"{feed_name}: Error extracting content from {url}: {e}")

    return items


async def _process_cached_feeds(
    db: Database, feed_name: str, feed_config: FeedConfig, ignore_before_time: datetime
) -> Tuple[List[FeedItem], List[str]]:
    """Process cached feeds and return items and uncached URLs."""
    items = []
    urls_to_fetch = []
    cache_hits = 0
    total_urls = len(feed_config.urls)

    for url in feed_config.urls:
        if cached := await db.get_content(url):
            cache_hits += 1
            try:
                feed_items = await _process_feed_entries(
                    cached, feed_config, ignore_before_time
                )
                items.extend(feed_items)
            except Exception as e:
                logger.error(
                    f"Error processing cached content from {url}: {e}", exc_info=True
                )
        else:
            urls_to_fetch.append(url)

    if not urls_to_fetch:
        logger.info(f"{feed_name} all {total_urls} feeds were cached")
    else:
        logger.info(
            f"{feed_name} fetching {len(urls_to_fetch)} uncached feeds, total {total_urls}"
        )

    return items, urls_to_fetch


async def _process_uncached_feeds(
    fetcher: FeedFetcher,
    db: Database,
    feed_name: str,
    urls_to_fetch: List[str],
    feed_config: FeedConfig,
    ignore_before_time: datetime,
) -> List[FeedItem]:
    """Fetch and process uncached feeds."""
    items = []

    if not urls_to_fetch:
        return items

    # Fetch all uncached feeds concurrently
    results = await fetcher.fetch_urls(feed_name, urls_to_fetch)

    # Process results and update cache sequentially
    for processed, (url, content, error) in enumerate(results, 1):
        # Update cache first
        await db.set_content(url, content, success=error is None, error_reason=error)

        # Process content if successful
        if not content:
            logger.warning(
                f"{feed_name} skipping {url} due to fetch failure ({processed}/{len(urls_to_fetch)})"
            )
            continue

        try:
            feed_items = await _process_feed_entries(
                content, feed_config, ignore_before_time
            )
            items.extend(feed_items)
            logger.info(
                f"{feed_name} processed {len(feed_items)} entries from {url} ({processed}/{len(urls_to_fetch)})"
            )
        except Exception as e:
            logger.error(f"{feed_name} error processing {url}: {e}", exc_info=True)

    return items


async def process_feeds(
    fetcher: FeedFetcher, db: Database, feed_name: str, feed_config: FeedConfig
) -> list[FeedItem]:
    """Process all feeds for a configuration."""
    week_ago = datetime.now(UTC) - timedelta(days=7)
    logger.info(f"{feed_name} processing {len(feed_config.urls)} feeds")

    # Process cached feeds
    cached_items, urls_to_fetch = await _process_cached_feeds(
        db, feed_name, feed_config, week_ago
    )

    # Process uncached feeds
    uncached_items = await _process_uncached_feeds(
        fetcher, db, feed_name, urls_to_fetch, feed_config, week_ago
    )

    # Combine all items
    all_items = cached_items + uncached_items

    # Fulfill item content if enabled
    if feed_config.fulfill and all_items:
        logger.info(f"{feed_name} fulfilling content for {len(all_items)} items")
        all_items = await _fulfill_items_content_if_needed(
            fetcher, db, feed_name, all_items
        )

    return all_items


async def main():
    logger.info("Starting feed forging")
    output_dir = Path("outputs")
    output_dir.mkdir(exist_ok=True)

    db = Database()
    await db.init()
    await db.cleanup()

    fetcher = FeedFetcher()
    try:
        for feed_name, feed_config in get_recipes().items():
            logger.info(f"{feed_name} processing")

            # Process feeds and get items
            items = await process_feeds(fetcher, db, feed_name, feed_config)

            # Create and save feed using the model's class method
            feed = Feed.create_from_items(feed_name, items)
            output_path = output_dir / f"{feed_name}.json"
            output_path.write_text(feed.model_dump_json(indent=2, exclude_none=True))

            logger.info(
                f"{feed_name} generated feed file: {output_path}, {len(items)} items"
            )
    finally:
        await fetcher.close()


if __name__ == "__main__":
    asyncio.run(main())
