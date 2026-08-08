"""Auto-escalates http fetches to browser when the response looks empty/JS-shell (FR-2).

Tries the http fetcher first — cheap, fast, the default for most sites. If the classifier detects
`EMPTY_OR_JS_SHELL`, escalates to a lazily-created browser fetcher and returns that result
instead. A soft block is not escalated: switching fetchers does not fix an anti-bot challenge, so
retrying one would just waste a browser launch on a problem escalation can't solve.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from pipeline.core.models import RateLimitConfig, RawResponse, Target
from pipeline.fetchers.classifier import ResponseClassification, classify_response


class Fetcher(Protocol):
    """The shape both `HttpFetcher` and `BrowserFetcher` implement."""

    async def fetch(self, target: Target, *, rate_limit: RateLimitConfig) -> RawResponse: ...


class EscalatingFetcher:
    """Fetches via `http_fetcher`, escalating to a lazily-created browser fetcher on demand.

    `browser_fetcher_provider` is only ever called on the *first* actual escalation, so a run
    that never needs a browser never pays for launching one — the whole point of FR-2.
    `http_only_count`/`escalated_count` record the savings this made possible.
    """

    def __init__(
        self,
        http_fetcher: Fetcher,
        browser_fetcher_provider: Callable[[], Awaitable[Fetcher]],
    ) -> None:
        self._http_fetcher = http_fetcher
        self._browser_fetcher_provider = browser_fetcher_provider
        self._browser_fetcher: Fetcher | None = None
        self.http_only_count = 0
        self.escalated_count = 0

    async def fetch(self, target: Target, *, rate_limit: RateLimitConfig) -> RawResponse:
        """Fetch `target.url`, escalating to a browser if the http response is an empty/JS-shell
        page.

        Any exception from `http_fetcher.fetch` (a real HTTP failure, a robots disallow, an open
        circuit) propagates as-is — escalation only ever responds to a *successful* fetch whose
        content just isn't useful, never to a failure.
        """
        raw = await self._http_fetcher.fetch(target, rate_limit=rate_limit)
        if classify_response(raw) != ResponseClassification.EMPTY_OR_JS_SHELL:
            self.http_only_count += 1
            return raw

        browser_fetcher = await self._get_browser_fetcher()
        escalated_raw = await browser_fetcher.fetch(target, rate_limit=rate_limit)
        self.escalated_count += 1
        return escalated_raw

    async def _get_browser_fetcher(self) -> Fetcher:
        if self._browser_fetcher is None:
            self._browser_fetcher = await self._browser_fetcher_provider()
        return self._browser_fetcher
