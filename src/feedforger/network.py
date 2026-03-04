from __future__ import annotations

import asyncio

import httpx

from feedforger.log import logger

USER_AGENT = "FeedForger/1.0 (+https://github.com/RoCry/feedforger)"


class FeedFetcher:
    def __init__(self, max_concurrent: int = 5, timeout: float = 15.0):
        self.client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            max_redirects=3,
            headers={"User-Agent": USER_AGENT},
        )
        self.semaphore = asyncio.Semaphore(max_concurrent)

    async def __aenter__(self) -> FeedFetcher:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        await self.client.aclose()

    async def fetch_url(
        self, url: str, retries: int = 2
    ) -> tuple[str, str | None, str | None]:
        async with self.semaphore:
            last_error = ""
            for attempt in range(1 + retries):
                try:
                    response = await self.client.get(url)
                    response.raise_for_status()
                    return url, response.text, None
                except httpx.TimeoutException as e:
                    last_error = f"Timeout: {e}"
                except httpx.HTTPStatusError as e:
                    if e.response.status_code < 500:
                        return url, None, f"HTTP {e.response.status_code}"
                    last_error = f"HTTP {e.response.status_code}"
                except httpx.RequestError as e:
                    last_error = f"{type(e).__name__}: {e}"

                if attempt < retries:
                    delay = (attempt + 1) * 1.0
                    logger.debug(f"Retrying {url} in {delay}s (attempt {attempt + 1})")
                    await asyncio.sleep(delay)

            logger.error(f"Failed to fetch '{url}': {last_error}")
            return url, None, last_error

    async def fetch_urls(
        self, feed_name: str, urls: list[str]
    ) -> list[tuple[str, str | None, str | None]]:
        total = len(urls)
        completed = 0
        results = []
        tasks = [self.fetch_url(url) for url in urls]
        for coro in asyncio.as_completed(tasks):
            completed += 1
            result = await coro
            results.append(result)
            logger.info(f"{feed_name} {completed}/{total}")
        return results
