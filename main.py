import asyncio
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Optional

import httpx
import feedparser
from dateutil import parser as date_parser

from models import FeedItem, Feed, Author
from db import Database
from recipes import get_recipes
from filters import should_include_item
from utils import logger


async def fetch_feed(client: httpx.AsyncClient, url: str, db: Database) -> Optional[str]:
    # Try to get from cache first
    if cached := await db.get_content(url):
        return cached
        
    logger.info(f"Fetching feed from '{url}'")
    try:
        # Fetch fresh content
        response = await client.get(url)
        response.raise_for_status()
        content = response.text
        
        # Update cache with success
        await db.set_content(url, content, success=True)
        return content
    except Exception as e:
        logger.error(f"Failed to fetch {url}: {e}")
        # Update cache with failure
        await db.set_content(url, None, success=False)
        return None


def parse_date(date_str: str) -> datetime:
    """Parse date string to UTC datetime."""
    try:
        return date_parser.parse(date_str).astimezone(UTC)
    except (ValueError, TypeError) as e:
        logger.warning(f"Failed to parse date '{date_str}': {e}")
        return datetime.now(UTC)


async def process_feed(
    client: httpx.AsyncClient, db: Database, feed_name: str, feed_config: dict
) -> list[FeedItem]:
    """Process a feed configuration and return feed items."""
    items = []
    week_ago = datetime.now(UTC) - timedelta(days=7)

    for url in feed_config["urls"]:
        try:
            if content := await fetch_feed(client, url, db):
                feed = feedparser.parse(content)
                logger.info(f"Processing {len(feed.entries)} entries from {url}")

                for entry in feed.entries:
                    published = parse_date(entry.get("published", ""))
                    if published < week_ago:
                        # logger.debug(f"Skipping old entry from {published}")
                        continue

                    if not should_include_item(entry, feed_config.get("filters", [])):
                        continue

                    # Extract author information if available
                    author = None
                    if entry.get("author_detail"):
                        author = Author(
                            name=entry.get("author_detail", {}).get("name"),
                            url=entry.get("author_detail", {}).get("href"),
                        )
                    elif entry.get("author"):  # Fallback to simple author string
                        author = Author(name=entry.get("author"))

                    # Get the best content available
                    content_html = None
                    content_text = None

                    # Try to get content from various possible fields
                    if entry.get("content"):
                        content = entry.get("content")[0]
                        if content.get("type") == "text/html":
                            content_html = content.get("value")
                        else:
                            content_text = content.get("value")

                    if not content_html and not content_text:
                        content_text = entry.get("summary", "")

                    # Get tags from categories if available
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
                        summary=entry.get("summary"),
                        date_published=published,
                        author=author,
                        tags=tags,
                    )
                    
                    items.append(item)
            else:
                logger.warning(f"Skipping {url} due to fetch failure")
                
        except Exception as e:
            logger.error(f"Error processing {url}: {e}", exc_info=True)

    return items


async def main():
    logger.info("Starting feed aggregation")
    output_dir = Path("outputs")
    output_dir.mkdir(exist_ok=True)
    
    db = Database()
    await db.init()
    
    # Cleanup old entries
    await db.cleanup()
    
    async with httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        max_redirects=5,
    ) as client:
        for feed_name, feed_config in get_recipes().items():
            logger.info(f"Processing feed: {feed_name}")
            items = await process_feed(client, db, feed_name, feed_config)

            feed = Feed(
                title=feed_name,
                items=sorted(items, key=lambda x: x.date_published, reverse=True),
                description=f"Aggregated feed for {feed_name}",
                user_comment="Generated by FeedForger",
            )

            output_path = output_dir / f"{feed_name}.json"
            output_path.write_text(feed.model_dump_json(indent=2))
            logger.info(f"Generated feed file: {output_path}, {len(items)} items")


if __name__ == "__main__":
    asyncio.run(main())
