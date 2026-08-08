"""Shared data models used by the backend protocols and the scraper plugin contract.

`RunManifest`, `SiteState`, and `Alert` are minimal placeholders sized to make the Phase 0
protocol signatures in `backends/base.py` type-check; their full field set is designed alongside
the orchestrator and quality engine. `RateLimitConfig`, `Target`, `RawResponse`, and
`HealthStatus` back the `BaseScraper` contract in `core/scraper.py` and the fetchers in
`fetchers/`. `ScrapedRecord` and `QuarantinedRecord` back `BaseScraper.parse` (PRD §3.3, Hard
Rule 6).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, HttpUrl

from pipeline.core.config import Backend


class AlertSeverity(StrEnum):
    """Severity of an `Alert` delivered through a `Notifier`."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class RunManifest(BaseModel):
    """Summary of a single orchestrator run, persisted via `StateStore.save_run`."""

    run_id: str
    started_at: datetime
    finished_at: datetime
    site_counts: dict[str, int]
    error_counts: dict[str, int]
    git_sha: str


class SiteState(BaseModel):
    """Per-site state persisted between runs via `StateStore.put_site_state`."""

    site_id: str
    last_success_at: datetime | None
    consecutive_failures: int
    breaker_open: bool


class Alert(BaseModel):
    """A single alert payload delivered by a `Notifier`."""

    severity: AlertSeverity
    title: str
    message: str
    dims: dict[str, str]


class RateLimitConfig(BaseModel):
    """Per-domain fetch pacing, as declared in `sites.yaml`."""

    rps: float
    burst: int
    respect_crawl_delay: bool = True


class Target(BaseModel):
    """One URL yielded by `BaseScraper.discover` for a fetcher to retrieve."""

    url: str
    max_scroll_rounds: int = 0
    """Hint for `BrowserFetcher` only: scroll-and-wait up to this many rounds before extracting
    content, for infinite-scroll pages. `0` (default) means no scrolling. `HttpFetcher` ignores
    this field entirely, since a plain HTTP GET cannot trigger scroll-loaded content anyway."""


class RawResponse(BaseModel):
    """An unparsed fetch result, persisted to the raw zone before parsing (Hard Rule 5)."""

    url: str
    status_code: int
    headers: dict[str, str]
    body: bytes
    fetched_at: datetime
    content_type: str | None


class HealthStatus(BaseModel):
    """Result of `BaseScraper.health_check`: whether a scraper's selectors still work."""

    healthy: bool
    message: str


class ScrapedRecord(BaseModel):
    """Canonical record schema every `BaseScraper.parse` implementation returns (PRD §3.3).

    Lineage fields (`run_id`, `scraper_version`, `fetch_strategy`, `backend`, `extraction_method`)
    are what let a record be debugged months later without re-fetching anything.
    """

    # Identity
    record_id: str
    site_id: str
    source_url: HttpUrl
    content_hash: str

    # Lineage
    run_id: str
    scraped_at: datetime
    scraper_version: str
    fetch_strategy: Literal["http", "browser"]
    backend: Backend

    # Payload
    title: str
    price: Decimal | None
    currency: str | None
    availability: str | None
    attributes: dict[str, Any]

    # Quality
    completeness_score: float
    extraction_method: Literal["css", "xpath", "json_ld", "llm_fallback"]


class QuarantinedRecord(BaseModel):
    """A record that failed validation, kept with its rejection reason (Hard Rule 6, ADR 0005).

    Written to `quarantine/site=<site_id>/dt=<date>/rejected.jsonl` — never silently dropped.
    """

    site_id: str
    source_url: str
    run_id: str
    quarantined_at: datetime
    reason: str
    payload: dict[str, Any]
