import asyncio
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import List, Optional

import feedparser
from dateutil import parser as date_parser
import json

from db import Database
from filters import should_include_item
from models import Author, Feed, FeedConfig, FeedItem
from network import FeedFetcher
from recipes import get_recipes
from utils import logger


def parse_date(date_str: str) -> Optional[datetime]:
    try:
        return date_parser.parse(date_str).astimezone(UTC)
    except (ValueError, TypeError):
        return None


async def process_feed_entries(
    content: str, feed_config: FeedConfig, ignore_before_time: datetime
) -> list[FeedItem]:
    """Process a single feed's content and return items."""
    items = []
    feed = feedparser.parse(content)

    # Get feed language
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
        if published < ignore_before_time:
            continue

        if not should_include_item(entry, feed_config.filters):
            continue

        # Extract author information
        author = entry.get("author")
        if not author:
            author = feed.feed.get("author")
        if isinstance(author, dict):
            author = Author(
                name=author.get("name"),
                url=author.get("uri") or author.get("href"),
                avatar=author.get("avatar"),
            )
        elif isinstance(author, str):
            author = Author(name=author)
        else:
            author = None

        # Get content
        content_html = None
        content_text = None
        summary = None
        if entry.get("content"):
            content = entry.get("content")[0]
            if content.get("type") == "text/html":
                content_html = content.get("value")
            else:
                content_text = content.get("value")
            summary = content.get("summary")
        else:
            summary = entry.get("summary")
            if summary and summary.startswith("<"):
                content_html = summary
            else:
                content_text = summary
            summary = None  # prefer put summary in content

        # Get images
        image = None
        if media_content := entry.get("media_content", []):
            for media in media_content:
                if media.get("medium") == "image":
                    image = media.get("url")
                    break
        if not image and entry.get("image"):
            image = entry.get("image").get("href")

        # Get tags
        tags = []
        if entry.get("tags"):
            tags.extend(tag.get("term", "") for tag in entry.get("tags"))
        elif entry.get("categories"):
            tags.extend(entry.get("categories"))

        item = FeedItem(
            id=entry.link,
            url=entry.link,
            title=entry.title,
            content_text=content_text,
            content_html=content_html,
            summary=summary,
            date_published=published,
            author=author,
            tags=tags,
            language=feed_language or None,
            image=image,
            external_url=entry.get("source", {}).get("href"),
        )
        items.append(item)

    return items


async def fulfill_items_content(
    fetcher: FeedFetcher, db: Database, feed_name: str, items: List[FeedItem]
) -> List[FeedItem]:
    """
    Fulfill content for feed items by fetching full content from their URLs.
    Returns the updated list of items.
    """
    if not items:
        return items

    # Get URLs that need fulfillment (items with minimal content)
    urls_to_fulfill = []
    url_to_item_map = {}

    for item in items:
        # Skip items that already have substantial content
        has_substantial_content = (
            item.content_html and len(item.content_html) > 500
        ) or (item.content_text and len(item.content_text) > 300)
        if not has_substantial_content:
            urls_to_fulfill.append(item.url)
            url_to_item_map[item.url] = item

    if not urls_to_fulfill:
        logger.info(f"{feed_name} no items need content fulfillment")
        return items

    # Check cache first
    logger.info(
        f"{feed_name} checking cache for {len(urls_to_fulfill)} items to fulfill"
    )
    cached_json_contents = await db.batch_get_content(urls_to_fulfill)
    
    # Parse JSON strings into content dictionaries
    cached_contents = {}
    for url, json_content in cached_json_contents.items():
        if json_content:
            try:
                cached_contents[url] = json.loads(json_content)
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse cached JSON for {url}")

    # Apply cached content to items
    for url, content in cached_contents.items():
        if url in url_to_item_map and content:
            item = url_to_item_map[url]
            # Update item with fulfilled content
            if content.get("content_html"):
                item.content_html = content["content_html"]
            if content.get("content_text"):
                item.content_text = content["content_text"]
            # Update title if it was empty or very short
            if content.get("title") and (not item.title or len(item.title) < 10):
                item.title = content["title"]

    # Remove URLs that were successfully fulfilled from cache
    urls_to_fetch = [url for url in urls_to_fulfill if url not in cached_contents]

    if not urls_to_fetch:
        logger.info(
            f"{feed_name} all {len(urls_to_fulfill)} items were fulfilled from cache"
        )
        return items

    # Fetch remaining URLs
    logger.info(f"{feed_name} fetching content for {len(urls_to_fetch)} items")
    content_results = await fetcher.fetch_items_content(feed_name, urls_to_fetch)

    # Update cache with new content
    # Convert content to JSON strings before storing
    for url, (content, success) in content_results.items():
        if content and success:
            json_content = json.dumps(content)
            await db.set_content(url, json_content, success)
        else:
            error_reason = content.get("error") if content else "Unknown error"
            await db.set_content(url, None, success, error_reason)

    # Apply fetched content to items
    for url, (content, _) in content_results.items():
        if url in url_to_item_map and content:
            item = url_to_item_map[url]
            # Update item with fulfilled content
            if content.get("content_html"):
                item.content_html = content["content_html"]
            if content.get("content_text"):
                item.content_text = content["content_text"]
            # Update title if it was empty or very short
            if content.get("title") and (not item.title or len(item.title) < 10):
                item.title = content["title"]

    return items


