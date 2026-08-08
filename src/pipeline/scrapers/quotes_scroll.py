"""T0 sandbox scraper for the infinite-scroll `quotes.toscrape.com/scroll` (PRD §2.4, Phase 2.2).

Approved in `docs/compliance.md` — same operator and setup as `books.toscrape.com`, no
`robots.txt`. `strategy = "browser"` is mandatory: the page starts with an empty `.quotes`
container and loads quotes only in response to real scroll position (via scroll-triggered calls
to `/api/quotes`), which a plain HTTP fetch can neither trigger nor observe.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal

from pydantic import BaseModel
from selectolax.parser import HTMLParser

from pipeline.core.context import RunContext
from pipeline.core.exceptions import ParsingError, RecordValidationError
from pipeline.core.models import (
    QuarantinedRecord,
    RateLimitConfig,
    RawResponse,
    ScrapedRecord,
    Target,
)
from pipeline.core.scraper import BaseScraper
from pipeline.scrapers._quotes_common import build_quote_record

_SCROLL_URL = "https://quotes.toscrape.com/scroll"
_MAX_SCROLL_ROUNDS = 15
"""The site serves exactly 100 quotes across 10 scroll-triggered AJAX loads (confirmed directly:
`/api/quotes?page=10` returns `has_next: false`). 15 gives a safety margin above that; the scroll
loop in `BrowserFetcher` already stops early once a scroll stops growing the page, so a higher
cap here costs nothing when the real content runs out sooner."""


class QuotesScrollScraper(BaseScraper):
    """Scrapes the single infinite-scroll quote page at quotes.toscrape.com/scroll."""

    site_id = "quotes_scroll"
    strategy: Literal["http", "browser"] = "browser"
    rate_limit = RateLimitConfig(rps=2.0, burst=5, respect_crawl_delay=True)
    record_model = ScrapedRecord

    async def discover(self, ctx: RunContext) -> AsyncIterator[Target]:
        """Yield the single scroll page, with scrolling enabled to load every quote."""
        yield Target(url=_SCROLL_URL, max_scroll_rounds=_MAX_SCROLL_ROUNDS)

    async def parse(self, raw: RawResponse, ctx: RunContext) -> list[BaseModel]:
        """Extract one `ScrapedRecord` per quote rendered after scrolling `raw`'s page.

        Raises `ParsingError` if `raw` is not a successful response or contains no rendered
        quotes at all. A single malformed quote does not fail the page — it is appended to
        `self.quarantined` with a reason and parsing continues (Hard Rule 6).
        """
        if raw.status_code != 200:
            raise ParsingError(f"unexpected status {raw.status_code} fetching {raw.url}")

        tree = HTMLParser(raw.body)
        quotes = tree.css("div.quote")
        if not quotes:
            raise ParsingError(f"no rendered quotes found on {raw.url}")

        records: list[BaseModel] = []
        for quote in quotes:
            try:
                records.append(build_quote_record(quote, site_id=self.site_id, raw=raw, ctx=ctx))
            except RecordValidationError as exc:
                self.quarantined.append(
                    QuarantinedRecord(
                        site_id=self.site_id,
                        source_url=raw.url,
                        run_id=ctx.run_id,
                        quarantined_at=raw.fetched_at,
                        reason=str(exc),
                        payload={"quote_html": quote.html or ""},
                    )
                )
        return records
