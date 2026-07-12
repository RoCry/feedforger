from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from bs4 import BeautifulSoup, Comment
from dateutil import parser as date_parser

from feedforger.log import logger
from feedforger.models import Author, FeedItem, Source

HTML_SUMMARY_MAX_LENGTH = 280
SUMMARY_MAX_LENGTH = 320
SUBSTANTIAL_HTML_LENGTH = 700
SUBSTANTIAL_TEXT_LENGTH = 400

_CONTENT_SELECTORS = (
    "article",
    "main",
    ".post-content",
    ".entry-content",
    ".article-content",
    "#content",
)
_NOISE_TAGS = ("script", "style", "noscript", "iframe")
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class _EmbeddedContent:
    html: str | None
    text: str | None
    summary: str | None


@dataclass(frozen=True, slots=True)
class _PageContent:
    html: str | None = None
    text: str | None = None
    title: str | None = None


def _sanitize_html(html: str) -> str:
    """Remove non-content HTML while preserving the original on parse failure."""
    if not html:
        return html
    try:
        soup = BeautifulSoup(html, "html.parser")
        for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
            comment.extract()
        for element in soup.find_all(_NOISE_TAGS):
            element.decompose()
        return str(soup).strip()
    except Exception as error:
        logger.warning(f"HTML sanitization failed: {error}")
        return html


