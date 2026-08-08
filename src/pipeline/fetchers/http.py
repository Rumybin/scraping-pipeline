"""HTTP fetching: composes robots.txt compliance and per-domain rate limiting around httpx."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx

from pipeline.core.exceptions import HttpFetchError, RobotsDisallowedError
from pipeline.core.models import RateLimitConfig, RawResponse, Target
from pipeline.fetchers.rate_limiter import RateLimiter
from pipeline.fetchers.robots import RobotsChecker


class HttpFetcher:
    """Fetches pages over plain HTTP (FR-1), enforcing robots.txt and a per-domain rate limit."""

    def __init__(
        self,
        *,
        user_agent: str,
        client: httpx.AsyncClient,
        rate_limiter: RateLimiter | None = None,
        robots_checker: RobotsChecker | None = None,
    ) -> None:
        self._user_agent = user_agent
        self._client = client
        self._rate_limiter = rate_limiter or RateLimiter()
        self._robots_checker = robots_checker or RobotsChecker(client, user_agent)

    async def fetch(self, target: Target, *, rate_limit: RateLimitConfig) -> RawResponse:
        """Fetch `target.url`, honoring robots.txt and `rate_limit`.

        Raises `RobotsDisallowedError` if robots.txt disallows the URL, and `HttpFetchError` on a
        network-level failure. A non-2xx HTTP response is not an error here — it is returned as a
        `RawResponse` so it can still be persisted to the raw zone (Hard Rule 5) for inspection.
        """
        if not await self._robots_checker.is_allowed(target.url):
            raise RobotsDisallowedError(f"robots.txt disallows {target.url}")

        effective_rate_limit = await self._effective_rate_limit(target.url, rate_limit)
        domain = urlsplit(target.url).netloc
        await self._rate_limiter.acquire(domain, effective_rate_limit)

        try:
            response = await self._client.get(target.url, headers={"User-Agent": self._user_agent})
        except httpx.HTTPError as exc:
            raise HttpFetchError(f"failed to fetch {target.url}: {exc}") from exc

        return RawResponse(
            url=target.url,
            status_code=response.status_code,
            headers=dict(response.headers),
            body=response.content,
            fetched_at=datetime.now(UTC),
            content_type=response.headers.get("content-type"),
        )

    async def _effective_rate_limit(self, url: str, config: RateLimitConfig) -> RateLimitConfig:
        """Narrow `config.rps` to the origin's robots.txt `Crawl-delay`, when it is more strict."""
        if not config.respect_crawl_delay:
            return config
        crawl_delay = await self._robots_checker.crawl_delay(url)
        if not crawl_delay or crawl_delay <= 0:
            return config
        crawl_delay_rps = 1.0 / crawl_delay
        if crawl_delay_rps >= config.rps:
            return config
        return config.model_copy(update={"rps": crawl_delay_rps})

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
