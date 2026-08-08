"""Browser-driven fetching via Playwright (FR-1), for pages that need JS execution to render.

Sits behind the exact same per-domain rate limiter, retry policy, and circuit breaker as
`HttpFetcher` (`fetchers/resilience.py`, ADR 0003) — escalating from `http` to `browser` changes
how a page is retrieved, not whether the pipeline's politeness and resilience guarantees apply.
robots.txt itself is still checked over plain HTTP via the same `RobotsChecker` `HttpFetcher`
uses, since reading a text file does not need JS execution.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

from playwright.async_api import Browser, BrowserContext
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Response as PlaywrightResponse

from pipeline.core.exceptions import BrowserFetchError, RobotsDisallowedError
from pipeline.core.models import RateLimitConfig, RawResponse, Target
from pipeline.fetchers.circuit_breaker import DomainCircuitBreaker
from pipeline.fetchers.rate_limiter import RateLimiter
from pipeline.fetchers.resilience import fetch_with_resilience
from pipeline.fetchers.robots import RobotsChecker

_DEFAULT_CONTEXT_POOL_SIZE = 4
_DEFAULT_NAVIGATION_TIMEOUT_MS = 30_000.0
_XHR_RESOURCE_TYPES = frozenset({"xhr", "fetch"})


@dataclass(frozen=True)
class XhrCapture:
    """One XHR/`fetch()` response observed while a page loaded."""

    url: str
    status_code: int
    content_type: str | None
    body: bytes


class _ContextPool:
    """Reuses a bounded set of browser contexts instead of creating a new one per fetch."""

    def __init__(self, browser: Browser, *, size: int, user_agent: str) -> None:
        self._browser = browser
        self._size = size
        self._user_agent = user_agent
        self._available: asyncio.Queue[BrowserContext] = asyncio.Queue()
        self._created = 0
        self._lock = asyncio.Lock()

    async def acquire(self) -> BrowserContext:
        """Return an idle context, create a new one under `size`, or wait for one to free up."""
        try:
            return self._available.get_nowait()
        except asyncio.QueueEmpty:
            pass
        async with self._lock:
            if self._created < self._size:
                context = await self._browser.new_context(user_agent=self._user_agent)
                self._created += 1
                return context
        return await self._available.get()

    async def release(self, context: BrowserContext) -> None:
        """Return `context` to the pool for reuse by the next fetch."""
        await self._available.put(context)

    async def close(self) -> None:
        """Close every pooled context. Does not close the underlying `Browser`."""
        while not self._available.empty():
            context = self._available.get_nowait()
            await context.close()


class BrowserFetcher:
    """Fetches pages via a pooled Playwright browser context (FR-1), for JS-rendered content.

    `last_xhr_responses` holds whatever XHR/`fetch()` traffic the most recent `fetch()` call
    observed while its page loaded — replaced (not accumulated) on every call.
    """

    def __init__(
        self,
        *,
        browser: Browser,
        user_agent: str,
        robots_checker: RobotsChecker,
        rate_limiter: RateLimiter | None = None,
        circuit_breaker: DomainCircuitBreaker | None = None,
        retry_sleep: Callable[[float], Awaitable[None]] | None = None,
        context_pool_size: int = _DEFAULT_CONTEXT_POOL_SIZE,
        navigation_timeout_ms: float = _DEFAULT_NAVIGATION_TIMEOUT_MS,
    ) -> None:
        self._robots_checker = robots_checker
        self._rate_limiter = rate_limiter or RateLimiter()
        self._circuit_breaker = circuit_breaker or DomainCircuitBreaker()
        self._retry_sleep = retry_sleep
        self._navigation_timeout_ms = navigation_timeout_ms
        self._context_pool = _ContextPool(browser, size=context_pool_size, user_agent=user_agent)
        self.last_xhr_responses: list[XhrCapture] = []

    async def fetch(self, target: Target, *, rate_limit: RateLimitConfig) -> RawResponse:
        """Fetch `target.url` with a real browser, honoring robots.txt, `rate_limit`, retry, and
        the circuit breaker — the same contract as `HttpFetcher.fetch` (ADR 0003).

        Raises `CircuitOpenError` immediately, without launching a page, if `target.url`'s domain
        has tripped its breaker. Raises `RobotsDisallowedError` if robots.txt disallows the URL.
        Raises `BrowserFetchError` if every retry of a navigation failure is exhausted.
        """
        domain = urlsplit(target.url).netloc
        return await fetch_with_resilience(
            domain,
            lambda: self._fetch_once(target, rate_limit=rate_limit),
            circuit_breaker=self._circuit_breaker,
            retry_sleep=self._retry_sleep,
        )

    async def _fetch_once(self, target: Target, *, rate_limit: RateLimitConfig) -> RawResponse:
        """Perform exactly one fetch attempt, with no retry — `fetch` supplies that."""
        if not await self._robots_checker.is_allowed(target.url):
            raise RobotsDisallowedError(f"robots.txt disallows {target.url}")

        effective_rate_limit = await self._effective_rate_limit(target.url, rate_limit)
        domain = urlsplit(target.url).netloc
        await self._rate_limiter.acquire(domain, effective_rate_limit)

        context = await self._context_pool.acquire()
        try:
            return await self._navigate(context, target.url)
        finally:
            await self._context_pool.release(context)

    async def _navigate(self, context: BrowserContext, url: str) -> RawResponse:
        page = await context.new_page()
        xhr_captures: list[XhrCapture] = []
        capture_tasks: list[asyncio.Task[None]] = []

        async def _capture(response: PlaywrightResponse) -> None:
            if response.request.resource_type not in _XHR_RESOURCE_TYPES:
                return
            try:
                body = await response.body()
            except PlaywrightError:
                return  # body unavailable (aborted/redirected/etc.) -- not this fetch's concern
            xhr_captures.append(
                XhrCapture(
                    url=response.url,
                    status_code=response.status,
                    content_type=response.headers.get("content-type"),
                    body=body,
                )
            )

        page.on(
            "response",
            lambda response: capture_tasks.append(asyncio.ensure_future(_capture(response))),
        )

        try:
            try:
                response = await page.goto(
                    url, wait_until="networkidle", timeout=self._navigation_timeout_ms
                )
            except PlaywrightError as exc:
                raise BrowserFetchError(f"failed to fetch {url}: {exc}") from exc
            if response is None:
                raise BrowserFetchError(f"no response received navigating to {url}")

            if capture_tasks:
                await asyncio.gather(*capture_tasks, return_exceptions=True)
            self.last_xhr_responses = xhr_captures

            content = await page.content()
            return RawResponse(
                url=url,
                status_code=response.status,
                headers=response.headers,
                body=content.encode("utf-8"),
                fetched_at=datetime.now(UTC),
                content_type=response.headers.get("content-type"),
            )
        finally:
            await page.close()

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
        """Close every pooled browser context. Does not close the underlying `Browser`."""
        await self._context_pool.close()
