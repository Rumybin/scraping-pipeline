"""T0 sandbox scraper for the JS-rendered `quotes.toscrape.com/js/` (FR-5, PRD §2.4, Phase 2.2).

Approved in `docs/compliance.md` — same operator and setup as `books.toscrape.com`, no
`robots.txt`. `strategy = "browser"` is mandatory here, not a choice: the raw HTML response
contains zero `.quote` elements — every quote is produced by `document.write` from a JS array
embedded in a `<script>` tag, so a plain HTTP fetch would see none of them.
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

_CATALOGUE_BASE_URL = "https://quotes.toscrape.com/js/page/"
_TOTAL_CATALOGUE_PAGES = 10
"""Page 10 has 10 quotes and no "next" link; page 11+ each return 200 with zero quotes. Verified
directly (not guessed, per docs/compliance.md's audit method) -- this is a fixed, static demo
dataset (same operator/disclaimer as books.toscrape.com), so the count is stable."""


class QuotesJsScraper(BaseScraper):
    """Scrapes the full paginated, JS-rendered quote list at quotes.toscrape.com/js/."""

    site_id = "quotes_js"
    strategy: Literal["http", "browser"] = "browser"
    rate_limit = RateLimitConfig(rps=2.0, burst=5, respect_crawl_delay=True)
    record_model = ScrapedRecord

    async def discover(self, ctx: RunContext) -> AsyncIterator[Target]:
        """Yield the site's fixed 10 JS-rendered listing pages."""
        for page_number in range(1, _TOTAL_CATALOGUE_PAGES + 1):
            yield Target(url=f"{_CATALOGUE_BASE_URL}{page_number}/")

    async def parse(self, raw: RawResponse, ctx: RunContext) -> list[BaseModel]:
        """Extract one `ScrapedRecord` per quote rendered on `raw`'s listing page.

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
