import asyncio
from typing import Optional, List, Dict
import httpx

from utils import logger


class FeedFetcher:
    def __init__(
        self, 
        max_concurrent: int = 5,
        timeout: float = 15.0,
    ):
        self.client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            max_redirects=3,
        )
        self.semaphore = asyncio.Semaphore(max_concurrent)

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()

    async def fetch_url(self, url: str) -> tuple[str, Optional[str], Optional[str]]:
        """
        Fetch content from URL with concurrency control.
        Returns: (url, content, error_message)
        """
        async with self.semaphore:  # Limit concurrent requests
            try:
                logger.info(f"Fetching feed from '{url}'")
                response = await self.client.get(url)
                response.raise_for_status()
                return url, response.text, None
            except Exception as e:
                error_msg = f"{type(e).__name__}: {str(e)}"
                logger.error(f"Failed to fetch {url}: {error_msg}")
                return url, None, error_msg

    async def fetch_urls(self, urls: List[str]) -> List[tuple[str, Optional[str], Optional[str]]]:
        """Fetch multiple URLs concurrently."""
        tasks = [self.fetch_url(url) for url in urls]
        return await asyncio.gather(*tasks) 
