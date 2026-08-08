"""Tests for `pipeline.scrapers.quotes_js.QuotesJsScraper` (Phase 2.2)."""

from datetime import UTC, datetime

import pytest

from pipeline.core.config import Backend
from pipeline.core.context import RunContext
from pipeline.core.exceptions import ParsingError
from pipeline.core.models import RawResponse, ScrapedRecord
from pipeline.scrapers.quotes_js import QuotesJsScraper

# Mirrors the exact structure `document.write` produces on the real, rendered page.
_GOOD_QUOTE = (
    '<div class="quote"><span class="text">“The world as we have created it.”</span>'
    '<span>by <small class="author">Albert Einstein</small></span>'
    '<div class="tags">Tags: <a class="tag">change</a> <a class="tag">world</a></div></div>'
)
_NO_AUTHOR_QUOTE = (
    '<div class="quote"><span class="text">“Missing an author.”</span>'
    '<span>by <small class="author"></small></span>'
    '<div class="tags">Tags: <a class="tag">lonely</a></div></div>'
)


def _page(*quotes: str) -> bytes:
    return f"<html><body>{''.join(quotes)}</body></html>".encode()


def _make_context() -> RunContext:
    return RunContext(
        run_id="run-1",
        site_id="quotes_js",
        backend=Backend.LOCAL,
        scraper_version="deadbeef",
        started_at=datetime.now(UTC),
    )


def _make_raw(body: bytes, *, status_code: int = 200) -> RawResponse:
    return RawResponse(
        url="https://quotes.toscrape.com/js/page/1/",
        status_code=status_code,
        headers={},
        body=body,
        fetched_at=datetime.now(UTC),
        content_type="text/html",
    )


async def test_discover_yields_all_ten_pages() -> None:
    scraper = QuotesJsScraper()
    ctx = _make_context()

    targets = [target async for target in scraper.discover(ctx)]

    assert len(targets) == 10
    assert targets[0].url == "https://quotes.toscrape.com/js/page/1/"
    assert targets[-1].url == "https://quotes.toscrape.com/js/page/10/"


async def test_parse_extracts_text_author_and_tags() -> None:
    scraper = QuotesJsScraper()
    ctx = _make_context()
    raw = _make_raw(_page(_GOOD_QUOTE))

    records = await scraper.parse(raw, ctx)

    assert len(records) == 1
    record = records[0]
    assert isinstance(record, ScrapedRecord)
    assert record.title == "“The world as we have created it.”"
    assert record.attributes["author"] == "Albert Einstein"
    assert record.attributes["tags"] == ["change", "world"]
    assert record.fetch_strategy == "browser"
    assert record.price is None
    assert record.completeness_score == 1.0
    assert scraper.quarantined == []


async def test_parse_quarantines_a_quote_missing_its_author() -> None:
    scraper = QuotesJsScraper()
    ctx = _make_context()
    raw = _make_raw(_page(_NO_AUTHOR_QUOTE, _GOOD_QUOTE))

    records = await scraper.parse(raw, ctx)

    kept = [record for record in records if isinstance(record, ScrapedRecord)]
    assert [record.attributes["author"] for record in kept] == ["Albert Einstein"]
    assert len(scraper.quarantined) == 1
    assert "author" in scraper.quarantined[0].reason


async def test_parse_raises_parsing_error_on_non_200_status() -> None:
    scraper = QuotesJsScraper()
    ctx = _make_context()
    raw = _make_raw(b"<html></html>", status_code=503)

    with pytest.raises(ParsingError, match="unexpected status 503"):
        await scraper.parse(raw, ctx)


async def test_parse_raises_parsing_error_when_no_quotes_rendered() -> None:
    scraper = QuotesJsScraper()
    ctx = _make_context()
    raw = _make_raw(b"<html><body><script>var data = [];</script></body></html>")

    with pytest.raises(ParsingError, match="no rendered quotes found"):
        await scraper.parse(raw, ctx)
