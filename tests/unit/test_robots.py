"""Tests for `pipeline.fetchers.robots.RobotsChecker`.

Hard Rule 3 (CLAUDE.md §2): robots.txt, including `Crawl-delay`, is always respected. Ambiguous
fetch outcomes (server error, network failure) must fail safe toward *disallow*, not toward
*allow* — the opposite of the convention used for a genuinely missing file (404).
"""

import httpx
import pytest
import respx

from pipeline.fetchers.robots import RobotsChecker

USER_AGENT = "scraping-pipeline-test/1.0"

ROBOTS_WITH_CRAWL_DELAY = """\
User-agent: *
Disallow: /private/
Crawl-delay: 5
"""

ROBOTS_WITHOUT_CRAWL_DELAY = """\
User-agent: *
Disallow: /private/
"""


@pytest.fixture
async def client() -> httpx.AsyncClient:
    async with httpx.AsyncClient() as client:
        yield client


@respx.mock
async def test_allows_a_path_not_covered_by_disallow(client: httpx.AsyncClient) -> None:
    respx.get("https://example.invalid/robots.txt").mock(
        return_value=httpx.Response(200, text=ROBOTS_WITH_CRAWL_DELAY)
    )
    checker = RobotsChecker(client, USER_AGENT)

    assert await checker.is_allowed("https://example.invalid/public/page") is True


@respx.mock
async def test_disallows_a_path_covered_by_disallow(client: httpx.AsyncClient) -> None:
    respx.get("https://example.invalid/robots.txt").mock(
        return_value=httpx.Response(200, text=ROBOTS_WITH_CRAWL_DELAY)
    )
    checker = RobotsChecker(client, USER_AGENT)

    assert await checker.is_allowed("https://example.invalid/private/page") is False


@respx.mock
async def test_crawl_delay_returns_the_declared_value(client: httpx.AsyncClient) -> None:
    respx.get("https://example.invalid/robots.txt").mock(
        return_value=httpx.Response(200, text=ROBOTS_WITH_CRAWL_DELAY)
    )
    checker = RobotsChecker(client, USER_AGENT)

    assert await checker.crawl_delay("https://example.invalid/public/page") == 5.0


@respx.mock
async def test_crawl_delay_is_none_when_not_declared(client: httpx.AsyncClient) -> None:
    respx.get("https://example.invalid/robots.txt").mock(
        return_value=httpx.Response(200, text=ROBOTS_WITHOUT_CRAWL_DELAY)
    )
    checker = RobotsChecker(client, USER_AGENT)

    assert await checker.crawl_delay("https://example.invalid/public/page") is None


@respx.mock
async def test_missing_robots_txt_404_is_fully_permissive(client: httpx.AsyncClient) -> None:
    respx.get("https://example.invalid/robots.txt").mock(return_value=httpx.Response(404))
    checker = RobotsChecker(client, USER_AGENT)

    assert await checker.is_allowed("https://example.invalid/anything") is True


@respx.mock
async def test_other_4xx_is_fully_permissive(client: httpx.AsyncClient) -> None:
    respx.get("https://example.invalid/robots.txt").mock(return_value=httpx.Response(403))
    checker = RobotsChecker(client, USER_AGENT)

    assert await checker.is_allowed("https://example.invalid/anything") is True


@respx.mock
async def test_server_error_fails_safe_to_disallow(client: httpx.AsyncClient) -> None:
    respx.get("https://example.invalid/robots.txt").mock(return_value=httpx.Response(500))
    checker = RobotsChecker(client, USER_AGENT)

    assert await checker.is_allowed("https://example.invalid/anything") is False


@respx.mock
async def test_network_error_fails_safe_to_disallow(client: httpx.AsyncClient) -> None:
    respx.get("https://example.invalid/robots.txt").mock(side_effect=httpx.ConnectError("boom"))
    checker = RobotsChecker(client, USER_AGENT)

    assert await checker.is_allowed("https://example.invalid/anything") is False


@respx.mock
async def test_robots_txt_is_fetched_once_per_origin(client: httpx.AsyncClient) -> None:
    route = respx.get("https://example.invalid/robots.txt").mock(
        return_value=httpx.Response(200, text=ROBOTS_WITH_CRAWL_DELAY)
    )
    checker = RobotsChecker(client, USER_AGENT)

    await checker.is_allowed("https://example.invalid/public/a")
    await checker.is_allowed("https://example.invalid/public/b")
    await checker.crawl_delay("https://example.invalid/public/c")

    assert route.call_count == 1
