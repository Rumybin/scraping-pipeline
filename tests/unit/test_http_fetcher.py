"""Tests for `pipeline.fetchers.http.HttpFetcher`.

Composes `RobotsChecker` and `RateLimiter` (real instances, since their own behavior is already
covered in `test_robots.py` / `test_rate_limiter.py`) except where a test needs to inspect what
`HttpFetcher` hands the rate limiter, where an `AsyncMock` stands in instead.
"""

from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from pipeline.core.exceptions import HttpFetchError, RobotsDisallowedError
from pipeline.core.models import RateLimitConfig, Target
from pipeline.fetchers.http import HttpFetcher

USER_AGENT = "scraping-pipeline-test/1.0"
PERMISSIVE_ROBOTS = "User-agent: *\n"
CRAWL_DELAY_ROBOTS = "User-agent: *\nCrawl-delay: 10\n"
DISALLOW_ROBOTS = "User-agent: *\nDisallow: /blocked/\n"


@pytest.fixture
async def client() -> httpx.AsyncClient:
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
