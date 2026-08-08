"""Tests for the field normalizers in `pipeline.core.normalize` (FR-7)."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from pipeline.core.exceptions import RecordValidationError
from pipeline.core.normalize import (
    normalize_currency,
    normalize_datetime,
    normalize_price,
    normalize_text,
    normalize_url,
)


def test_normalize_text_collapses_nfkc_variants_and_strips_whitespace() -> None:
    # Roman numeral U+2160 is deliberately ambiguous with "I" — that's the NFKC fold under test.
    assert normalize_text("  Ⅰ Century  ") == "I Century"  # noqa: RUF001


def test_normalize_url_resolves_relative_path_against_base() -> None:
    resolved = normalize_url(
        "../../a-light-in-the-attic_1000/index.html",
        base_url="https://books.toscrape.com/catalogue/category/books/page-1.html",
    )

    assert resolved == "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"


def test_normalize_url_leaves_absolute_url_unchanged() -> None:
    resolved = normalize_url(
        "https://example.invalid/x", base_url="https://books.toscrape.com/catalogue/page-1.html"
    )

    assert resolved == "https://example.invalid/x"


def test_normalize_price_parses_symbol_prefixed_amount() -> None:
    assert normalize_price("£51.77") == Decimal("51.77")


def test_normalize_price_parses_amount_with_thousands_free_plain_digits() -> None:
    assert normalize_price("  1234.50  ") == Decimal("1234.50")


def test_normalize_price_raises_on_unparseable_value() -> None:
    with pytest.raises(RecordValidationError, match="cannot parse price"):
        normalize_price("free")


def test_normalize_currency_maps_known_symbol() -> None:
    assert normalize_currency("£51.77") == "GBP"


def test_normalize_currency_raises_on_unrecognized_symbol() -> None:
    with pytest.raises(RecordValidationError, match="no recognized currency symbol"):
        normalize_currency("51.77 kr")


def test_normalize_datetime_parses_naive_value_as_utc() -> None:
    parsed = normalize_datetime("2026-08-08", fmt="%Y-%m-%d")

    assert parsed == datetime(2026, 8, 8, tzinfo=UTC)


def test_normalize_datetime_raises_on_format_mismatch() -> None:
    with pytest.raises(RecordValidationError, match="cannot parse date"):
        normalize_datetime("08/08/2026", fmt="%Y-%m-%d")
