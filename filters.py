import re
from typing import Any
from utils import logger


def should_include_item(entry: dict[str, Any], filters: list[dict]) -> bool:
    """
    Check if an entry should be included based on filter rules.

    Args:
        entry: Feed entry to check
        filters: List of filter rules from recipe

    Returns:
        bool: True if entry should be included, False otherwise
    """
    if not filters:
        return True

    for filter_rule in filters:
        pattern = filter_rule["title"]
        invert = filter_rule.get("invert", False)

        title = entry.get("title", "")
        logger.debug(f"Checking title '{title}' against pattern '{pattern}'")

        matches = bool(re.search(pattern, title, re.IGNORECASE))
        if invert:
            matches = not matches

        if not matches:
            logger.debug(f"Entry '{title}' filtered out by rule '{pattern}'")
            return False

    logger.debug(f"Entry '{entry.get('title', '')}' passed all filters")
    return True
