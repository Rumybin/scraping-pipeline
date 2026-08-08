"""Tests for `pipeline.fetchers.browser.BrowserFetcher`.

Runs a real Chromium instance (installed via `playwright install chromium`) against the real
hostile server, over a real loopback socket — mocking Playwright's API would not actually prove
this fetcher renders JavaScript or captures XHR traffic, which is the entire point of it existing
instead of `HttpFetcher`. One browser is launched per test function, matching pytest-asyncio's
default per-test event loop scope; slower than sharing a browser, but avoids fighting fixture
event-loop lifetime mismatches for a session/module-scoped instance.
"""

from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from playwright.async_api import Browser, async_playwright
from tests.resilience.conftest import hostile_server

from pipeline.core.exceptions import BrowserFetchError, CircuitOpenError, RobotsDisallowedError
from pipeline.core.models import RateLimitConfig, Target
from pipeline.fetchers.browser import BrowserFetcher
from pipeline.fetchers.circuit_breaker import DomainCircuitBreaker
from pipeline.fetchers.robots import RobotsChecker

_USER_AGENT = "browser-fetcher-test/1.0"
_GENEROUS_RATE_LIMIT = RateLimitConfig(rps=1000.0, burst=1000)


async def _no_sleep(seconds: float) -> None:
    """A `retry_sleep` override that skips real backoff waits between retry attempts."""


@pytest.fixture
async def browser() -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        instance = await playwright.chromium.launch()
        yield instance
        await instance.close()


def _permissive_robots_checker() -> RobotsChecker:
    client = httpx.AsyncClient()
    return RobotsChecker(client, _USER_AGENT)


async def test_fetch_returns_the_rendered_html_of_a_static_page(browser: Browser) -> None:
    async with hostile_server() as base_url:
        fetcher = BrowserFetcher(
            browser=browser,
            user_agent=_USER_AGENT,
            robots_checker=_permissive_robots_checker(),
        )

        raw = await fetcher.fetch(
            Target(url=f"{base_url}/challenge"), rate_limit=_GENEROUS_RATE_LIMIT
        )

    assert raw.status_code == 200
    assert "Checking your browser" in raw.body.decode("utf-8")


async def test_fetch_waits_for_javascript_to_render_content(browser: Browser) -> None:
    async with hostile_server() as base_url:
        fetcher = BrowserFetcher(
            browser=browser,
            user_agent=_USER_AGENT,
            robots_checker=_permissive_robots_checker(),
        )

        raw = await fetcher.fetch(
            Target(url=f"{base_url}/js-rendered"), rate_limit=_GENEROUS_RATE_LIMIT
        )

    body = raw.body.decode("utf-8")
    assert "rendered by JS" in body
    assert "loading..." not in body


async def test_fetch_captures_xhr_responses_made_during_page_load(browser: Browser) -> None:
    async with hostile_server() as base_url:
        fetcher = BrowserFetcher(
            browser=browser,
            user_agent=_USER_AGENT,
            robots_checker=_permissive_robots_checker(),
        )

        await fetcher.fetch(Target(url=f"{base_url}/js-rendered"), rate_limit=_GENEROUS_RATE_LIMIT)

    captured = fetcher.last_xhr_responses
    assert len(captured) == 1
    assert captured[0].url.endswith("/strict-rate-limit")
    assert captured[0].status_code == 200


async def test_fetch_scrolls_to_reveal_all_infinite_scroll_items(browser: Browser) -> None:
    async with hostile_server() as base_url:
        fetcher = BrowserFetcher(
            browser=browser,
            user_agent=_USER_AGENT,
            robots_checker=_permissive_robots_checker(),
        )

        raw = await fetcher.fetch(
            Target(url=f"{base_url}/infinite-scroll", max_scroll_rounds=10),
            rate_limit=_GENEROUS_RATE_LIMIT,
        )

    body = raw.body.decode("utf-8")
    for n in range(5):
        assert f"item-{n}" in body


async def test_fetch_does_not_scroll_when_max_scroll_rounds_is_zero(browser: Browser) -> None:
    async with hostile_server() as base_url:
        fetcher = BrowserFetcher(
            browser=browser,
            user_agent=_USER_AGENT,
            robots_checker=_permissive_robots_checker(),
        )

        raw = await fetcher.fetch(
            Target(url=f"{base_url}/infinite-scroll"), rate_limit=_GENEROUS_RATE_LIMIT
        )

    body = raw.body.decode("utf-8")
    assert "item-0" in body
    assert "item-1" not in body


async def test_context_pool_reuses_contexts_instead_of_growing_past_its_size(
    browser: Browser,
) -> None:
    async with hostile_server() as base_url:
        fetcher = BrowserFetcher(
            browser=browser,
            user_agent=_USER_AGENT,
            robots_checker=_permissive_robots_checker(),
            context_pool_size=1,
        )
        target = Target(url=f"{base_url}/challenge")

        await fetcher.fetch(target, rate_limit=_GENEROUS_RATE_LIMIT)
        await fetcher.fetch(target, rate_limit=_GENEROUS_RATE_LIMIT)

    assert len(browser.contexts) == 1


@respx.mock
async def test_fetch_raises_when_robots_disallows(browser: Browser) -> None:
    respx.get("https://example.invalid/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /blocked/\n")
    )
    fetcher = BrowserFetcher(
        browser=browser,
        user_agent=_USER_AGENT,
        robots_checker=_permissive_robots_checker(),
    )

    with pytest.raises(RobotsDisallowedError):
        await fetcher.fetch(
            Target(url="https://example.invalid/blocked/page"),
            rate_limit=_GENEROUS_RATE_LIMIT,
        )


@respx.mock
async def test_fetch_raises_browser_fetch_error_on_navigation_failure(browser: Browser) -> None:
    # `.invalid` is a reserved TLD guaranteed never to resolve (RFC 2606) -- robots.txt itself is
    # mocked permissive via respx (httpx traffic), but Playwright's own navigation to the same
    # host is real and will fail at DNS resolution, which is exactly the failure under test.
    respx.get("https://example.invalid/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\n")
    )
    fetcher = BrowserFetcher(
        browser=browser,
        user_agent=_USER_AGENT,
        robots_checker=_permissive_robots_checker(),
        retry_sleep=_no_sleep,
    )

    with pytest.raises(BrowserFetchError):
        await fetcher.fetch(
            Target(url="https://example.invalid/unreachable"), rate_limit=_GENEROUS_RATE_LIMIT
        )


async def test_fetch_raises_circuit_open_error_without_launching_a_page(browser: Browser) -> None:
    async with hostile_server() as base_url:
        breaker = DomainCircuitBreaker(fail_max=1, reset_timeout=60.0)
        port = base_url.rsplit(":", 1)[1]
        domain = f"127.0.0.1:{port}"
        breaker.record_failure(domain)
        fetcher = BrowserFetcher(
            browser=browser,
            user_agent=_USER_AGENT,
            robots_checker=_permissive_robots_checker(),
            circuit_breaker=breaker,
            retry_sleep=_no_sleep,
        )

        with pytest.raises(CircuitOpenError):
            await fetcher.fetch(
                Target(url=f"{base_url}/challenge"), rate_limit=_GENEROUS_RATE_LIMIT
            )
