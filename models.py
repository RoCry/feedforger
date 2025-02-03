from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class Author(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    avatar: Optional[str] = None


class FeedItem(BaseModel):
    id: str
    url: str
    title: Optional[str] = None
    content_text: Optional[str] = None
    content_html: Optional[str] = None
    summary: Optional[str] = None
    date_published: datetime
    author: Optional[Author] = None
    tags: Optional[List[str]] = None


class Feed(BaseModel):
    version: str = Field(default="https://jsonfeed.org/version/1.1")
    title: str
    home_page_url: Optional[str] = None
    feed_url: Optional[str] = None
    description: Optional[str] = None
    user_comment: Optional[str] = None
    next_url: Optional[str] = None
    icon: Optional[str] = None
    favicon: Optional[str] = None
    authors: Optional[List[Author]] = None
    language: Optional[str] = None
    items: List[FeedItem]
