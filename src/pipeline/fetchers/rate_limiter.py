"""Per-domain token-bucket rate limiting.

This is the plain, configured-rate limiter used by `HttpFetcher` (Phase 1). The AIMD-adaptive
limiter that backs off automatically on 429/403 is a distinct, later component (`docs/prd.md`
§4.2, `AdaptiveRateLimiter`).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from pipeline.core.models import RateLimitConfig


class TokenBucket:
    """Allows `capacity` immediate acquisitions, then paces further ones at `rate` per second."""

    def __init__(
        self, rate: float, capacity: int, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._rate = rate
        self._capacity = float(capacity)
        self._tokens = float(capacity)
        self._clock = clock
        self._updated_at = clock()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Block until a token is available, then consume one."""
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await asyncio.sleep((1 - self._tokens) / self._rate)

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._updated_at
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._updated_at = now


class RateLimiter:
    """Owns one `TokenBucket` per domain, created lazily on first use.

    A domain's bucket is sized from the `RateLimitConfig` passed on its *first* `acquire` call;
    later calls for the same domain reuse that bucket regardless of the config passed, since a
    domain's effective pacing should stay stable for the life of a run rather than jitter between
    requests.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, domain: str, config: RateLimitConfig) -> None:
        """Block until `domain` is allowed to make one more request under `config`."""
        bucket = await self._get_bucket(domain, config)
        await bucket.acquire()

    async def _get_bucket(self, domain: str, config: RateLimitConfig) -> TokenBucket:
        async with self._lock:
            if domain not in self._buckets:
                self._buckets[domain] = TokenBucket(
                    rate=config.rps, capacity=config.burst, clock=self._clock
                )
            return self._buckets[domain]
