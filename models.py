from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, HttpUrl


class Author(BaseModel):
    name: str
    url: Optional[HttpUrl] = None
    avatar: Optional[HttpUrl] = None


class FeedItem(BaseModel):
    id: str
    url: HttpUrl
    title: str
    content_text: Optional[str] = None
    content_html: Optional[str] = None
    summary: Optional[str] = None
    date_published: datetime
    author: Optional[Author] = None
    tags: List[str] = Field(default_factory=list)
    language: Optional[str] = None
    image: Optional[HttpUrl] = None  # Main image URL
    banner_image: Optional[HttpUrl] = None  # Banner image URL
    external_url: Optional[HttpUrl] = None  # For linkblog-style entries


class Feed(BaseModel):
    version: str = "https://jsonfeed.org/version/1.1"
    title: str
    description: Optional[str] = None
    home_page_url: Optional[HttpUrl] = None
    feed_url: Optional[HttpUrl] = None
    items: List[FeedItem]
    icon: Optional[HttpUrl] = None  # Feed icon (large, e.g. 512x512)
    favicon: Optional[HttpUrl] = None  # Small icon (e.g. 64x64)
    authors: Optional[List[Author]] = None
    language: Optional[str] = None
    user_comment: Optional[str] = None


class FeedFilter(BaseModel):
    # the pattern to match the title, optional for potential body filter
    title: Optional[str] = None
    invert: bool = False  # whether to invert the match


class FeedConfig(BaseModel):
    urls: List[str]
    filters: List[FeedFilter] = Field(default_factory=list)
    fulfill: bool  # whether to fetch content for each item


class RecipeCollection(BaseModel):
    recipes: Dict[str, FeedConfig]
