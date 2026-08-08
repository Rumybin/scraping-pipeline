"""Tests for `pipeline.scrapers.wikipedia_tech.WikipediaScraper` (Phase 2.7)."""

from datetime import UTC, datetime

import pytest

from pipeline.core.config import Backend
from pipeline.core.context import RunContext
from pipeline.core.exceptions import ParsingError
from pipeline.core.models import RawResponse, ScrapedRecord
from pipeline.scrapers.wikipedia_tech import WikipediaScraper

_GOOD_ARTICLE = """
<html><body>
<h1 id="firstHeading"><span><span class="mw-page-title-main">Example Topic</span></span></h1>
<div id="mw-content-text">
<div class="shortdescription">A concise topic description</div>
<p class="mw-empty-elt"></p>
<p>Example topic is a <a href="/wiki/Thing">thing</a> that people write about
at considerable length , with citations [38] and more citations [12] sprinkled throughout
the prose for good measure.</p>
</div>
</body></html>
"""

_NO_LEAD_PARAGRAPH_ARTICLE = """
<html><body>
<h1 id="firstHeading">Stub Topic</h1>
<div id="mw-content-text"><p>Too short.</p></div>
</body></html>
"""


def _make_context() -> RunContext:
    return RunContext(
        run_id="run-1",
        site_id="wikipedia_tech",
        backend=Backend.LOCAL,
        scraper_version="deadbeef",
        started_at=datetime.now(UTC),
    )


def _make_raw(body: str, *, status_code: int = 200) -> RawResponse:
    return RawResponse(
        url="https://en.wikipedia.org/wiki/Example_Topic",
        status_code=status_code,
        headers={},
        body=body.encode("utf-8"),
        fetched_at=datetime.now(UTC),
        content_type="text/html",
    )


async def test_discover_yields_one_target_per_curated_title() -> None:
    scraper = WikipediaScraper()
    ctx = _make_context()

    targets = [target async for target in scraper.discover(ctx)]

    assert len(targets) >= 25  # a generous floor, not the exact curated count
    assert all(t.url.startswith("https://en.wikipedia.org/wiki/") for t in targets)
    assert any("Python" in t.url for t in targets)


async def test_parse_extracts_title_short_description_and_cleaned_summary() -> None:
    scraper = WikipediaScraper()
    ctx = _make_context()
    raw = _make_raw(_GOOD_ARTICLE)

    records = await scraper.parse(raw, ctx)

    assert len(records) == 1
    record = records[0]
    assert isinstance(record, ScrapedRecord)
    assert record.title == "Example Topic"
    assert record.attributes["short_description"] == "A concise topic description"
    summary = record.attributes["summary"]
    assert "[38]" not in summary
    assert "[12]" not in summary
    assert "length ," not in summary  # space-before-comma artifact must be cleaned
    assert "length," in summary
    assert record.completeness_score == 1.0
    assert scraper.quarantined == []


async def test_parse_quarantines_a_stub_with_no_substantial_lead_paragraph() -> None:
    scraper = WikipediaScraper()
    ctx = _make_context()
    raw = _make_raw(_NO_LEAD_PARAGRAPH_ARTICLE)

    records = await scraper.parse(raw, ctx)

    assert records == []
    assert len(scraper.quarantined) == 1
    assert "lead paragraph" in scraper.quarantined[0].reason


async def test_parse_raises_parsing_error_on_non_200_status() -> None:
    scraper = WikipediaScraper()
    ctx = _make_context()
    raw = _make_raw("<html></html>", status_code=503)

    with pytest.raises(ParsingError, match="unexpected status 503"):
        await scraper.parse(raw, ctx)


async def test_parse_raises_parsing_error_when_no_heading_found() -> None:
    scraper = WikipediaScraper()
    ctx = _make_context()
    raw = _make_raw("<html><body><p>no heading here</p></body></html>")

    with pytest.raises(ParsingError, match="no article heading found"):
        await scraper.parse(raw, ctx)
