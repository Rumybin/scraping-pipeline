"""Golden-dataset regression tests for `BooksScraper` (Phase 1.7, PRD §4.3).

`tests/fixtures/books_sandbox/pages/page-01.html` .. `page-20.html` are real catalogue pages
fetched once from books.toscrape.com and frozen on disk; the parser is tested against these
fixtures, never against the live site (`docs/adr/0004-hostile-test-harness.md`'s companion
principle for parsing: fixtures over live traffic keeps CI deterministic). The matching
`tests/fixtures/books_sandbox/expected/page-NN.json` files are the parser's own output at the
time the fixtures were captured — an unintended change to parsing or normalization changes this
snapshot and turns CI red, which is the whole point of a golden-dataset test.

`page-21-malformed.html` is hand-crafted (not fetched) to exercise the Hard-Rule-6 quarantine
path: one well-formed article plus one with its price stripped out.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipeline.core.config import Backend
from pipeline.core.context import RunContext
from pipeline.core.models import RawResponse, ScrapedRecord
from pipeline.scrapers.books_sandbox import BooksScraper

FIXTURES_ROOT = Path(__file__).parent.parent / "fixtures" / "books_sandbox"
PAGES_DIR = FIXTURES_ROOT / "pages"
EXPECTED_DIR = FIXTURES_ROOT / "expected"

_FIXED_TIMESTAMP = datetime(2026, 1, 1, tzinfo=UTC)
_GOLDEN_RUN_ID = "golden-fixture"
_GOLDEN_SCRAPER_VERSION = "golden"


def _make_context() -> RunContext:
    return RunContext(
        run_id=_GOLDEN_RUN_ID,
        site_id="books_sandbox",
        backend=Backend.LOCAL,
        scraper_version=_GOLDEN_SCRAPER_VERSION,
        started_at=_FIXED_TIMESTAMP,
    )


def _make_raw(page_number: int, html_path: Path) -> RawResponse:
    return RawResponse(
        url=f"https://books.toscrape.com/catalogue/page-{page_number}.html",
        status_code=200,
        headers={},
        body=html_path.read_bytes(),
        fetched_at=_FIXED_TIMESTAMP,
        content_type="text/html",
    )


@pytest.mark.parametrize("page_number", range(1, 21))
async def test_parse_matches_golden_snapshot(page_number: int) -> None:
    name = f"page-{page_number:02d}"
    raw = _make_raw(page_number, PAGES_DIR / f"{name}.html")
    expected = json.loads((EXPECTED_DIR / f"{name}.json").read_text(encoding="utf-8"))
    scraper = BooksScraper()

    records = await scraper.parse(raw, _make_context())

    actual = [json.loads(record.model_dump_json()) for record in records]
    assert actual == expected
    assert scraper.quarantined == []


async def test_parse_quarantines_the_malformed_fixture_and_keeps_the_valid_article() -> None:
    raw = _make_raw(21, PAGES_DIR / "page-21-malformed.html")
    scraper = BooksScraper()

    records = await scraper.parse(raw, _make_context())

    kept = [record for record in records if isinstance(record, ScrapedRecord)]
    assert [record.title for record in kept] == ["A Light in the Attic"]
    assert len(scraper.quarantined) == 1
    assert "price" in scraper.quarantined[0].reason


def test_golden_fixture_set_has_twenty_real_pages_and_one_malformed_page() -> None:
    real_pages = sorted(PAGES_DIR.glob("page-[0-9][0-9].html"))
    expected_snapshots = sorted(EXPECTED_DIR.glob("page-[0-9][0-9].json"))

    assert len(real_pages) == 20
    assert len(expected_snapshots) == 20
    assert (PAGES_DIR / "page-21-malformed.html").exists()
