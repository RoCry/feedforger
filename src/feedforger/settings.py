from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import ClassVar


@dataclass(frozen=True, slots=True, kw_only=True)
class Settings:
    DEFAULT_RECIPES_PATH: ClassVar[Path] = Path("recipes")
    DEFAULT_OUTPUT_DIR: ClassVar[Path] = Path("outputs")
    DEFAULT_REPORT_PATH: ClassVar[Path] = Path("cache/failure_report.json")
    DEFAULT_DB_PATH: ClassVar[Path] = Path("cache/feeds.sqlite")
    DEFAULT_SINCE: ClassVar[timedelta] = timedelta(days=7)
    DEFAULT_FEED_TTL: ClassVar[timedelta] = timedelta(minutes=30)
    DEFAULT_ARTICLE_TTL: ClassVar[timedelta] = timedelta(days=30)
    DEFAULT_CLEANUP_RETENTION: ClassVar[timedelta] = timedelta(days=7)
    DEFAULT_REPOSITORY: ClassVar[str] = "RoCry/feedforger"
    DEFAULT_BASE_URL: ClassVar[str] = (
        f"https://github.com/{DEFAULT_REPOSITORY}/releases"
    )
    DEFAULT_MAX_CONCURRENT: ClassVar[int] = 5
    DEFAULT_REQUEST_TIMEOUT: ClassVar[float] = 15.0
    DEFAULT_REQUEST_RETRIES: ClassVar[int] = 2

    recipes_path: Path = DEFAULT_RECIPES_PATH
    output_dir: Path = DEFAULT_OUTPUT_DIR
    report_path: Path = DEFAULT_REPORT_PATH
    db_path: Path = DEFAULT_DB_PATH
    since: timedelta = DEFAULT_SINCE
    feed_ttl: timedelta = DEFAULT_FEED_TTL
    article_ttl: timedelta = DEFAULT_ARTICLE_TTL
    cleanup_retention: timedelta = DEFAULT_CLEANUP_RETENTION
    base_url: str = DEFAULT_BASE_URL
    max_concurrent: int = DEFAULT_MAX_CONCURRENT
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT
    request_retries: int = DEFAULT_REQUEST_RETRIES

    def __post_init__(self) -> None:
        positive_windows = {
            "since": self.since,
            "feed_ttl": self.feed_ttl,
            "article_ttl": self.article_ttl,
        }
        for name, value in positive_windows.items():
            if value <= timedelta(0):
                raise ValueError(f"Settings {name} must be positive")
        if self.cleanup_retention < timedelta(0):
            raise ValueError("Settings cleanup_retention must not be negative")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("Settings base_url must be an HTTP(S) URL")
        if self.max_concurrent < 1:
            raise ValueError("Settings max_concurrent must be positive")
        if self.request_timeout <= 0:
            raise ValueError("Settings request_timeout must be positive")
        if self.request_retries < 0:
            raise ValueError("Settings request_retries must not be negative")

    @classmethod
    def from_sources(
        cls,
        *,
        env: Mapping[str, str],
        recipes_path: Path | None = None,
        output_dir: Path | None = None,
        report_path: Path | None = None,
        db_path: Path | None = None,
        since: timedelta | None = None,
        feed_ttl: timedelta | None = None,
        article_ttl: timedelta | None = None,
        cleanup_retention: timedelta | None = None,
        base_url: str | None = None,
        max_concurrent: int | None = None,
        request_timeout: float | None = None,
        request_retries: int | None = None,
    ) -> Settings:
        resolved_base_url = (
            base_url if base_url is not None else env.get("FEEDFORGER_BASE_URL")
        )
        if resolved_base_url is None:
            repository = env.get("GITHUB_REPOSITORY", cls.DEFAULT_REPOSITORY)
            resolved_base_url = f"https://github.com/{repository}/releases"

        return cls(
            recipes_path=(
                recipes_path if recipes_path is not None else cls.DEFAULT_RECIPES_PATH
            ),
            output_dir=(
                output_dir if output_dir is not None else cls.DEFAULT_OUTPUT_DIR
            ),
            report_path=(
                report_path if report_path is not None else cls.DEFAULT_REPORT_PATH
            ),
            db_path=db_path if db_path is not None else cls.DEFAULT_DB_PATH,
            since=since if since is not None else cls.DEFAULT_SINCE,
            feed_ttl=feed_ttl if feed_ttl is not None else cls.DEFAULT_FEED_TTL,
            article_ttl=(
                article_ttl if article_ttl is not None else cls.DEFAULT_ARTICLE_TTL
            ),
            cleanup_retention=(
                cleanup_retention
                if cleanup_retention is not None
                else cls.DEFAULT_CLEANUP_RETENTION
            ),
            base_url=resolved_base_url,
            max_concurrent=(
                max_concurrent
                if max_concurrent is not None
                else cls.DEFAULT_MAX_CONCURRENT
            ),
            request_timeout=(
                request_timeout
                if request_timeout is not None
                else cls.DEFAULT_REQUEST_TIMEOUT
            ),
            request_retries=(
                request_retries
                if request_retries is not None
                else cls.DEFAULT_REQUEST_RETRIES
            ),
        )
