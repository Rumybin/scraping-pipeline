"""Shared quote-parsing logic for quotes.toscrape.com's `/js/` and `/scroll` variants (2.2).

Both variants render the identical `div.quote` markup client-side, from the same underlying
quote dataset — just via different loading mechanisms (paginated `document.write` vs
scroll-triggered AJAX). That markup-parsing logic is the one thing worth sharing; each site's
`discover()` pagination strategy differs enough to stay in its own scraper file.
"""

from __future__ import annotations

import hashlib

from selectolax.parser import Node

from pipeline.core.context import RunContext
from pipeline.core.exceptions import RecordValidationError
from pipeline.core.models import RawResponse, ScrapedRecord
from pipeline.core.normalize import normalize_text


def build_quote_record(
    quote: Node, *, site_id: str, raw: RawResponse, ctx: RunContext
) -> ScrapedRecord:
    """Build one `ScrapedRecord` from a single rendered `div.quote` node.

    Raises `RecordValidationError` if the quote text or author is missing.
    """
    text_node = quote.css_first("span.text")
    if text_node is None or not text_node.text(strip=True):
        raise RecordValidationError("missing quote text")
    text = normalize_text(text_node.text(strip=True))

    author_node = quote.css_first("small.author")
    if author_node is None or not author_node.text(strip=True):
        raise RecordValidationError("missing author")
    author = normalize_text(author_node.text(strip=True))

    tags = [normalize_text(tag.text(strip=True)) for tag in quote.css("div.tags a.tag")]

    return ScrapedRecord(
        record_id=_hash(f"{site_id}:{raw.url}:{text}"),
        site_id=site_id,
        source_url=raw.url,  # type: ignore[arg-type]  # pydantic HttpUrl coerces from str
        content_hash=_hash(f"{text}|{author}"),
        run_id=ctx.run_id,
        scraped_at=raw.fetched_at,
        scraper_version=ctx.scraper_version,
        fetch_strategy="browser",
        backend=ctx.backend,
        title=text,
        price=None,
        currency=None,
        availability=None,
        attributes={"author": author, "tags": tags},
        completeness_score=1.0,
        extraction_method="css",
    )


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
