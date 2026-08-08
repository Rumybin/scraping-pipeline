"""Tests for `pipeline.quality.dq_engine` — the four-dimension DQ engine v1 (FR-13/FR-14)."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from pipeline.core.config import Backend
from pipeline.core.models import ScrapedRecord
from pipeline.quality.dq_engine import STRICT_PROFILE, evaluate, resolve_profile


def _record(
    *,
    record_id: str = "r1",
    title: str = "A Light in the Attic",
    price: Decimal | None = Decimal("51.77"),
    currency: str | None = "GBP",
    availability: str | None = "In stock",
    content_hash: str = "hash-1",
    completeness_score: float = 1.0,
) -> ScrapedRecord:
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
        title=title,
        price=price,
        currency=currency,
        availability=availability,
        attributes={},
        completeness_score=completeness_score,
        extraction_method="css",
    )


def test_resolve_profile_returns_registered_profile() -> None:
    assert resolve_profile("strict") is STRICT_PROFILE


def test_resolve_profile_raises_key_error_for_unknown_name() -> None:
    with pytest.raises(KeyError):
        resolve_profile("does-not-exist")


def test_evaluate_all_clean_records_passes_every_dimension() -> None:
    records = [_record(record_id=f"r{i}", content_hash=f"hash-{i}") for i in range(5)]

    report = evaluate(records, run_id="run-1", site_id="books_sandbox", profile=STRICT_PROFILE)

    assert report.total_records == 5
    assert report.gate_status == "pass"
    assert all(dimension.status == "pass" for dimension in report.dimensions)


def test_evaluate_empty_batch_fails_the_gate() -> None:
    report = evaluate([], run_id="run-1", site_id="books_sandbox", profile=STRICT_PROFILE)

    assert report.total_records == 0
    assert report.gate_status == "fail"
    assert all(dimension.score == 0.0 for dimension in report.dimensions)


def test_evaluate_duplicate_content_hash_fails_uniqueness() -> None:
    records = [
        _record(record_id="r1", content_hash="same"),
        _record(record_id="r2", content_hash="same"),
    ]

    report = evaluate(records, run_id="run-1", site_id="books_sandbox", profile=STRICT_PROFILE)

    uniqueness = next(d for d in report.dimensions if d.dimension == "uniqueness")
    assert uniqueness.score == 0.5
    assert uniqueness.status == "fail"
    assert report.gate_status == "fail"


def test_evaluate_non_positive_price_fails_validity() -> None:
    records = [
        _record(record_id="r1", content_hash="h1", price=Decimal("10.00")),
        _record(record_id="r2", content_hash="h2", price=Decimal("-5.00")),
    ]

    report = evaluate(records, run_id="run-1", site_id="books_sandbox", profile=STRICT_PROFILE)

    validity = next(d for d in report.dimensions if d.dimension == "validity")
    assert validity.score == 0.5
    assert validity.status == "fail"


def test_evaluate_price_without_currency_fails_consistency() -> None:
    records = [
        _record(record_id="r1", content_hash="h1", price=Decimal("10.00"), currency="GBP"),
        _record(record_id="r2", content_hash="h2", price=Decimal("10.00"), currency=None),
    ]

    report = evaluate(records, run_id="run-1", site_id="books_sandbox", profile=STRICT_PROFILE)

    consistency = next(d for d in report.dimensions if d.dimension == "consistency")
    assert consistency.score == 0.5
    assert consistency.status == "fail"


def test_evaluate_low_completeness_fails_that_dimension() -> None:
    records = [
        _record(record_id="r1", content_hash="h1", completeness_score=1.0),
        _record(record_id="r2", content_hash="h2", completeness_score=0.5),
    ]

    report = evaluate(records, run_id="run-1", site_id="books_sandbox", profile=STRICT_PROFILE)

    completeness = next(d for d in report.dimensions if d.dimension == "completeness")
    assert completeness.score == pytest.approx(0.75)
    assert completeness.status == "fail"
