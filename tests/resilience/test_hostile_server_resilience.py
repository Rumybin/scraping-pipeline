"""Resilience integration tests: the real `HttpFetcher` (retry + circuit breaker) driven against
the real hostile test server, over a real loopback socket — never against a real third party
(Hard Rule 4, `docs/adr/0004-hostile-test-harness.md`).

This is the literal Phase 2.5 deliverable: retry/backoff and the per-domain circuit breaker,
*proven* against the hostile server built in 2.4, not just unit-tested against fakes.
"""

from __future__ import annotations

import time

import httpx
import pytest

from pipeline.core.exceptions import CircuitOpenError, HttpFetchError
from pipeline.core.models import RateLimitConfig, Target
from pipeline.fetchers.circuit_breaker import DomainCircuitBreaker
from pipeline.fetchers.http import HttpFetcher
from tests.resilience.conftest import hostile_server

_USER_AGENT = "resilience-test/1.0"
_GENEROUS_RATE_LIMIT = RateLimitConfig(rps=1000.0, burst=1000)


async def _no_sleep(seconds: float) -> None:
    """A `retry_sleep` override that skips real backoff waits between retry attempts."""


async def test_retry_honors_the_retry_after_header_against_a_real_server() -> None:
    """Scenario 1 (`/retry-after`): three real attempts, two real ~1s waits, still 429 at the
    end — proving the wait duration itself (not just the retry count) comes from the server's
    own header, over a real connection.
    """
    async with hostile_server() as base_url, httpx.AsyncClient(timeout=5.0) as client:
        fetcher = HttpFetcher(user_agent=_USER_AGENT, client=client)
        started = time.monotonic()

        raw = await fetcher.fetch(
            Target(url=f"{base_url}/retry-after?seconds=1"), rate_limit=_GENEROUS_RATE_LIMIT
        )

        elapsed = time.monotonic() - started

    assert raw.status_code == 429
    assert elapsed >= 1.8  # 2 real Retry-After waits of ~1s (rate-limited retry budget is 3)


async def test_retry_recovers_from_a_transient_flaky_failure() -> None:
    """Scenario 2 (`/flaky`): `flaky_seed=1` deterministically fails the first call and succeeds
    the second, so a `fetch()` call should come back `200`, not `503`.
    """
    async with (
        hostile_server(flaky_seed=1) as base_url,
        httpx.AsyncClient(timeout=5.0) as client,
    ):
        fetcher = HttpFetcher(user_agent=_USER_AGENT, client=client, retry_sleep=_no_sleep)

        raw = await fetcher.fetch(Target(url=f"{base_url}/flaky"), rate_limit=_GENEROUS_RATE_LIMIT)

    assert raw.status_code == 200


async def test_timeout_is_retried_and_eventually_raises_http_fetch_error() -> None:
    """Scenario 3 (`/timeout`): a real client-side read timeout, over a real connection —
    `ASGITransport` cannot exercise this, since it never applies httpx's timeout machinery.
    """
    async with (
        hostile_server() as base_url,
        httpx.AsyncClient(timeout=0.2) as client,
    ):
        fetcher = HttpFetcher(user_agent=_USER_AGENT, client=client, retry_sleep=_no_sleep)

        with pytest.raises(HttpFetchError):
            await fetcher.fetch(
                Target(url=f"{base_url}/timeout?delay=5"), rate_limit=_GENEROUS_RATE_LIMIT
            )


async def test_circuit_breaker_opens_after_consecutive_failures_against_a_dead_endpoint() -> None:
    """`/always-down`: two straight failed `fetch()` calls trip a `fail_max=2` breaker; the third
    is rejected locally as `CircuitOpenError` instead of making another round trip.
    """
    breaker = DomainCircuitBreaker(fail_max=2, reset_timeout=60.0)
    async with hostile_server() as base_url, httpx.AsyncClient(timeout=5.0) as client:
        fetcher = HttpFetcher(
            user_agent=_USER_AGENT, client=client, circuit_breaker=breaker, retry_sleep=_no_sleep
        )
        target = Target(url=f"{base_url}/always-down")

        for _ in range(2):
            raw = await fetcher.fetch(target, rate_limit=_GENEROUS_RATE_LIMIT)
            assert raw.status_code == 503

        with pytest.raises(CircuitOpenError):
            await fetcher.fetch(target, rate_limit=_GENEROUS_RATE_LIMIT)


async def test_circuit_breaker_is_isolated_per_domain_against_a_real_server() -> None:
    """The same real server reached via two different hostnames (`127.0.0.1` / `localhost`)
    proves the breaker keys on domain, not on "the target is unhealthy" globally: tripping one
    leaves fetches to the other domain unaffected.
    """
    breaker = DomainCircuitBreaker(fail_max=2, reset_timeout=60.0)
    async with hostile_server() as base_url, httpx.AsyncClient(timeout=5.0) as client:
        fetcher = HttpFetcher(
            user_agent=_USER_AGENT, client=client, circuit_breaker=breaker, retry_sleep=_no_sleep
        )
        port = base_url.rsplit(":", 1)[1]
        down_target = Target(url=f"http://127.0.0.1:{port}/always-down")
        other_host_target = Target(url=f"http://localhost:{port}/always-down")

        for _ in range(2):
            await fetcher.fetch(down_target, rate_limit=_GENEROUS_RATE_LIMIT)
        with pytest.raises(CircuitOpenError):
            await fetcher.fetch(down_target, rate_limit=_GENEROUS_RATE_LIMIT)

        raw = await fetcher.fetch(other_host_target, rate_limit=_GENEROUS_RATE_LIMIT)

    assert raw.status_code == 503  # still reachable — its breaker was never tripped
