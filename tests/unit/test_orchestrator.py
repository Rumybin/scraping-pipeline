"""Tests for `pipeline.orchestrator.run` — the Phase 1 single-site orchestrator."""

import re
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import polars as pl
import pytest
import respx

from pipeline.core.config import Backend, Settings
from pipeline.core.exceptions import ConfigurationError
from pipeline.core.models import RateLimitConfig, ScrapedRecord, Target
from pipeline.core.sites import load_sites_config
from pipeline.fetchers.browser import BrowserFetcher
from pipeline.fetchers.escalating import EscalatingFetcher
from pipeline.orchestrator.run import (
    RunResult,
    _build_fetcher,
    _dedupe,
    _load_scraper_class,
    _raw_key,
    _records_to_parquet_bytes,
    _resolve_site,
)
from pipeline.scrapers.books_sandbox import BooksScraper

FIXTURES = Path(__file__).parent.parent / "fixtures"
SITES_VALID = FIXTURES / "sites_valid.yaml"

_ROBOTS_PERMISSIVE = httpx.Response(404)


class _FakeStateStore:
    """Minimal in-memory double for the `seen`/`mark_seen` slice of `StateStore`."""

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self.marked: list[tuple[str, str]] = []

    async def seen(self, content_hash: str) -> bool:
        return content_hash in self._seen

    async def mark_seen(self, content_hash: str, site_id: str, ttl_days: int) -> None:
        self._seen.add(content_hash)
        self.marked.append((content_hash, site_id))


def _record(record_id: str, content_hash: str) -> ScrapedRecord:
    return ScrapedRecord(
        record_id=record_id,
        site_id="books_sandbox",
        source_url="https://books.toscrape.com/catalogue/a_1/index.html",  # type: ignore[arg-type]  # pydantic HttpUrl coerces from str
        content_hash=content_hash,
        run_id="run-1",
        scraped_at=datetime.now(UTC),
        scraper_version="deadbeef",
        fetch_strategy="http",
        backend=Backend.LOCAL,
        title="A Light in the Attic",
        price=Decimal("51.77"),
        currency="GBP",
        availability="In stock",
        attributes={},
        completeness_score=1.0,
        extraction_method="css",
    )


def _page_response(request: httpx.Request) -> httpx.Response:
    match = re.search(r"page-(\d+)\.html", str(request.url))
    assert match is not None
    page_number = match.group(1)
    articles = "".join(
        f"""
        <article class="product_pod">
            <div class="image_container">
                <a href="book-{page_number}-{n}/index.html">
                    <img src="../media/cache/thumb.jpg" alt="Book" class="thumbnail">
                </a>
            </div>
            <p class="star-rating Three"><i class="icon-star"></i></p>
            <h3><a href="book-{page_number}-{n}/index.html"
                   title="Book {page_number}-{n}">Book</a></h3>
            <div class="product_price">
                <p class="price_color">£{10 + n}.00</p>
                <p class="instock availability"><i class="icon-ok"></i> In stock </p>
            </div>
        </article>
        """
        for n in range(2)
    )
    return httpx.Response(200, text=f"<html><body>{articles}</body></html>")


class TestRawKey:
    def test_raw_key_includes_partitioning_and_a_stable_url_hash(self) -> None:
        key_a = _raw_key("books_sandbox", "2026-08-08", "run-1", "https://example.invalid/x")
        key_b = _raw_key("books_sandbox", "2026-08-08", "run-1", "https://example.invalid/x")
        key_c = _raw_key("books_sandbox", "2026-08-08", "run-1", "https://example.invalid/y")

        assert key_a == key_b
        assert key_a != key_c
        assert key_a.startswith("raw/site=books_sandbox/dt=2026-08-08/run=run-1/")
        assert key_a.endswith(".html.gz")


class TestRecordsToParquetBytes:
    def test_round_trips_row_count_and_non_temporal_columns(self) -> None:
        records = [_record("r1", "h1"), _record("r2", "h2")]

        parquet_bytes = _records_to_parquet_bytes(records)

        frame = pl.read_parquet(parquet_bytes)
        assert frame.height == 2
        assert frame["title"].to_list() == ["A Light in the Attic", "A Light in the Attic"]
        assert frame["price"].to_list() == [51.77, 51.77]
        assert frame["content_hash"].to_list() == ["h1", "h2"]

    def test_empty_records_produce_a_zero_row_parquet_file(self) -> None:
        parquet_bytes = _records_to_parquet_bytes([])

        frame = pl.read_parquet(parquet_bytes)
        assert frame.height == 0


class TestResolveSite:
    def test_returns_the_matching_enabled_site(self) -> None:
        config = load_sites_config(SITES_VALID)

        site = _resolve_site(config, "books_sandbox")

        assert site.id == "books_sandbox"

    def test_raises_configuration_error_for_unregistered_site(self) -> None:
        config = load_sites_config(SITES_VALID)

        with pytest.raises(ConfigurationError, match="not registered"):
            _resolve_site(config, "does_not_exist")

    def test_raises_configuration_error_for_disabled_site(self) -> None:
        config = load_sites_config(SITES_VALID)
        config.sites[0].enabled = False

        with pytest.raises(ConfigurationError, match="disabled"):
            _resolve_site(config, "books_sandbox")