def _truncate(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip() + "…"


def _html_to_summary(html: str) -> str | None:
    if not html:
        return None
    text = BeautifulSoup(html, "html.parser").get_text(separator=" ", strip=True)
    text = _WS_RE.sub(" ", text).strip()
    return _truncate(text, HTML_SUMMARY_MAX_LENGTH) if text else None


def _first_image_in_html(html: str) -> str | None:
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    if not (image := soup.find("img")):
        return None
    source = image.get("src") or image.get("data-src")
    if isinstance(source, str) and source.startswith(("http://", "https://")):
        return source
    return None


def _extract_embedded_content(entry: Mapping[str, Any]) -> _EmbeddedContent:
    content_html: str | None = None
    content_text: str | None = None

    contents = entry.get("content")
    if isinstance(contents, list) and contents:
        content = contents[0]
        if isinstance(content, Mapping) and isinstance(content.get("value"), str):
            if content.get("type") == "text/html":
                content_html = content["value"]
            else:
                content_text = content["value"]
    elif isinstance(raw_summary := entry.get("summary"), str) and raw_summary:
        if raw_summary.lstrip().startswith("<"):
            content_html = raw_summary
        else:
            content_text = raw_summary

    raw_summary = entry.get("summary")
    summary: str | None = None
    if (
        isinstance(raw_summary, str)
        and raw_summary
        and not raw_summary.lstrip().startswith("<")
    ):
        summary = raw_summary.strip()
    elif content_html:
        summary = _html_to_summary(content_html)
    elif content_text:
        summary = content_text.strip()

    if summary:
        summary = _truncate(summary, SUMMARY_MAX_LENGTH)
    if summary and content_text and content_text.strip().startswith(summary):
        summary = None
    if content_html:
        content_html = _sanitize_html(content_html)

    return _EmbeddedContent(
        html=content_html,
        text=content_text,
        summary=summary,
    )


def _extract_author(
    author_data: Any,
    feed_meta: Mapping[str, Any],
) -> Author | None:
    if not author_data:
        author_data = feed_meta.get("author")

    if isinstance(author_data, Mapping):
        if not (name := author_data.get("name")):
            return None
        return Author.model_validate(
            {
                "name": name,
                "url": author_data.get("uri") or author_data.get("href"),
                "avatar": author_data.get("avatar"),
            }
        )
    if isinstance(author_data, str):
        return Author(name=author_data)
    return None


def _extract_tags(entry: Mapping[str, Any]) -> list[str]:
    if tags := entry.get("tags"):
        return [
            term
            for tag in tags
            if isinstance(tag, Mapping)
            and isinstance(term := tag.get("term"), str)
            and (term := term.strip())
        ]
    if isinstance(categories := entry.get("categories"), list):
        return [category for category in categories if isinstance(category, str)]
    return []


def _extract_source(
    feed_meta: Mapping[str, Any],
    source_url: str | None,
) -> Source | None:
    if not feed_meta and not source_url:
        return None
    title_value = feed_meta.get("title")
    title = title_value.strip() if isinstance(title_value, str) else None
    home_page_url = feed_meta.get("link") or None
    if not title and not home_page_url and not source_url:
        return None
    return Source.model_validate(
        {
            "title": title or None,
            "url": source_url,
            "home_page_url": home_page_url,
        }
    )


def _extract_image(
    entry: Mapping[str, Any],
    content_html: str | None,
) -> str | None:
    for media in entry.get("media_content", []) or []:
        if (
            isinstance(media, Mapping)
            and media.get("medium") == "image"
            and isinstance(media.get("url"), str)
        ):
            return media["url"]

    for thumbnail in entry.get("media_thumbnail", []) or []:
        if isinstance(thumbnail, Mapping) and isinstance(thumbnail.get("url"), str):
            return thumbnail["url"]

    if isinstance(image := entry.get("image"), Mapping) and isinstance(
        image.get("href"), str
    ):
        return image["href"]

    for enclosure in entry.get("enclosures", []) or []:
        if not isinstance(enclosure, Mapping):
            continue
        media_type = enclosure.get("type")
        url = enclosure.get("href") or enclosure.get("url")
        if (
            isinstance(media_type, str)
            and media_type.lower().startswith("image/")
            and isinstance(url, str)
        ):
            return url

    return _first_image_in_html(content_html) if content_html else None


def _extract_page_content(html: str, url: str) -> _PageContent:
    try:
        soup = BeautifulSoup(html, "html.parser")
        title = (
            title_tag.get_text(strip=True)
            if (title_tag := soup.find("title"))
            else None
        )

        content = next(
            (
                selected
                for selector in _CONTENT_SELECTORS
                if (selected := soup.select_one(selector))
            ),
            None,
        )
        if content is None and (divs := soup.find_all("div")):
            content = max(divs, key=lambda div: len(div.get_text()))
        if content is None:
            return _PageContent(title=title)

        content_html = _sanitize_html(str(content))
        content_text = BeautifulSoup(content_html, "html.parser").get_text(
            separator=" ", strip=True
        )
        if not content_text:
            return _PageContent(title=title)
        return _PageContent(
            html=content_html or None,
            text=content_text or None,
            title=title,
        )
    except Exception as error:
        raise RuntimeError(f"Failed to extract Content from {url}") from error


def build_item_content(
    entry: Mapping[str, Any],
    feed_meta: Mapping[str, Any],
    published: datetime,
    *,
    feed_language: str | None = None,
    source_url: str | None = None,
    page_html: str | None = None,
) -> FeedItem | None:
    """Build a finished item from feed data and optional fetched-page HTML."""
    link = entry.get("link")
    title = entry.get("title")
    if not isinstance(link, str) or not link or not isinstance(title, str) or not title:
        return None

    embedded = _extract_embedded_content(entry)
    content_html = embedded.html
    content_text = embedded.text

    if page_html:
        page = _extract_page_content(page_html, link)
        if page.html or page.text:
            content_html = page.html or content_html
            content_text = page.text or content_text
            if page.title and len(title) < 20 and len(page.title) > len(title):
                title = page.title

    source_data = entry.get("source")
    external_url = source_data.get("href") if isinstance(source_data, Mapping) else None
    return FeedItem.model_validate(
        {
            "id": entry.get("id") or link,
            "url": link,
            "title": title,
            "content_text": content_text,
            "content_html": content_html,
            "summary": embedded.summary,
            "date_published": published,
            "author": _extract_author(entry.get("author"), feed_meta),
            "tags": _extract_tags(entry),
            "language": feed_language or None,
            "image": _extract_image(entry, content_html),
            "external_url": external_url,
            "_source": _extract_source(feed_meta, source_url),
        }
    )


def needs_fulfillment(item: FeedItem) -> bool:
    """Return whether an item lacks substantial embedded or fetched content."""
    return not (
        (
            item.content_html is not None
            and len(item.content_html) > SUBSTANTIAL_HTML_LENGTH
        )
        or (
            item.content_text is not None
            and len(item.content_text) > SUBSTANTIAL_TEXT_LENGTH
        )
    )


def parse_date(date_str: str) -> datetime | None:
    try:
        return date_parser.parse(date_str).astimezone(UTC)
    except (ValueError, TypeError):
        return None
