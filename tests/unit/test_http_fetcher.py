"""Tests for `pipeline.fetchers.http.HttpFetcher`.

Composes `RobotsChecker` and `RateLimiter` (real instances, since their own behavior is already
covered in `test_robots.py` / `test_rate_limiter.py`) except where a test needs to inspect what
`HttpFetcher` hands the rate limiter, where an `AsyncMock` stands in instead. Retry/circuit-breaker
tests pass `retry_sleep` as a recording no-op so they don't wait out real backoff delays; end-to-end
proof against real wall-clock retry timing lives in `tests/resilience/`.
"""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from pipeline.core.exceptions import CircuitOpenError, HttpFetchError, RobotsDisallowedError
from pipeline.core.models import RateLimitConfig, Target
from pipeline.fetchers.circuit_breaker import DomainCircuitBreaker
from pipeline.fetchers.http import HttpFetcher

USER_AGENT = "scraping-pipeline-test/1.0"
PERMISSIVE_ROBOTS = "User-agent: *\n"
CRAWL_DELAY_ROBOTS = "User-agent: *\nCrawl-delay: 10\n"
DISALLOW_ROBOTS = "User-agent: *\nDisallow: /blocked/\n"


async def _no_sleep(seconds: float) -> None:
    """A `retry_sleep` override that records nothing and waits nothing — keeps retry tests fast."""


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


def _mock_robots(text: str = PERMISSIVE_ROBOTS) -> None:
    respx.get("https://example.invalid/robots.txt").mock(
        return_value=httpx.Response(200, text=text)
    )


@respx.mock
async def test_fetch_returns_raw_response_on_success(client: httpx.AsyncClient) -> None:
    _mock_robots()
    respx.get("https://example.invalid/page").mock(
        return_value=httpx.Response(200, text="hello", headers={"content-type": "text/html"})
    )
    fetcher = HttpFetcher(user_agent=USER_AGENT, client=client)

    raw = await fetcher.fetch(
        Target(url="https://example.invalid/page"), rate_limit=RateLimitConfig(rps=10.0, burst=5)
    )

    assert raw.url == "https://example.invalid/page"
    assert raw.status_code == 200
    assert raw.body == b"hello"
    assert raw.content_type == "text/html"


@respx.mock
async def test_fetch_returns_raw_response_for_non_2xx_status(client: httpx.AsyncClient) -> None:
    _mock_robots()
    respx.get("https://example.invalid/missing").mock(return_value=httpx.Response(404))
    fetcher = HttpFetcher(user_agent=USER_AGENT, client=client)

    raw = await fetcher.fetch(
        Target(url="https://example.invalid/missing"),
        rate_limit=RateLimitConfig(rps=10.0, burst=5),
    )

    assert raw.status_code == 404


@respx.mock
async def test_fetch_raises_when_robots_disallows(client: httpx.AsyncClient) -> None:
    _mock_robots(DISALLOW_ROBOTS)
    page_route = respx.get("https://example.invalid/blocked/page").mock(
        return_value=httpx.Response(200)
    )
    fetcher = HttpFetcher(user_agent=USER_AGENT, client=client)

    with pytest.raises(RobotsDisallowedError):
        await fetcher.fetch(
            Target(url="https://example.invalid/blocked/page"),
            rate_limit=RateLimitConfig(rps=10.0, burst=5),
        )

    assert page_route.call_count == 0


@respx.mock
async def test_fetch_raises_http_fetch_error_on_network_failure(client: httpx.AsyncClient) -> None:
    _mock_robots()
    respx.get("https://example.invalid/page").mock(side_effect=httpx.ConnectError("boom"))
    fetcher = HttpFetcher(user_agent=USER_AGENT, client=client)

    with pytest.raises(HttpFetchError):
        await fetcher.fetch(
            Target(url="https://example.invalid/page"),
            rate_limit=RateLimitConfig(rps=10.0, burst=5),
        )


@respx.mock
async def test_fetch_acquires_the_rate_limiter_for_the_target_domain(
    client: httpx.AsyncClient,
) -> None:
    _mock_robots()
    respx.get("https://example.invalid/page").mock(return_value=httpx.Response(200))
    mock_limiter = AsyncMock()
    fetcher = HttpFetcher(user_agent=USER_AGENT, client=client, rate_limiter=mock_limiter)

    await fetcher.fetch(
        Target(url="https://example.invalid/page"), rate_limit=RateLimitConfig(rps=10.0, burst=5)
    )

    mock_limiter.acquire.assert_awaited_once()
    domain, config = mock_limiter.acquire.await_args.args
    assert domain == "example.invalid"
    assert config.rps == 10.0


@respx.mock
async def test_fetch_narrows_rate_to_crawl_delay_when_it_is_slower(
    client: httpx.AsyncClient,
) -> None:
    _mock_robots(CRAWL_DELAY_ROBOTS)  # Crawl-delay: 10 => 0.1 rps
    respx.get("https://example.invalid/page").mock(return_value=httpx.Response(200))
    mock_limiter = AsyncMock()
    fetcher = HttpFetcher(user_agent=USER_AGENT, client=client, rate_limiter=mock_limiter)

    await fetcher.fetch(
        Target(url="https://example.invalid/page"),
        rate_limit=RateLimitConfig(rps=5.0, burst=5, respect_crawl_delay=True),
    )

    _, config = mock_limiter.acquire.await_args.args
    assert config.rps == pytest.approx(0.1)


