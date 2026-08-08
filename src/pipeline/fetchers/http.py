"""HTTP fetching: composes robots.txt compliance, per-domain rate limiting, per-error-class
retry, and a per-domain circuit breaker around httpx (FR-1, FR-9, FR-10).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx

from pipeline.core.exceptions import CircuitOpenError, HttpFetchError, RobotsDisallowedError
from pipeline.core.models import RateLimitConfig, RawResponse, Target
from pipeline.fetchers.circuit_breaker import DomainCircuitBreaker
from pipeline.fetchers.rate_limiter import RateLimiter
from pipeline.fetchers.retry import ErrorClass, classify, fetch_with_retry
from pipeline.fetchers.robots import RobotsChecker

_BREAKER_FAILURE_CLASSES = frozenset({ErrorClass.RATE_LIMITED, ErrorClass.SERVER_ERROR})


class HttpFetcher:
    """Fetches pages over plain HTTP (FR-1), enforcing robots.txt, a per-domain rate limit,
    per-error-class retry with backoff (FR-9), and a per-domain circuit breaker (FR-10).
    """

    def __init__(
        self,
        *,
        user_agent: str,
        client: httpx.AsyncClient,
        rate_limiter: RateLimiter | None = None,
        robots_checker: RobotsChecker | None = None,
        circuit_breaker: DomainCircuitBreaker | None = None,
        retry_sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._user_agent = user_agent
        self._client = client
        self._rate_limiter = rate_limiter or RateLimiter()
        self._robots_checker = robots_checker or RobotsChecker(client, user_agent)
        self._circuit_breaker = circuit_breaker or DomainCircuitBreaker()
        self._retry_sleep = retry_sleep

    async def fetch(self, target: Target, *, rate_limit: RateLimitConfig) -> RawResponse:
        """Fetch `target.url`, honoring robots.txt, `rate_limit`, retry, and the circuit breaker.

        Raises `CircuitOpenError` immediately, without touching the network, if `target.url`'s
        domain has tripped its breaker. Raises `RobotsDisallowedError` if robots.txt disallows the
        URL — this does not count against the breaker, since it reflects site policy, not domain
        health. Raises `HttpFetchError` if every retry of a network/timeout failure is exhausted.
        A non-2xx HTTP response that survives retries is not an error here — it is returned as a
        `RawResponse` so it can still be persisted to the raw zone (Hard Rule 5) for inspection.
        """
        domain = urlsplit(target.url).netloc
        if self._circuit_breaker.is_open(domain):
            raise CircuitOpenError(f"circuit open for {domain!r}, skipping {target.url}")

        try:
            raw = await fetch_with_retry(
                lambda: self._fetch_once(target, rate_limit=rate_limit), sleep=self._retry_sleep
            )
        except HttpFetchError:
            self._circuit_breaker.record_failure(domain)
            raise

        if classify(raw, None) in _BREAKER_FAILURE_CLASSES:
            self._circuit_breaker.record_failure(domain)
        else:
            self._circuit_breaker.record_success(domain)
        return raw

    async def _fetch_once(self, target: Target, *, rate_limit: RateLimitConfig) -> RawResponse:
        """Perform exactly one fetch attempt, with no retry — `fetch` supplies that."""
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
