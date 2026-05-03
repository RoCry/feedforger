from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from feedforger.content import clean_html, first_image_in_html, html_to_summary


class Author(BaseModel):
    name: str
    url: HttpUrl | None = None
    avatar: HttpUrl | None = None

    @classmethod
    def from_feed_data(
        cls, author_data: Any, feed_data: dict | None = None
    ) -> Author | None:
        """Create an Author instance from feed data."""
        if not author_data and feed_data:
            author_data = feed_data.get("author")

        if isinstance(author_data, dict):
            name = author_data.get("name")
            if not name:
                return None
            return cls(
                name=name,
                url=author_data.get("uri") or author_data.get("href"),
                avatar=author_data.get("avatar"),
            )
        elif isinstance(author_data, str):
            return cls(name=author_data)
        return None


class Source(BaseModel):
    """JSON Feed `_source` extension: where this item originally came from.

    Critical when one aggregated feed merges many source blogs — without it,
    readers can't tell which blog a post belongs to.
    """

    title: str | None = None
    url: HttpUrl | None = None  # the source feed URL
    home_page_url: HttpUrl | None = None  # the source site URL


class FeedItem(BaseModel):
    # Allow `_source` (JSONFeed extension) by populating via alias.
    model_config = ConfigDict(populate_by_name=True)

    id: str
    url: HttpUrl
    title: str
    content_text: str | None = None
    content_html: str | None = None
    summary: str | None = None
    date_published: datetime
    author: Author | None = None
    tags: list[str] = Field(default_factory=list)
    language: str | None = None
    image: HttpUrl | None = None  # Main image URL
    banner_image: HttpUrl | None = None  # Banner image URL
    external_url: HttpUrl | None = None  # For linkblog-style entries
    source: Source | None = Field(
        default=None,
        alias="_source",
        serialization_alias="_source",
    )

    @staticmethod
    def _extract_content(
        entry: dict,
    ) -> tuple[str | None, str | None, str | None]:
        """Extract content_html, content_text and summary from a feed entry.

        Returns (content_html, content_text, summary). Summary is preserved as
        a short plain-text excerpt independent of content for nicer reader UX.
        """
        content_html: str | None = None
        content_text: str | None = None

        if entry.get("content"):
            content = entry["content"][0]
            value = content.get("value")
            if content.get("type") == "text/html":
                content_html = value
            else:
                content_text = value
        else:
            raw_summary = entry.get("summary")
            if raw_summary:
                if raw_summary.lstrip().startswith("<"):
                    content_html = raw_summary
                else:
                    content_text = raw_summary

        # Independent short summary: prefer feed-supplied summary, else derive
        # from content. Always plain-text-ish, capped, no leading repetition of
        # the title's HTML.
        raw_summary = entry.get("summary") or ""
        summary: str | None = None
        if raw_summary and not raw_summary.lstrip().startswith("<"):
            summary = raw_summary.strip()
        elif content_html:
            summary = html_to_summary(content_html)
        elif content_text:
            summary = content_text.strip()

        if summary and len(summary) > 320:
            summary = summary[:319].rstrip() + "…"

        # Drop summary if it's just a duplicate of (the start of) content_text.
        if summary and content_text and content_text.strip().startswith(summary):
            summary = None

        if content_html:
            content_html = clean_html(content_html)

        return content_html, content_text, summary

    @staticmethod
    def _extract_image(entry: dict, content_html: str | None) -> str | None:
        """Extract a single representative image URL from a feed entry."""
        # 1. media:content medium=image
        for media in entry.get("media_content", []) or []:
            if media.get("medium") == "image" and media.get("url"):
                return media["url"]

        # 2. media:thumbnail
        for thumb in entry.get("media_thumbnail", []) or []:
            if thumb.get("url"):
                return thumb["url"]

        # 3. atom <image>
        if image := entry.get("image"):
            href = image.get("href") if isinstance(image, dict) else None
            if href:
                return href

        # 4. RSS enclosure with image type
        for enc in entry.get("enclosures", []) or []:
            etype = (enc.get("type") or "").lower()
            href = enc.get("href") or enc.get("url")
            if href and etype.startswith("image/"):
                return href

        # 5. First <img> in content_html as last resort
        if content_html:
            return first_image_in_html(content_html)

        return None

    @staticmethod
    def _extract_tags(entry: dict) -> list[str]:
        """Extract tags from a feed entry."""
        if entry.get("tags"):
            tags = [tag.get("term", "").strip() for tag in entry.get("tags")]
            return [t for t in tags if t]
        elif entry.get("categories"):
            return entry.get("categories")
        return []

    @staticmethod
    def _extract_source(feed_data: dict, source_url: str | None) -> Source | None:
        """Build the `_source` block from feedparser's parsed feed metadata."""
        if not feed_data and not source_url:
            return None
        title = (feed_data.get("title") or "").strip() or None
        home = feed_data.get("link") or None
        if not title and not home and not source_url:
            return None
        return Source(title=title, url=source_url, home_page_url=home)

    @classmethod
    def from_feed_entry(
        cls,
        entry: dict,
        feed_data: dict,
        published: datetime,
        feed_language: str | None = None,
        source_url: str | None = None,
    ) -> FeedItem | None:
        """Create a FeedItem from a feedparser entry. Returns None if essential fields missing."""
        link = entry.get("link")
        title = entry.get("title")
        if not link or not title:
            return None

        author = Author.from_feed_data(entry.get("author"), feed_data)
        content_html, content_text, summary = cls._extract_content(entry)
        image = cls._extract_image(entry, content_html)
        tags = cls._extract_tags(entry)
        source = cls._extract_source(feed_data, source_url)

        return cls(
            id=entry.get("id") or link,
            url=link,
            title=title,
            content_text=content_text,
            content_html=content_html,
            summary=summary,
            date_published=published,
            author=author,
            tags=tags,
            language=feed_language or None,
            image=image,
            external_url=entry.get("source", {}).get("href"),
            source=source,
        )


