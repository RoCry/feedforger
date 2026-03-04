from __future__ import annotations

import re
from typing import Any

from feedforger.models import FeedFilter


def should_include_item(entry: dict[str, Any], filters: list[FeedFilter]) -> bool:
    if not filters:
        return True

    for f in filters:
        if not f.title:
            continue
        title = entry.get("title", "")
        if not title:
            continue

        matches = bool(re.search(f.title, title, re.IGNORECASE))
        if f.invert:
            matches = not matches

        if not matches:
            return False

    return True
