from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from bs4 import BeautifulSoup, Comment
from dateutil import parser as date_parser

from feedforger.log import logger


@dataclass(slots=True)
class ExtractedContent:
    content_html: str | None = None
    content_text: str | None = None
    title: str | None = None


def clean_html(html: str) -> str:
    """Strip noise (comments, scripts, styles) from HTML content_html."""
    if not html:
        return html
    try:
        soup = BeautifulSoup(html, "html.parser")
        for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
            c.extract()
        for el in soup.find_all(["script", "style", "noscript", "iframe"]):
            el.decompose()
        return str(soup).strip()
    except Exception as e:
        logger.warning(f"clean_html failed: {e}")
        return html


def first_image_in_html(html: str) -> str | None:
    """Return the first <img src> URL inside the given HTML, or None."""
    if not html:
        return None
    try:
        soup = BeautifulSoup(html, "html.parser")
        img = soup.find("img")
        if not img:
            return None
        src = img.get("src") or img.get("data-src")
        if src and src.startswith(("http://", "https://")):
            return src
    except Exception as e:
        logger.warning(f"first_image_in_html failed: {e}")
    return None


_WS_RE = re.compile(r"\s+")


def html_to_summary(html: str, max_len: int = 280) -> str | None:
    """Convert HTML to a short plain-text summary (~max_len chars)."""
    if not html:
        return None
    try:
        text = BeautifulSoup(html, "html.parser").get_text(separator=" ", strip=True)
        text = _WS_RE.sub(" ", text).strip()
        if not text:
            return None
        if len(text) <= max_len:
            return text
        return text[: max_len - 1].rstrip() + "…"
    except Exception:
        return None


def extract_main_content(html: str, url: str) -> ExtractedContent:
    """Extract the main content from an HTML page."""
    result = ExtractedContent()
    try:
        soup = BeautifulSoup(html, "html.parser")

        if title_tag := soup.find("title"):
            result.title = title_tag.get_text(strip=True)

        content = None
        for selector in [
            "article",
            "main",
            ".post-content",
            ".entry-content",
            ".article-content",
            "#content",
        ]:
            if content := soup.select_one(selector):
                break

        if not content:
            divs = soup.find_all("div")
            if divs:
                content = max(divs, key=lambda d: len(d.get_text()))

        if content:
            for element in content.find_all(["script", "style", "iframe", "noscript"]):
                element.decompose()
            for c in content.find_all(string=lambda t: isinstance(t, Comment)):
                c.extract()
            result.content_html = str(content)
            result.content_text = content.get_text(separator=" ", strip=True)

    except Exception as e:
        logger.error(f"Error extracting content from {url}: {e}")

    return result


def parse_date(date_str: str) -> datetime | None:
    try:
        return date_parser.parse(date_str).astimezone(UTC)
    except (ValueError, TypeError):
        return None
