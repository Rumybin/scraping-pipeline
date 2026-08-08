"""Tests for `pipeline.scrapers.books_sandbox.BooksScraper` (Phase 1.4)."""

from datetime import UTC, datetime

import pytest

from pipeline.core.config import Backend
from pipeline.core.context import RunContext
from pipeline.core.exceptions import ParsingError
from pipeline.core.models import RawResponse, ScrapedRecord
from pipeline.scrapers.books_sandbox import BooksScraper

_ARTICLE_TEMPLATE = """
<article class="product_pod">
    <div class="image_container">
        <a href="{href}"><img src="../media/cache/thumb.jpg" alt="{title}" class="thumbnail"></a>
    </div>
    <p class="star-rating {rating}"><i class="icon-star"></i></p>
    <h3><a href="{href}" title="{title}">{truncated_title}</a></h3>
    <div class="product_price">
        <p class="price_color">{price}</p>
        <p class="instock availability"><i class="icon-ok"></i> {availability} </p>
    </div>
</article>
"""


def _page(*articles: str) -> bytes:
    return f"<html><body>{''.join(articles)}</body></html>".encode()


def _good_article(
    title: str = "A Light in the Attic",
    href: str = "a-light-in-the-attic_1000/index.html",
    price: str = "£51.77",
    availability: str = "In stock",
    rating: str = "Three",
) -> str:
    return _ARTICLE_TEMPLATE.format(
        href=href,
        title=title,
        truncated_title=title[:15],
        price=price,
        availability=availability,
        rating=rating,
    )


def _make_context() -> RunContext:
    return RunContext(
        run_id="run-1",
        site_id="books_sandbox",
        backend=Backend.LOCAL,
        scraper_version="deadbeef",
        started_at=datetime.now(UTC),
    )


def _make_raw(body: bytes, *, status_code: int = 200) -> RawResponse:
    return RawResponse(
        url="https://books.toscrape.com/catalogue/page-1.html",
        status_code=status_code,
        headers={},
        body=body,
        fetched_at=datetime.now(UTC),
        content_type="text/html",
    )


async def test_discover_yields_all_fifty_catalogue_pages() -> None:
    scraper = BooksScraper()
    ctx = _make_context()

    targets = [target async for target in scraper.discover(ctx)]

    assert len(targets) == 50
    assert targets[0].url == "https://books.toscrape.com/catalogue/page-1.html"
    assert targets[-1].url == "https://books.toscrape.com/catalogue/page-50.html"


async def test_parse_extracts_expected_fields_from_one_article() -> None:
    scraper = BooksScraper()
    ctx = _make_context()
    raw = _make_raw(_page(_good_article()))

    records = await scraper.parse(raw, ctx)

    assert len(records) == 1
    record = records[0]
    assert isinstance(record, ScrapedRecord)
    assert record.title == "A Light in the Attic"
    assert str(record.source_url) == (
        "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
    )
    assert record.price is not None
    assert str(record.price) == "51.77"
    assert record.currency == "GBP"
    assert record.availability == "In stock"
    assert record.attributes["rating"] == 3
    assert record.completeness_score == 1.0
    assert record.extraction_method == "css"
    assert record.fetch_strategy == "http"
    assert scraper.quarantined == []


async def test_parse_quarantines_article_missing_price_and_keeps_the_rest() -> None:
    scraper = BooksScraper()
    ctx = _make_context()
    broken_article = _ARTICLE_TEMPLATE.replace('<p class="price_color">{price}</p>', "").format(
        href="broken-book/index.html",
        title="Broken Book",
        truncated_title="Broken",
        availability="In stock",
        rating="One",
    )
    raw = _make_raw(_page(broken_article, _good_article()))

    records = await scraper.parse(raw, ctx)

    assert len(records) == 1
    kept = records[0]
    assert isinstance(kept, ScrapedRecord)
    assert kept.title == "A Light in the Attic"
    assert len(scraper.quarantined) == 1
    rejection = scraper.quarantined[0]
    assert "price" in rejection.reason
    assert rejection.site_id == "books_sandbox"
    assert rejection.run_id == "run-1"


async def test_parse_raises_parsing_error_on_non_200_status() -> None:
    scraper = BooksScraper()
    ctx = _make_context()
    raw = _make_raw(b"<html></html>", status_code=503)

    with pytest.raises(ParsingError, match="unexpected status 503"):
        await scraper.parse(raw, ctx)


async def test_parse_raises_parsing_error_when_no_listings_found() -> None:
    scraper = BooksScraper()
    ctx = _make_context()
    raw = _make_raw(b"<html><body><p>no books here</p></body></html>")

    with pytest.raises(ParsingError, match="no product listings found"):
        await scraper.parse(raw, ctx)
