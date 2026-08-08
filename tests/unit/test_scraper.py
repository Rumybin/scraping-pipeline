"""Tests for the `BaseScraper` plugin contract in `pipeline.core.scraper`."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from pipeline.core.config import Backend
from pipeline.core.context import RunContext
from pipeline.core.models import HealthStatus, RateLimitConfig, RawResponse, Target
from pipeline.core.scraper import BaseScraper


class _DummyRecord(BaseModel):
    value: str


class _DummyScraper(BaseScraper):
    site_id = "dummy_site"
    strategy = "http"
    rate_limit = RateLimitConfig(rps=1.0, burst=1)
    record_model = _DummyRecord

    async def discover(self, ctx: RunContext) -> AsyncIterator[Target]:
        yield Target(url=f"https://example.invalid/{ctx.site_id}")

    async def parse(self, raw: RawResponse, ctx: RunContext) -> list[BaseModel]:
        return [_DummyRecord(value=raw.url)]


def _make_context() -> RunContext:
    return RunContext(
        run_id="run-1",
        site_id="dummy_site",
        backend=Backend.LOCAL,
        scraper_version="deadbeef",
        started_at=datetime.now(UTC),
    )


def test_base_scraper_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        BaseScraper()  # type: ignore[abstract]  # instantiating the ABC directly is the point of this test


async def test_discover_yields_targets() -> None:
    scraper = _DummyScraper()
    ctx = _make_context()

    targets = [target async for target in scraper.discover(ctx)]

    assert targets == [Target(url="https://example.invalid/dummy_site")]


async def test_parse_returns_validated_records() -> None:
    scraper = _DummyScraper()
    ctx = _make_context()
    raw = RawResponse(
        url="https://example.invalid/page",
        status_code=200,
        headers={},
        body=b"<html></html>",
        fetched_at=datetime.now(UTC),
        content_type="text/html",
    )

    records = await scraper.parse(raw, ctx)

    assert records == [_DummyRecord(value="https://example.invalid/page")]


async def test_default_health_check_reports_healthy_with_no_implementation_note() -> None:
    scraper = _DummyScraper()

    status = await scraper.health_check()

    assert status == HealthStatus(healthy=True, message="no site-specific health check implemented")
