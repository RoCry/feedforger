from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class FeedItem(BaseModel):
    id: str  # URL as primary key
    title: str
    link: str
    description: Optional[str] = None
    published: datetime
    source: str  # Feed name from recipe


class Feed(BaseModel):
    title: str
    items: list[FeedItem]
