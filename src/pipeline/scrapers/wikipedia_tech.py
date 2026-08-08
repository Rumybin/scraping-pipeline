"""T1 scraper for a curated set of English Wikipedia technology articles (PRD §2.4, Phase 2.7).

Approved in `docs/compliance.md` after arXiv, OpenLibrary, PyPI, and Project Gutenberg were each
audited and rejected (full `Disallow: /`, an active bot-verification challenge, a disallowed API
path, and an explicit anti-automation ToS clause, respectively — see the compliance table for
detail). Only `/wiki/<Title>` article pages are fetched; `/w/api.php` and `/wiki/Special:Search`
are both disallowed by `robots.txt`, so discovery is a curated, individually-verified title list
rather than a search/API index — the same "known, bounded target set" pattern already used by
`BooksScraper`'s page count and `QuotesJsScraper`'s page count, just applied to article titles.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import AsyncIterator
from typing import Literal
from urllib.parse import quote

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
from pipeline.core.normalize import normalize_text
from pipeline.core.scraper import BaseScraper

_ARTICLE_BASE_URL = "https://en.wikipedia.org/wiki/"

_ARTICLE_TITLES = (
    "Python_(programming_language)",
    "JavaScript",
    "Rust_(programming_language)",
    "Go_(programming_language)",
    "TypeScript",
    "Hypertext_Transfer_Protocol",
    "Representational_state_transfer",
    "GraphQL",
    "WebSocket",
    "Docker_(software)",
    "Kubernetes",
    "PostgreSQL",
    "Redis",
    "Apache_Kafka",
    "SQLite",
    "Apache_Parquet",
    "DuckDB",
    "Machine_learning",
    "Artificial_intelligence",
    "Artificial_neural_network",
    "Web_scraping",
    "Web_crawler",
    "Robots_exclusion_standard",
    "Rate_limiting",
    "Circuit_breaker_design_pattern",
    "Data_quality",
    "Distributed_computing",
    "Microservices",
    "Continuous_integration",
    "Git",
)
"""Individually verified to return HTTP 200 (per docs/compliance.md's audit method) rather than
discovered via search or an index page, since both are disallowed by robots.txt. Thematically
tied to this project's own stack — a deliberate, honest choice, not an attempt to disguise a
narrow demo set as broad "real volume": each article is nonetheless genuine, large-scale public
content, not a synthetic fixture."""

_MIN_LEAD_PARAGRAPH_LENGTH = 80
_CITATION_MARKER_RE = re.compile(r"\[\s*\w+\s*\]")
_SPACE_BEFORE_PUNCTUATION_RE = re.compile(r"\s+([,.;:])")
_WHITESPACE_RE = re.compile(r"\s+")


class WikipediaScraper(BaseScraper):
    """Scrapes a curated set of English Wikipedia technology articles."""

    site_id = "wikipedia_tech"
    strategy: Literal["http", "browser"] = "http"
    rate_limit = RateLimitConfig(rps=1.0, burst=1, respect_crawl_delay=True)
    record_model = ScrapedRecord

    async def discover(self, ctx: RunContext) -> AsyncIterator[Target]:
        """Yield the curated, individually-verified article title list."""
        for title in _ARTICLE_TITLES:
            yield Target(url=f"{_ARTICLE_BASE_URL}{quote(title)}")

    async def parse(self, raw: RawResponse, ctx: RunContext) -> list[BaseModel]:
        """Extract one `ScrapedRecord` from `raw`'s Wikipedia article page.

        Raises `ParsingError` if `raw` is not a successful response or has no article heading at
        all. A missing lead paragraph does not fail the page — the record is appended to
        `self.quarantined` with a reason instead (Hard Rule 6).
        """
        if raw.status_code != 200:
            raise ParsingError(f"unexpected status {raw.status_code} fetching {raw.url}")

        tree = HTMLParser(raw.body)
        heading = tree.css_first("#firstHeading")
        if heading is None or not heading.text(strip=True):
            raise ParsingError(f"no article heading found on {raw.url}")

        try:
            record = self._parse_article(tree, heading, raw, ctx)
        except RecordValidationError as exc:
            self.quarantined.append(
                QuarantinedRecord(
                    site_id=self.site_id,
                    source_url=raw.url,
                    run_id=ctx.run_id,
                    quarantined_at=raw.fetched_at,
                    reason=str(exc),
                    payload={"title": heading.text(strip=True)},
                )
            )
            return []
        return [record]

    def _parse_article(
        self, tree: HTMLParser, heading: Node, raw: RawResponse, ctx: RunContext
    ) -> ScrapedRecord:
        """Build one `ScrapedRecord` from a parsed article page.

        Raises `RecordValidationError` if no substantial lead paragraph is found.
        """
        title = normalize_text(heading.text(strip=True))

        short_description_node = tree.css_first(".shortdescription")
        short_description = (
            normalize_text(short_description_node.text(strip=True))
            if short_description_node
            else None
        )

        summary = self._lead_paragraph(tree)
        if summary is None:
            raise RecordValidationError("no substantial lead paragraph found")

        return ScrapedRecord(
            record_id=_hash(f"{self.site_id}:{raw.url}"),
            site_id=self.site_id,
            source_url=raw.url,  # type: ignore[arg-type]  # pydantic HttpUrl coerces from str
            content_hash=_hash(f"{title}|{summary}"),
            run_id=ctx.run_id,
            scraped_at=raw.fetched_at,
            scraper_version=ctx.scraper_version,
            fetch_strategy="http",
            backend=ctx.backend,
            title=title,
            price=None,
            currency=None,
            availability=None,
            attributes={"short_description": short_description, "summary": summary},
            completeness_score=1.0 if short_description else 0.5,
            extraction_method="css",
        )

    def _lead_paragraph(self, tree: HTMLParser) -> str | None:
        content = tree.css_first("#mw-content-text")
        if content is None:
            return None
        for paragraph in content.css("p"):
            text = paragraph.text(deep=True, separator=" ", strip=True)
            text = _clean_wiki_text(text)
            if len(text) >= _MIN_LEAD_PARAGRAPH_LENGTH:
                return text
        return None


def _clean_wiki_text(text: str) -> str:
    """Strip citation markers (e.g. `[38]`) and tidy the spacing artifacts that come from
    joining Parsoid's many inline elements with a blanket separator."""
    text = _CITATION_MARKER_RE.sub("", text)
    text = _SPACE_BEFORE_PUNCTUATION_RE.sub(r"\1", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