@respx.mock
async def test_fetch_ignores_crawl_delay_when_respect_crawl_delay_is_false(
    client: httpx.AsyncClient,
) -> None:
    _mock_robots(CRAWL_DELAY_ROBOTS)
    respx.get("https://example.invalid/page").mock(return_value=httpx.Response(200))
    mock_limiter = AsyncMock()
    fetcher = HttpFetcher(user_agent=USER_AGENT, client=client, rate_limiter=mock_limiter)

    await fetcher.fetch(
        Target(url="https://example.invalid/page"),
        rate_limit=RateLimitConfig(rps=5.0, burst=5, respect_crawl_delay=False),
    )

    _, config = mock_limiter.acquire.await_args.args
    assert config.rps == 5.0


@respx.mock
async def test_fetch_keeps_configured_rps_when_already_slower_than_crawl_delay(
    client: httpx.AsyncClient,
) -> None:
    _mock_robots("User-agent: *\nCrawl-delay: 1\n")  # 1 rps
    respx.get("https://example.invalid/page").mock(return_value=httpx.Response(200))
    mock_limiter = AsyncMock()
    fetcher = HttpFetcher(user_agent=USER_AGENT, client=client, rate_limiter=mock_limiter)

    await fetcher.fetch(
        Target(url="https://example.invalid/page"),
        rate_limit=RateLimitConfig(rps=0.5, burst=5, respect_crawl_delay=True),
    )

    _, config = mock_limiter.acquire.await_args.args
    assert config.rps == 0.5


async def test_aclose_closes_the_underlying_client() -> None:
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    fetcher = HttpFetcher(user_agent=USER_AGENT, client=mock_client)

    await fetcher.aclose()

    mock_client.aclose.assert_awaited_once()


@respx.mock
async def test_fetch_retries_a_503_and_returns_the_eventual_success(
    client: httpx.AsyncClient,
) -> None:
    _mock_robots()
    route = respx.get("https://example.invalid/page")
    route.side_effect = [httpx.Response(503), httpx.Response(200, text="ok")]
    fetcher = HttpFetcher(user_agent=USER_AGENT, client=client, retry_sleep=_no_sleep)

    raw = await fetcher.fetch(
        Target(url="https://example.invalid/page"), rate_limit=RateLimitConfig(rps=10.0, burst=5)
    )

    assert raw.status_code == 200
    assert route.call_count == 2


@respx.mock
async def test_fetch_does_not_retry_an_ordinary_4xx(client: httpx.AsyncClient) -> None:
    _mock_robots()
    route = respx.get("https://example.invalid/missing").mock(return_value=httpx.Response(404))
    fetcher = HttpFetcher(user_agent=USER_AGENT, client=client, retry_sleep=_no_sleep)

    raw = await fetcher.fetch(
        Target(url="https://example.invalid/missing"),
        rate_limit=RateLimitConfig(rps=10.0, burst=5),
    )

    assert raw.status_code == 404
    assert route.call_count == 1


@respx.mock
async def test_fetch_raises_circuit_open_error_without_touching_the_network(
    client: httpx.AsyncClient,
) -> None:
    _mock_robots()
    route = respx.get("https://example.invalid/page").mock(side_effect=httpx.ConnectError("down"))
    breaker = DomainCircuitBreaker(fail_max=2, reset_timeout=60.0)
    fetcher = HttpFetcher(
        user_agent=USER_AGENT, client=client, circuit_breaker=breaker, retry_sleep=_no_sleep
    )
    target = Target(url="https://example.invalid/page")
    rate_limit = RateLimitConfig(rps=10.0, burst=5)

    with pytest.raises(HttpFetchError):
        await fetcher.fetch(target, rate_limit=rate_limit)
    with pytest.raises(HttpFetchError):
        await fetcher.fetch(target, rate_limit=rate_limit)

    calls_before = route.call_count
    with pytest.raises(CircuitOpenError):
        await fetcher.fetch(target, rate_limit=rate_limit)

    assert route.call_count == calls_before  # the third fetch never touched the network


@respx.mock
async def test_fetch_does_not_count_a_robots_disallow_against_the_circuit_breaker(
    client: httpx.AsyncClient,
) -> None:
    _mock_robots(DISALLOW_ROBOTS)
    page_route = respx.get("https://example.invalid/blocked/page").mock(
        return_value=httpx.Response(200)
    )
    breaker = DomainCircuitBreaker(fail_max=1, reset_timeout=60.0)
    fetcher = HttpFetcher(
        user_agent=USER_AGENT, client=client, circuit_breaker=breaker, retry_sleep=_no_sleep
    )
    target = Target(url="https://example.invalid/blocked/page")
    rate_limit = RateLimitConfig(rps=10.0, burst=5)

    for _ in range(3):
        with pytest.raises(RobotsDisallowedError):
            await fetcher.fetch(target, rate_limit=rate_limit)

    assert page_route.call_count == 0
    assert breaker.is_open("example.invalid") is False
