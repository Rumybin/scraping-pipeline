"""T0 sandbox scraper for `books.toscrape.com` (FR-5, PRD §2.4, Phase 1.4).

Approved in `docs/compliance.md` — the site has no `robots.txt` and its own homepage banner
states its catalog is a synthetic scraping demo. Only the paginated catalogue listing pages are
fetched; per-book detail pages are not visited, since every field this scraper extracts (title,
price, availability, star rating, thumbnail) is already present on the listing page.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from typing import Literal

from pydantic import BaseModel
from selectolax.parser import HTMLParser, Node

from pipeline.core.context import RunContext
from pipeline.core.exceptions import ParsingError, RecordValidationError
from pipeline.core.models import (
    QuarantinedRecord,
    RateLimitConfig,
    RawResponse,
    ScrapedRecord,
    Target,
)
from pipeline.core.normalize import (
    normalize_currency,
    normalize_price,
    normalize_text,
    normalize_url,
)
from pipeline.core.scraper import BaseScraper

_CATALOGUE_BASE_URL = "https://books.toscrape.com/catalogue/"
_TOTAL_CATALOGUE_PAGES = 50
"""books.toscrape.com's own pagination footer reports "Page N of 50" on every listing page; the
catalog is a fixed, static demo dataset (per docs/compliance.md) so this count is stable."""

_RATING_WORDS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}


class BooksScraper(BaseScraper):
    """Scrapes the full paginated book catalogue at books.toscrape.com."""

    site_id = "books_sandbox"
    strategy: Literal["http", "browser"] = "http"
    rate_limit = RateLimitConfig(rps=2.0, burst=5, respect_crawl_delay=True)
    record_model = ScrapedRecord

    async def discover(self, ctx: RunContext) -> AsyncIterator[Target]:
        """Yield the site's fixed 50 catalogue listing pages."""
        for page_number in range(1, _TOTAL_CATALOGUE_PAGES + 1):
            yield Target(url=f"{_CATALOGUE_BASE_URL}page-{page_number}.html")

    async def parse(self, raw: RawResponse, ctx: RunContext) -> list[BaseModel]:
        """Extract one `ScrapedRecord` per book listed on `raw`'s catalogue page.

        Raises `ParsingError` if `raw` is not a successful response or contains no book listings
        at all. A single malformed listing does not fail the page — it is appended to
        `self.quarantined` with a reason and parsing continues (Hard Rule 6).
        """
        if raw.status_code != 200:
            raise ParsingError(f"unexpected status {raw.status_code} fetching {raw.url}")

        tree = HTMLParser(raw.body)
        articles = tree.css("article.product_pod")
        if not articles:
            raise ParsingError(f"no product listings found on {raw.url}")

        records: list[BaseModel] = []
        for article in articles:
            try:
                records.append(self._parse_article(article, raw, ctx))
            except RecordValidationError as exc:
                self.quarantined.append(
                    QuarantinedRecord(
                        site_id=self.site_id,
                        source_url=raw.url,
                        run_id=ctx.run_id,
                        quarantined_at=raw.fetched_at,
                        reason=str(exc),
                        payload={"article_html": article.html or ""},
                    )
                )
        return records

    def _parse_article(self, article: Node, raw: RawResponse, ctx: RunContext) -> ScrapedRecord:
        """Build one `ScrapedRecord` from a single `article.product_pod` node.

        Raises `RecordValidationError` if a required field (title, detail link, or price) is
        missing from the article.
        """
        title_node = article.css_first("h3 > a")
        title_attr = title_node.attributes.get("title") if title_node else None
        if not title_attr:
            raise RecordValidationError("missing title anchor in product listing")
        title = normalize_text(title_attr)

        detail_href = title_node.attributes.get("href") if title_node else None
        if not detail_href:
            raise RecordValidationError("missing detail link in product listing")
        source_url = normalize_url(detail_href, base_url=raw.url)

        price_node = article.css_first("p.price_color")
        if price_node is None:
            raise RecordValidationError("missing price in product listing")
        price_text = price_node.text(strip=True)
        price = normalize_price(price_text)
        currency = normalize_currency(price_text)

        availability_node = article.css_first("p.instock.availability")
        availability = (
            normalize_text(availability_node.text(strip=True)) if availability_node else None
        )

        rating_node = article.css_first("p.star-rating")
        rating = _rating_from_class(rating_node.attributes.get("class")) if rating_node else None

        image_node = article.css_first("div.image_container img")
        image_src = image_node.attributes.get("src") if image_node else None
        image_url = normalize_url(image_src, base_url=raw.url) if image_src else None

        required_fields = (title, price is not None, currency is not None, availability is not None)
        completeness_score = sum(1 for field in required_fields if field) / len(required_fields)

        return ScrapedRecord(
            record_id=_hash(f"{self.site_id}:{source_url}"),
            site_id=self.site_id,
            source_url=source_url,  # type: ignore[arg-type]  # pydantic HttpUrl coerces from str
            content_hash=_hash(f"{title}|{price}|{availability}"),
            run_id=ctx.run_id,
            scraped_at=raw.fetched_at,
            scraper_version=ctx.scraper_version,
            fetch_strategy="http",
            backend=ctx.backend,
            title=title,
            price=price,
            currency=currency,
            availability=availability,
            attributes={"rating": rating, "image_url": image_url},
            completeness_score=completeness_score,
            extraction_method="css",
        )


def _rating_from_class(class_attr: str | None) -> int | None:
    """Map a `p.star-rating` element's CSS class (e.g. `"star-rating Three"`) to an int 1-5."""
    if not class_attr:
        return None
    for token in class_attr.split():
        if token in _RATING_WORDS:
            return _RATING_WORDS[token]
    return None


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
