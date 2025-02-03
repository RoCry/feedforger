import asyncio
from datetime import datetime, timedelta, UTC
import json
from pathlib import Path

import httpx
import feedparser
from dateutil import parser as date_parser
import re

from models import FeedItem, Feed
from db import Database
from recipes import get_recipes


async def fetch_feed(client: httpx.AsyncClient, url: str) -> str:
    response = await client.get(url)
    response.raise_for_status()
    return response.text


def parse_date(date_str: str) -> datetime:
    try:
        return date_parser.parse(date_str).astimezone(UTC)
    except (ValueError, TypeError):
        return datetime.now(UTC)


def should_include_item(entry: dict, filters: list[dict]) -> bool:
    if not filters:
        return True

    for filter_rule in filters:
        pattern = filter_rule["title"]
        invert = filter_rule.get("invert", False)
        matches = bool(re.search(pattern, entry.get("title", ""), re.IGNORECASE))
        if invert:
            matches = not matches
        if not matches:
            return False
    return True


async def process_feed(
    client: httpx.AsyncClient, db: Database, feed_name: str, feed_config: dict
) -> list[FeedItem]:
    items = []
    week_ago = datetime.now(UTC) - timedelta(days=7)

    for url in feed_config["urls"]:
        try:
            content = await fetch_feed(client, url)
            feed = feedparser.parse(content)

            for entry in feed.entries:
                published = parse_date(entry.get("published", ""))
                if published < week_ago:
                    continue

                if not should_include_item(entry, feed_config.get("filters", [])):
                    continue

                item = FeedItem(
                    id=entry.link,
                    title=entry.title,
                    link=entry.link,
                    description=entry.get("description"),
                    published=published,
                    source=feed_name,
                )
                await db.upsert_item(item)
                items.append(item)

        except Exception as e:
            print(f"Error processing {url}: {e}")

    return items


async def main():
    output_dir = Path("outputs")
    output_dir.mkdir(exist_ok=True)

    db = Database()
    await db.init()

    async with httpx.AsyncClient() as client:
        for feed_name, feed_config in get_recipes().items():
            items = await process_feed(client, db, feed_name, feed_config)

            feed = Feed(
                title=feed_name,
                items=sorted(items, key=lambda x: x.published, reverse=True),
            )

            output_path = output_dir / f"{feed_name}.json"
            output_path.write_text(feed.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
