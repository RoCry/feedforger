import re
from typing import Any


def should_include_item(entry: dict[str, Any], filters: list[dict]) -> bool:
    if not filters:
        return True

    for f in filters:
        if not f.title:
            # only support title filter for now
            continue
        title = entry.get("title", "")
        if not title:
            # skip if no title
            continue

        matches = bool(re.search(f.title, title, re.IGNORECASE))
        if f.invert:
            matches = not matches

        if not matches:
            return False

    return True
