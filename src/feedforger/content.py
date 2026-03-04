from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from feedforger.log import logger


@dataclass(slots=True)
class ExtractedContent:
    content_html: str | None = None
    content_text: str | None = None
    title: str | None = None


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
