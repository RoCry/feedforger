from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import feedparser

from feedforger.content import build_item_content, needs_fulfillment, parse_date
from feedforger.content_store import ContentStore
from feedforger.filters import should_include_item
from feedforger.log import logger, setup_logging
from feedforger.models import Feed, FeedConfig, FeedItem
from feedforger.recipes import load_recipes
from feedforger.settings import Settings


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


def _process_feed_entries(
    content: str,
    feed_config: FeedConfig,
    ignore_before: datetime,
    *,
    source_url: str,
) -> list[_BuiltItem]:
    items: list[_BuiltItem] = []
    feed = feedparser.parse(content)
    feed_language = feed.feed.get("language", "").split("-")[0].lower()

    for entry in feed.entries:
        date_value = entry.get("published", "") or entry.get("updated", "")
        entry_url = entry.get("link") or entry.get("id") or "<unknown>"
        if not date_value:
            logger.warning(f"No date found for {entry_url}")
            continue

        published = parse_date(date_value)
        if published is None:
            logger.warning(f"Failed to parse date: '{date_value}' for {entry_url}")
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
        if item := _build_item(source):
            items.append(_BuiltItem(source=source, item=item))
        else:
            logger.warning(f"Skipping entry without title/link in feed: {entry!r:.120}")

    return items


async def _fulfill_items_content(
    store: ContentStore,
    settings: Settings,
    feed_name: str,
    items: list[_BuiltItem],
) -> None:
    pending = [built for built in items if needs_fulfillment(built.item)]
    if not pending:
        logger.info(f"{feed_name}: all {len(items)} items have substantial Content")
        return

    logger.info(f"{feed_name}: {len(pending)}/{len(items)} items need Content")
    pages = await asyncio.gather(
        *(store.get(str(built.item.url), ttl=settings.article_ttl) for built in pending)
    )
    for built, page_html in zip(pending, pages, strict=True):
        if page_html and (rebuilt := _build_item(built.source, page_html=page_html)):
            built.item = rebuilt


async def process_feeds(
    store: ContentStore,
    settings: Settings,
    feed_name: str,
    feed_config: FeedConfig,
) -> list[FeedItem]:
    ignore_before = datetime.now(UTC) - settings.since
    logger.info(f"{feed_name}: processing {len(feed_config.urls)} feeds")

    contents = await asyncio.gather(
        *(store.get(url, ttl=settings.feed_ttl) for url in feed_config.urls)
    )
    built_items: list[_BuiltItem] = []
    for processed, (url, content) in enumerate(
        zip(feed_config.urls, contents, strict=True),
        1,
    ):
        if not content:
            logger.warning(f"{feed_name}: skipping {url} ({processed}/{len(contents)})")
            continue
        try:
            feed_items = _process_feed_entries(
                content,
                feed_config,
                ignore_before,
                source_url=url,
            )
            built_items.extend(feed_items)
            logger.info(
                f"{feed_name}: processed {len(feed_items)} entries from {url} "
                f"({processed}/{len(contents)})"
            )
        except Exception as error:
            raise RuntimeError(f"{feed_name}: failed to process {url}") from error

    if feed_config.fulfill and built_items:
        logger.info(f"{feed_name}: fulfilling Content for {len(built_items)} items")
        await _fulfill_items_content(store, settings, feed_name, built_items)

    return [built.item for built in built_items]


async def run_build(
    store: ContentStore,
    settings: Settings,
) -> None:
    setup_logging()
    logger.info("Starting feed forging")
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    await store.cleanup(retention=settings.cleanup_retention)

    recipes = load_recipes(settings.recipes_path)
    logger.info(f"Loaded {len(recipes)} recipes from {settings.recipes_path}")
    failing_urls = await store.persistently_failing_urls()
    for feed_name, feed_config in recipes.items():
        active_urls = [url for url in feed_config.urls if url not in failing_urls]
        skipped = len(feed_config.urls) - len(active_urls)
        if skipped:
            logger.info(f"{feed_name}: skipping {skipped} persistently failing URLs")
        if not active_urls:
            logger.warning(
                f"{feed_name}: all URLs are persistently failing; preserving prior output"
            )
            continue

        active_config = feed_config.model_copy(update={"urls": active_urls})
        items = await process_feeds(store, settings, feed_name, active_config)
        feed = Feed.create_from_items(
            feed_name,
            items,
            base_url=settings.base_url,
        )
        output_path = settings.output_dir / f"{feed_name}.json"
        output_path.write_text(
            feed.model_dump_json(indent=2, exclude_none=True, by_alias=True)
        )
        logger.info(f"{feed_name}: generated {output_path}, {len(items)} items")
