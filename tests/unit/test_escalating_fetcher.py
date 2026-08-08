"""Tests for `pipeline.fetchers.escalating.EscalatingFetcher` — auto-escalation from http to
browser when a response looks empty/JS-shell (FR-2, task 2.3).

Uses simple fake fetchers (matching the `fetch(target, *, rate_limit) -> RawResponse` shape both
`HttpFetcher` and `BrowserFetcher` implement) rather than real ones, so these tests exercise only
`EscalatingFetcher`'s own decision logic — laziness, classification-driven escalation, and
counting — independent of the underlying fetchers' own correctness (covered elsewhere).
"""

from datetime import UTC, datetime

from pipeline.core.models import RateLimitConfig, RawResponse, Target
from pipeline.fetchers.escalating import EscalatingFetcher

_RATE_LIMIT = RateLimitConfig(rps=10.0, burst=5)


def _raw(body: str, *, status_code: int = 200) -> RawResponse:
    return RawResponse(
        url="https://example.invalid/page",
        status_code=status_code,
        headers={},
        body=body.encode("utf-8"),
        fetched_at=datetime.now(UTC),
        content_type="text/html",
    )


_OK_BODY = (
    "<html><body><article><h1>Title</h1><p>"
    + "Real content. " * 10
    + "</p></article></body></html>"
)
_JS_SHELL_BODY = '<html><body><div id="root"></div></body></html>'
_SOFT_BLOCK_BODY = "<html><body><h1>Just a moment...</h1></body></html>"


class _FakeFetcher:
    def __init__(self, response: RawResponse) -> None:
        self.response = response
        self.calls: list[Target] = []

    async def fetch(self, target: Target, *, rate_limit: RateLimitConfig) -> RawResponse:
        self.calls.append(target)
        return self.response


class _BrowserProvider:
    """Tracks how many times it was actually invoked, returning the same fake fetcher."""

    def __init__(self, browser_fetcher: _FakeFetcher) -> None:
        self._browser_fetcher = browser_fetcher
        self.call_count = 0

    async def __call__(self) -> _FakeFetcher:
        self.call_count += 1
        return self._browser_fetcher


async def test_returns_the_http_result_directly_when_content_is_ok() -> None:
    http_fetcher = _FakeFetcher(_raw(_OK_BODY))
    browser_fetcher = _FakeFetcher(_raw("should never be used"))
    provider = _BrowserProvider(browser_fetcher)
    fetcher = EscalatingFetcher(http_fetcher, provider)

    raw = await fetcher.fetch(Target(url="https://example.invalid/page"), rate_limit=_RATE_LIMIT)

    assert raw.body.decode() == _OK_BODY
    assert provider.call_count == 0
    assert fetcher.http_only_count == 1
    assert fetcher.escalated_count == 0


async def test_escalates_to_browser_when_the_response_is_an_empty_js_shell() -> None:
    http_fetcher = _FakeFetcher(_raw(_JS_SHELL_BODY))
    browser_fetcher = _FakeFetcher(_raw(_OK_BODY))
    provider = _BrowserProvider(browser_fetcher)
    fetcher = EscalatingFetcher(http_fetcher, provider)

    raw = await fetcher.fetch(Target(url="https://example.invalid/page"), rate_limit=_RATE_LIMIT)

    assert raw.body.decode() == _OK_BODY
    assert provider.call_count == 1
    assert len(browser_fetcher.calls) == 1
    assert fetcher.http_only_count == 0
    assert fetcher.escalated_count == 1


async def test_does_not_escalate_a_soft_block() -> None:
    http_fetcher = _FakeFetcher(_raw(_SOFT_BLOCK_BODY))
    browser_fetcher = _FakeFetcher(_raw("should never be used"))
    provider = _BrowserProvider(browser_fetcher)
    fetcher = EscalatingFetcher(http_fetcher, provider)

    raw = await fetcher.fetch(Target(url="https://example.invalid/page"), rate_limit=_RATE_LIMIT)

    assert raw.body.decode() == _SOFT_BLOCK_BODY
    assert provider.call_count == 0
    assert fetcher.http_only_count == 1
    assert fetcher.escalated_count == 0


async def test_browser_fetcher_provider_is_invoked_at_most_once() -> None:
    http_fetcher = _FakeFetcher(_raw(_JS_SHELL_BODY))
    browser_fetcher = _FakeFetcher(_raw(_OK_BODY))
    provider = _BrowserProvider(browser_fetcher)
    fetcher = EscalatingFetcher(http_fetcher, provider)
    target = Target(url="https://example.invalid/page")

    await fetcher.fetch(target, rate_limit=_RATE_LIMIT)
    await fetcher.fetch(target, rate_limit=_RATE_LIMIT)
    await fetcher.fetch(target, rate_limit=_RATE_LIMIT)

    assert provider.call_count == 1
    assert fetcher.escalated_count == 3


async def test_the_provider_is_never_called_when_nothing_ever_escalates() -> None:
    http_fetcher = _FakeFetcher(_raw(_OK_BODY))
    browser_fetcher = _FakeFetcher(_raw("should never be used"))
    provider = _BrowserProvider(browser_fetcher)
    fetcher = EscalatingFetcher(http_fetcher, provider)
    target = Target(url="https://example.invalid/page")

    for _ in range(5):
        await fetcher.fetch(target, rate_limit=_RATE_LIMIT)

    assert provider.call_count == 0
    assert fetcher.http_only_count == 5
    assert fetcher.escalated_count == 0
