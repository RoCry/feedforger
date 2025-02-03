import re
from typing import Any


def should_include_item(entry: dict[str, Any], filters: list[dict]) -> bool:
    if not filters:
        return True

    for filter_rule in filters:
        pattern = filter_rule["title"]
        invert = filter_rule.get("invert", False)

        title = entry.get("title", "")

        matches = bool(re.search(pattern, title, re.IGNORECASE))
        if invert:
            matches = not matches

        if not matches:
            return False

    return True
