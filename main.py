import asyncio
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

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


def _extract_author(entry: dict, feed: dict) -> Optional[Author]:
    """Extract author information from a feed entry."""
    author = entry.get("author")
    if not author:
        author = feed.feed.get("author")
        
    if isinstance(author, dict):
        return Author(
            name=author.get("name"),
            url=author.get("uri") or author.get("href"),
            avatar=author.get("avatar"),
        )
    elif isinstance(author, str):
        return Author(name=author)
    return None


def _extract_content(entry: dict) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Extract content_html, content_text and summary from a feed entry."""
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
        
    return content_html, content_text, summary


def _extract_image(entry: dict) -> Optional[str]:
    """Extract image URL from a feed entry."""
    if media_content := entry.get("media_content", []):
        for media in media_content:
            if media.get("medium") == "image":
                return media.get("url")
    
    if entry.get("image"):
        return entry.get("image").get("href")
    
    return None


def _extract_tags(entry: dict) -> List[str]:
    """Extract tags from a feed entry."""
    if entry.get("tags"):
        return [tag.get("term", "") for tag in entry.get("tags")]
    elif entry.get("categories"):
        return entry.get("categories")
    return []


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

        # Extract all entry components
        author = _extract_author(entry, feed)
        content_html, content_text, summary = _extract_content(entry)
        image = _extract_image(entry)
        tags = _extract_tags(entry)

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


async def _fulfill_items_content_if_needed(
    fetcher: FeedFetcher, db: Database, feed_name: str, items: List[FeedItem]
) -> List[FeedItem]:
    if not items:
        return items
    
    # 1. ignore items that already have substantial content
    # 2. fulfill with cache if possible
    # 3. fetch and cache remaining items

    # TODO: implement
 
    return items


async def _process_cached_feeds(
    db: Database, 
    feed_name: str, 
    feed_config: FeedConfig, 
    ignore_before_time: datetime
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
    ignore_before_time: datetime
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
        all_items = await _fulfill_items_content_if_needed(fetcher, db, feed_name, all_items)

    return all_items


def _create_feed(feed_name: str, items: List[FeedItem]) -> Feed:
    """Create a Feed object from the processed items."""
    feed_url = f"https://github.com/RoCry/feedforger/releases/download/latest/{urllib.parse.quote(feed_name)}.json"
    home_url = "https://github.com/RoCry/feedforger/releases/tag/latest"
    
    return Feed(
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
            
            # Create and save feed
            feed = _create_feed(feed_name, items)
            output_path = output_dir / f"{feed_name}.json"
            output_path.write_text(feed.model_dump_json(indent=2, exclude_none=True))
            
            logger.info(
                f"{feed_name} generated feed file: {output_path}, {len(items)} items"
            )
    finally:
        await fetcher.close()


if __name__ == "__main__":
    asyncio.run(main())
