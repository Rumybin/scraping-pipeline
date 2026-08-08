"""Tests for `pipeline.scrapers.quotes_scroll.QuotesScrollScraper` (Phase 2.2)."""

from datetime import UTC, datetime

import pytest

from pipeline.core.config import Backend
from pipeline.core.context import RunContext
from pipeline.core.exceptions import ParsingError
from pipeline.core.models import RawResponse, ScrapedRecord
from pipeline.scrapers.quotes_scroll import QuotesScrollScraper

_GOOD_QUOTE = (
    '<div class="quote"><span class="text">“Scroll on, my friend.”</span>'
    '<span>by <small class="author">Ada Lovelace</small></span>'
    '<div class="tags">Tags: <a class="tag">computing</a></div></div>'
)


def _page(*quotes: str) -> bytes:
    return f"<html><body><div class='quotes'>{''.join(quotes)}</div></body></html>".encode()


def _make_context() -> RunContext:
    return RunContext(
        run_id="run-1",
        site_id="quotes_scroll",
        backend=Backend.LOCAL,
        scraper_version="deadbeef",
        started_at=datetime.now(UTC),
    )


def _make_raw(body: bytes, *, status_code: int = 200) -> RawResponse:
    return RawResponse(
        url="https://quotes.toscrape.com/scroll",
        status_code=status_code,
        headers={},
        body=body,
        fetched_at=datetime.now(UTC),
        content_type="text/html",
    )


async def test_discover_yields_a_single_target_with_scroll_enabled() -> None:
    scraper = QuotesScrollScraper()
    ctx = _make_context()

    targets = [target async for target in scraper.discover(ctx)]

    assert len(targets) == 1
    assert targets[0].url == "https://quotes.toscrape.com/scroll"
    assert targets[0].max_scroll_rounds > 0


async def test_parse_extracts_quotes_after_scrolling() -> None:
    scraper = QuotesScrollScraper()
    ctx = _make_context()
    raw = _make_raw(_page(_GOOD_QUOTE))

    records = await scraper.parse(raw, ctx)

    assert len(records) == 1
    record = records[0]
    assert isinstance(record, ScrapedRecord)
    assert record.title == "“Scroll on, my friend.”"
    assert record.attributes["author"] == "Ada Lovelace"
    assert scraper.quarantined == []


async def test_parse_raises_parsing_error_on_non_200_status() -> None:
    scraper = QuotesScrollScraper()
    ctx = _make_context()
    raw = _make_raw(b"<html></html>", status_code=503)

    with pytest.raises(ParsingError, match="unexpected status 503"):
        await scraper.parse(raw, ctx)


async def test_parse_raises_parsing_error_when_no_quotes_rendered() -> None:
    scraper = QuotesScrollScraper()
    ctx = _make_context()
    raw = _make_raw(b"<html><body><div class='quotes'></div></body></html>")

    with pytest.raises(ParsingError, match="no rendered quotes found"):
        await scraper.parse(raw, ctx)
