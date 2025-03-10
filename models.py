from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, HttpUrl


class Author(BaseModel):
    name: str
    url: Optional[HttpUrl] = None
    avatar: Optional[HttpUrl] = None

    @classmethod
    def from_feed_data(
        cls, author_data: Any, feed_data: dict = None
    ) -> Optional["Author"]:
        """Create an Author instance from feed data."""
        if not author_data and feed_data:
            author_data = feed_data.get("author")

        if isinstance(author_data, dict):
            return cls(
                name=author_data.get("name"),
                url=author_data.get("uri") or author_data.get("href"),
                avatar=author_data.get("avatar"),
            )
        elif isinstance(author_data, str):
            return cls(name=author_data)
        return None


class FeedItem(BaseModel):
    id: str
    url: HttpUrl
    title: str
    content_text: Optional[str] = None
    content_html: Optional[str] = None
    summary: Optional[str] = None
    date_published: datetime
    author: Optional[Author] = None
    tags: List[str] = Field(default_factory=list)
    language: Optional[str] = None
    image: Optional[HttpUrl] = None  # Main image URL
    banner_image: Optional[HttpUrl] = None  # Banner image URL
    external_url: Optional[HttpUrl] = None  # For linkblog-style entries

    @staticmethod
    def _extract_content(
        entry: dict,
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
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

    @staticmethod
    def _extract_image(entry: dict) -> Optional[str]:
        """Extract image URL from a feed entry."""
        if media_content := entry.get("media_content", []):
            for media in media_content:
                if media.get("medium") == "image":
                    return media.get("url")

        if entry.get("image"):
            return entry.get("image").get("href")

        return None

    @staticmethod
    def _extract_tags(entry: dict) -> List[str]:
        """Extract tags from a feed entry."""
        if entry.get("tags"):
            return [tag.get("term", "") for tag in entry.get("tags")]
        elif entry.get("categories"):
            return entry.get("categories")
        return []

    @classmethod
    def from_feed_entry(
        cls,
        entry: dict,
        feed_data: dict,
        published: datetime,
        feed_language: Optional[str] = None,
    ) -> "FeedItem":
        """Create a FeedItem from a feedparser entry."""
        # Extract all entry components
        author = Author.from_feed_data(entry.get("author"), feed_data)
        content_html, content_text, summary = cls._extract_content(entry)
        image = cls._extract_image(entry)
        tags = cls._extract_tags(entry)

        return cls(
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


class Feed(BaseModel):
    version: str = "https://jsonfeed.org/version/1.1"
    title: str
    description: Optional[str] = None
    home_page_url: Optional[HttpUrl] = None
    feed_url: Optional[HttpUrl] = None
    items: List[FeedItem]
    icon: Optional[HttpUrl] = None  # Feed icon (large, e.g. 512x512)
    favicon: Optional[HttpUrl] = None  # Small icon (e.g. 64x64)
    authors: Optional[List[Author]] = None
    language: Optional[str] = None
    user_comment: Optional[str] = None

    @classmethod
    def create_from_items(
        cls,
        feed_name: str,
        items: List[FeedItem],
        base_url: str = "https://github.com/RoCry/feedforger/releases",
    ) -> "Feed":
        """Create a Feed from a list of FeedItems."""
        import urllib.parse

        feed_url = f"{base_url}/download/latest/{urllib.parse.quote(feed_name)}.json"
        home_url = f"{base_url}/tag/latest"

        return cls(
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


class FeedFilter(BaseModel):
    # the pattern to match the title, optional for potential body filter
    title: Optional[str] = None
    invert: bool = False  # whether to invert the match


class FeedConfig(BaseModel):
    urls: List[str]
    filters: List[FeedFilter] = Field(default_factory=list)
    fulfill: bool = False  # whether to fetch content for each item


class RecipeCollection(BaseModel):
    recipes: Dict[str, FeedConfig]