class TestLoadScraperClass:
    def test_resolves_a_real_scraper_class(self) -> None:
        scraper_class = _load_scraper_class("scrapers.books_sandbox:BooksScraper")

        assert scraper_class is BooksScraper

    def test_raises_configuration_error_for_malformed_spec(self) -> None:
        with pytest.raises(ConfigurationError, match="invalid scraper module spec"):
            _load_scraper_class("scrapers.books_sandbox")

    def test_raises_configuration_error_for_missing_module(self) -> None:
        with pytest.raises(ConfigurationError, match="cannot import"):
            _load_scraper_class("scrapers.does_not_exist:Whatever")

    def test_raises_configuration_error_for_non_scraper_class(self) -> None:
        with pytest.raises(ConfigurationError, match="does not resolve to a BaseScraper"):
            _load_scraper_class("core.models:ScrapedRecord")


class TestDedupe:
    async def test_keeps_only_first_occurrence_of_each_content_hash(self) -> None:
        state_store = _FakeStateStore()
        records = [_record("r1", "same"), _record("r2", "same"), _record("r3", "different")]

        # fake implements only the seen/mark_seen slice of StateStore
        deduped = await _dedupe(records, state_store)  # type: ignore[arg-type]

        assert [r.record_id for r in deduped] == ["r1", "r3"]

    async def test_skips_a_hash_already_marked_seen_in_a_prior_run(self) -> None:
        state_store = _FakeStateStore()
        await state_store.mark_seen("h1", "books_sandbox", 90)

        # fake implements only the seen/mark_seen slice of StateStore
        deduped = await _dedupe([_record("r1", "h1")], state_store)  # type: ignore[arg-type]

        assert deduped == []


@respx.mock
async def test_run_site_executes_the_full_pipeline_against_a_mocked_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pipeline.orchestrator.run import run_site

    monkeypatch.setattr(BooksScraper, "rate_limit", RateLimitConfig(rps=1000.0, burst=1000))
    respx.get("https://books.toscrape.com/robots.txt").mock(return_value=_ROBOTS_PERMISSIVE)
    respx.get(url__regex=r"https://books\.toscrape\.com/catalogue/page-\d+\.html").mock(
        side_effect=_page_response
    )

    result = await run_site(
        "books_sandbox",
        settings=Settings(pipeline_backend=Backend.LOCAL),
        sites_path=SITES_VALID,
        local_root=tmp_path / "data",
    )

    assert isinstance(result, RunResult)
    assert result.site_id == "books_sandbox"
    assert result.record_count == 100  # 50 pages x 2 articles, all unique content hashes
    assert result.quarantined_count == 0
    assert result.gate_status == "pass"
    assert result.http_only_fetch_count == 50  # one fetch per page, none ever escalated
    assert result.escalated_fetch_count == 0

    root = tmp_path / "data"
    raw_files = list((root / "raw").rglob("*.html.gz"))
    assert len(raw_files) == 50
    curated_files = list((root / "curated").rglob("*.parquet"))
    assert len(curated_files) == 1
    assert (root / "reports" / "dq_report.html").exists()
    assert list((root / "reports").glob("run=*/dq_report.html"))


class TestBuildFetcher:
    async def test_http_strategy_returns_an_escalating_fetcher(self) -> None:
        async with httpx.AsyncClient() as client, AsyncExitStack() as stack:
            fetcher, escalating = await _build_fetcher("http", client, "test-ua/1.0", stack)

        assert isinstance(fetcher, EscalatingFetcher)
        assert escalating is fetcher

    async def test_browser_strategy_returns_a_browser_fetcher_with_no_escalation_wrapper(
        self,
    ) -> None:
        async with httpx.AsyncClient() as client, AsyncExitStack() as stack:
            fetcher, escalating = await _build_fetcher("browser", client, "test-ua/1.0", stack)

            assert isinstance(fetcher, BrowserFetcher)
            assert escalating is None

    async def test_http_strategy_escalation_lazily_launches_a_browser_on_demand(self) -> None:
        from tests.resilience.conftest import hostile_server

        async with (
            hostile_server() as base_url,
            httpx.AsyncClient() as client,
            AsyncExitStack() as stack,
        ):
            fetcher, escalating = await _build_fetcher("http", client, "test-ua/1.0", stack)
            assert escalating is not None

            raw = await fetcher.fetch(
                Target(url=f"{base_url}/js-rendered"),
                rate_limit=RateLimitConfig(rps=1000.0, burst=1000),
            )

        assert "rendered by JS" in raw.body.decode("utf-8")
        assert escalating.escalated_count == 1
        assert escalating.http_only_count == 0
