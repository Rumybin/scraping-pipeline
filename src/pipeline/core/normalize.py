"""Field normalizers applied by scrapers before constructing a `ScrapedRecord` (FR-7).

Every scraper calls these instead of parsing prices, dates, text, or URLs ad hoc, so normalization
rules live in one place and are unit-tested independently of any one site's markup.
"""

from __future__ import annotations

import unicodedata
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin

from pipeline.core.exceptions import RecordValidationError

_CURRENCY_SYMBOLS = {
    "£": "GBP",
    "$": "USD",
    "€": "EUR",
}


def normalize_text(raw: str) -> str:
    """Normalize `raw` to NFKC form with surrounding whitespace stripped (FR-7)."""
    return unicodedata.normalize("NFKC", raw).strip()


def normalize_url(raw: str, *, base_url: str) -> str:
    """Resolve `raw` to an absolute URL against `base_url` (FR-7)."""
    return urljoin(base_url, raw.strip())


def normalize_price(raw: str) -> Decimal:
    """Parse a price string into a `Decimal`, ignoring any leading currency symbol (FR-7).

    Raises `RecordValidationError` if `raw` contains no parseable decimal number.
    """
    digits_and_dot = "".join(char for char in raw.strip() if char.isdigit() or char == ".")
    try:
        return Decimal(digits_and_dot)
    except InvalidOperation as exc:
        raise RecordValidationError(f"cannot parse price from {raw!r}") from exc


def normalize_currency(raw: str) -> str:
    """Map a leading currency symbol in `raw` to its ISO-4217 code (FR-7).

    Raises `RecordValidationError` if `raw` starts with no recognized currency symbol.
    """
    stripped = raw.strip()
    for symbol, code in _CURRENCY_SYMBOLS.items():
        if stripped.startswith(symbol):
            return code
    raise RecordValidationError(f"no recognized currency symbol in {raw!r}")


def normalize_datetime(raw: str, *, fmt: str) -> datetime:
    """Parse `raw` with `fmt` and return a UTC-aware `datetime` (FR-7).

    `fmt` must be supplied by the caller because it depends on the source site's own date format;
    a value parsed as naive is assumed to already represent UTC, since the scraper owns the
    knowledge of which timezone its site's dates are published in. Raises `RecordValidationError`
    if `raw` does not match `fmt`.
    """
    try:
        parsed = datetime.strptime(raw.strip(), fmt)
    except ValueError as exc:
        raise RecordValidationError(f"cannot parse date {raw!r} with format {fmt!r}") from exc
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
