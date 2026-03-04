from __future__ import annotations

import tomllib
from pathlib import Path
from xml.etree.ElementTree import parse as parse_xml

from feedforger.log import logger
from feedforger.models import FeedConfig, RecipeCollection


def load_toml(path: Path) -> dict[str, FeedConfig]:
    """Load recipes from a TOML file."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    collection = RecipeCollection.model_validate(data)
    return collection.recipes


def _collect_opml_feeds(
    outline, feeds: dict[str, list[str]], current_group: str
) -> None:
    """Recursively collect feed URLs from OPML outline elements."""
    xml_url = outline.get("xmlUrl")
    if xml_url:
        feeds.setdefault(current_group, []).append(xml_url)

    for child in outline:
        child_text = child.get("text") or child.get("title") or current_group
        # If this child has sub-elements, it's a folder
        if len(child) > 0 and not child.get("xmlUrl"):
            _collect_opml_feeds(child, feeds, child_text)
        else:
            _collect_opml_feeds(child, feeds, current_group)


def load_opml(path: Path) -> dict[str, FeedConfig]:
    """Load recipes from an OPML file. Folder structure maps to recipe names."""
    tree = parse_xml(path)
    root = tree.getroot()
    body = root.find("body")
    if body is None:
        raise ValueError(f"Invalid OPML file (no <body>): {path}")

    feeds: dict[str, list[str]] = {}
    default_group = path.stem

    for outline in body:
        group = outline.get("text") or outline.get("title") or default_group
        # If this outline itself is a feed (has xmlUrl), use default group
        if outline.get("xmlUrl"):
            feeds.setdefault(default_group, []).append(outline.get("xmlUrl"))
        else:
            _collect_opml_feeds(outline, feeds, group)

    # If no groups were created, use file stem
    if not feeds:
        logger.warning(f"No feeds found in OPML file: {path}")
        return {}

    return {name: FeedConfig(urls=urls) for name, urls in feeds.items() if urls}


def load_recipes(path: Path) -> dict[str, FeedConfig]:
    """Load recipes from a file or directory. Supports .toml and .opml/.xml files."""
    if path.is_dir():
        recipes: dict[str, FeedConfig] = {}
        for file in sorted(path.iterdir()):
            if file.suffix in {".toml", ".opml", ".xml"}:
                loaded = load_recipes(file)
                for name, config in loaded.items():
                    if name in recipes:
                        # Merge URLs from duplicate recipe names
                        recipes[name] = FeedConfig(
                            urls=recipes[name].urls + config.urls,
                            filters=recipes[name].filters or config.filters,
                            fulfill=recipes[name].fulfill or config.fulfill,
                        )
                    else:
                        recipes[name] = config
                logger.info(f"Loaded {len(loaded)} recipes from {file.name}")
        return recipes

    suffix = path.suffix.lower()
    if suffix == ".toml":
        return load_toml(path)
    elif suffix in {".opml", ".xml"}:
        return load_opml(path)
    else:
        raise ValueError(f"Unsupported recipe file format: {path.suffix}")
