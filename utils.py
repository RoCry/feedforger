import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Optional

from bs4 import BeautifulSoup
from dateutil import parser as date_parser


def setup_logger(name: str = "feedforger") -> logging.Logger:
    logger = logging.getLogger(name)

    if not logger.handlers:  # Avoid adding handlers multiple times
        # Get log level from environment variable, default to INFO
        log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        level = getattr(logging, log_level, logging.INFO)
        logger.setLevel(level)

        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

        # File handler
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "feedforger.log")
        file_handler.setLevel(level)
        file_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

    return logger


logger = setup_logger()


def extract_main_content(html: str, url: str) -> Dict[str, Any]:
    """
    Extract the main content from an HTML page.
    Returns a dictionary with content_html, content_text, and title.
    """
    result = {
        "content_html": None,
        "content_text": None,
        "title": None,
    }

    try:
        soup = BeautifulSoup(html, "html.parser")

        # Extract title
        if title_tag := soup.find("title"):
            result["title"] = title_tag.get_text(strip=True)

        # Extract main content - prioritize article tags, then main tag, then content divs
        content = None

        # Try common content containers
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

        # If no content found, take the largest div
        if not content:
            divs = soup.find_all("div")
            if divs:
                # Find div with most text content
                content = max(divs, key=lambda d: len(d.get_text()))

        if content:
            # Clean content
            # Remove script and style elements
            for element in content.find_all(["script", "style", "iframe", "noscript"]):
                element.decompose()

            result["content_html"] = str(content)
            result["content_text"] = content.get_text(separator=" ", strip=True)

        return result
    except Exception as e:
        logger.error(f"Error extracting content from {url}: {e}")
        return result


def parse_date(date_str: str) -> Optional[datetime]:
    try:
        return date_parser.parse(date_str).astimezone(UTC)
    except (ValueError, TypeError):
        return None