async def process_feeds(
    fetcher: FeedFetcher, db: Database, feed_name: str, feed_config: FeedConfig
) -> list[FeedItem]:
    """Process all feeds for a configuration."""
    items = []
    week_ago = datetime.now(UTC) - timedelta(days=7)

    total_urls = len(feed_config.urls)
    logger.info(f"{feed_name} processing {total_urls} feeds")

    # First check cache for all URLs
    urls_to_fetch = []
    cache_hits = 0
    for url in feed_config.urls:
        if cached := await db.get_content(url):
            cache_hits += 1
            try:
                feed_items = await process_feed_entries(
                    cached, feed_config, ignore_before_time=week_ago
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
        # Fetch all uncached feeds concurrently
        logger.info(
            f"{feed_name} fetching {len(urls_to_fetch)} uncached feeds, total {total_urls}"
        )
        results = await fetcher.fetch_urls(feed_name, urls_to_fetch)

        # Process results and update cache sequentially
        processed = 0
        for url, content, error in results:
            processed += 1
            # Update cache first
            await db.set_content(
                url, content, success=error is None, error_reason=error
            )
            # Process content if successful
            if not content:
                logger.warning(
                    f"{feed_name} skipping {url} due to fetch failure ({processed}/{len(urls_to_fetch)})"
                )
                continue

            try:
                feed_items = await process_feed_entries(
                    content, feed_config, ignore_before_time=week_ago
                )
                items.extend(feed_items)
                logger.info(
                    f"{feed_name} processed {len(feed_items)} entries from {url} ({processed}/{len(urls_to_fetch)})"
                )
            except Exception as e:
                logger.error(f"{feed_name} error processing {url}: {e}", exc_info=True)

    # Fulfill item content if enabled
    if feed_config.fulfill and items:
        logger.info(f"{feed_name} fulfilling content for {len(items)} items")
        items = await fulfill_items_content(fetcher, db, feed_name, items)

    return items


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
            items = await process_feeds(fetcher, db, feed_name, feed_config)

            feed_url = f"https://github.com/RoCry/feedforger/releases/download/latest/{urllib.parse.quote(feed_name)}.json"
            home_url = "https://github.com/RoCry/feedforger/releases/tag/latest"

            feed = Feed(
                title=feed_name,
                items=sorted(items, key=lambda x: x.date_published, reverse=True),
                description=f"Aggregated feed for {feed_name}",
                home_page_url=home_url,
                feed_url=feed_url,
                user_comment="Generated by FeedForger",
                language="en",
                authors=[],
                icon=None,
                favicon=None,
            )

            output_path = output_dir / f"{feed_name}.json"
            output_path.write_text(feed.model_dump_json(indent=2, exclude_none=True))
            logger.info(
                f"{feed_name} generated feed file: {output_path}, {len(items)} items"
            )
    finally:
        await fetcher.close()


if __name__ == "__main__":
    asyncio.run(main())
