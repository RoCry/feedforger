import asyncio
from typing import Optional, List, Dict, Any
import httpx

from utils import logger, extract_main_content


# network util, get content from urls with concurrency control
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
        await self.client.aclose()

    async def fetch_url(self, url: str) -> tuple[str, Optional[str], Optional[str]]:
        """
        Fetch content from URL with concurrency control.
        Returns: (url, content, error_message)
        """
        async with self.semaphore:  # Limit concurrent requests
            try:
                logger.debug(f"Fetching feed from '{url}'")
                response = await self.client.get(url)
                response.raise_for_status()
                return url, response.text, None
            except Exception as e:
                error_msg = f"{type(e).__name__}: {str(e)}"
                logger.error(f"Failed to fetch '{url}' {error_msg}")
                return url, None, error_msg

    # returns list of (url, content, error_message)
    async def fetch_urls(
        self, feed_name: str, urls: List[str]
    ) -> List[tuple[str, Optional[str], Optional[str]]]:
        """Fetch multiple URLs concurrently."""
        total = len(urls)
        completed = 0
        results = []

        # Create batches of tasks to show progress
        tasks = [self.fetch_url(url) for url in urls]
        for result in asyncio.as_completed(tasks):
            completed += 1
            result = await result
            results.append(result)
            logger.info(f"{feed_name} {completed}/{total}")

        return results

    async def fetch_item_content(
        self, url: str
    ) -> tuple[str, Optional[Dict[str, Any]], Optional[str]]:
        """
        Fetch and extract content from a feed item URL.
        Returns: (url, content_dict, error_message)

        content_dict includes:
            - content_html: Extracted main content HTML
            - content_text: Plain text version of content
            - title: Page title if available
        """
        async with self.semaphore:  # Limit concurrent requests
            try:
                logger.debug(f"Fetching item content from '{url}'")
                response = await self.client.get(url)
                response.raise_for_status()
                content = extract_main_content(response.text, url)
                return url, content, None
            except Exception as e:
                error_msg = f"{type(e).__name__}: {str(e)}"
                logger.error(f"Failed to fetch item content '{url}' {error_msg}")
                return url, None, error_msg

    async def fetch_items_content(
        self, feed_name: str, urls_to_fetch: List[str]
    ) -> Dict[str, tuple[Optional[Dict[str, Any]], Optional[str]]]:
        """
        Fetch content from multiple item URLs concurrently.
        Returns a dictionary mapping URLs to (content_dict, error_message)
        """
        total = len(urls_to_fetch)
        if not total:
            return {}

        completed = 0
        results = {}

        logger.info(f"{feed_name} fulfilling {total} items")
        tasks = [self.fetch_item_content(url) for url in urls_to_fetch]

        for result_future in asyncio.as_completed(tasks):
            url, content, error = await result_future
            completed += 1
            results[url] = (content, error)
            logger.info(f"{feed_name} fulfilled items {completed}/{total}")

        return results