class Feed(BaseModel):
    version: str = "https://jsonfeed.org/version/1.1"
    title: str
    description: str | None = None
    home_page_url: HttpUrl | None = None
    feed_url: HttpUrl | None = None
    items: list[FeedItem]
    icon: HttpUrl | None = None  # Feed icon (large, e.g. 512x512)
    favicon: HttpUrl | None = None  # Small icon (e.g. 64x64)
    authors: list[Author] | None = None
    language: str | None = None
    user_comment: str | None = None

    @classmethod
    def create_from_items(
        cls,
        feed_name: str,
        items: list[FeedItem],
        base_url: str | None = None,
    ) -> Feed:
        """Create a Feed from a list of FeedItems.

        base_url defaults to the GitHub releases URL of the current repository,
        derived from FEEDFORGER_BASE_URL or GITHUB_REPOSITORY env vars.
        """
        import os
        import urllib.parse

        if base_url is None:
            base_url = os.environ.get("FEEDFORGER_BASE_URL")
            if not base_url:
                repo = os.environ.get("GITHUB_REPOSITORY", "RoCry/feedforger")
                base_url = f"https://github.com/{repo}/releases"

        feed_url = f"{base_url}/download/latest/{urllib.parse.quote(feed_name)}.json"
        home_url = f"{base_url}/tag/latest"

        return cls(
            title=feed_name,
            items=sorted(items, key=lambda x: x.date_published, reverse=True),
            description=f"Aggregated feed for {feed_name}",
            home_page_url=home_url,
            feed_url=feed_url,
            user_comment="Generated by FeedForger",
            language=None,
            authors=[],
            icon=None,
            favicon=None,
        )


class FeedFilter(BaseModel):
    # the pattern to match the title, optional for potential body filter
    title: str | None = None
    invert: bool = False  # whether to invert the match


class FeedConfig(BaseModel):
    urls: list[str]
    filters: list[FeedFilter] = Field(default_factory=list)
    fulfill: bool = False  # whether to fetch content for each item


class RecipeCollection(BaseModel):
    recipes: dict[str, FeedConfig]
