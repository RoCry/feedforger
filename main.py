import asyncio
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Optional
import urllib.parse

import httpx
import feedparser
from dateutil import parser as date_parser

from models import FeedItem, Feed, Author
from db import Database
from recipes import get_recipes
from filters import should_include_item
from utils import logger


async def fetch_feed(
    client: httpx.AsyncClient, url: str, db: Database
) -> Optional[str]:
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
        error_msg = f"{type(e).__name__}: {str(e)}"
        logger.error(f"Failed to fetch {url}: {error_msg}")
        # Update cache with failure
        await db.set_content(url, None, success=False, error_reason=error_msg)
        return None


def parse_date(date_str: str) -> datetime:
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

                # Get feed language
                feed_language = feed.feed.get("language", "").split("-")[0].lower()

                for entry in feed.entries:
                    dt = entry.get("published", "") or entry.get("updated", "")
                    if not dt:
                        logger.warning(f"No date found for {entry.link}")
                        continue
                    published = parse_date(dt)
                    if published < week_ago:
                        continue

                    if not should_include_item(entry, feed_config.get("filters", [])):
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
                    if entry.get("content"):
                        content = entry.get("content")[0]
                        if content.get("type") == "text/html":
                            content_html = content.get("value")
                        else:
                            content_text = content.get("value")
                    if not content_html and not content_text:
                        content_text = entry.get("summary", "")

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
                        summary=entry.get("summary"),
                        date_published=published,
                        author=author,
                        tags=tags,
                        language=feed_language or None,
                        image=image,
                        external_url=entry.get("source", {}).get("href"),
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
    await db.cleanup()

    async with httpx.AsyncClient(
        timeout=15.0,
        follow_redirects=True,
        max_redirects=3,
    ) as client:
        for feed_name, feed_config in get_recipes().items():
            logger.info(f"Processing feed: {feed_name}")
            items = await process_feed(client, db, feed_name, feed_config)

            feed_url = f"https://github.com/RoCry/feedforger/releases/download/latest/{urllib.parse.quote(feed_name)}.json"
            home_url = f"https://github.com/RoCry/feedforger/releases/tag/latest"

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
            logger.info(f"Generated feed file: {output_path}, {len(items)} items")


if __name__ == "__main__":
    asyncio.run(main())
