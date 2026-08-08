"""Shared data models used by the backend protocols and the scraper plugin contract.

`RunManifest`, `SiteState`, and `Alert` are minimal placeholders sized to make the Phase 0
protocol signatures in `backends/base.py` type-check; their full field set is designed alongside
the orchestrator and quality engine. `RateLimitConfig`, `Target`, `RawResponse`, and
`HealthStatus` back the `BaseScraper` contract in `core/scraper.py` and the fetchers in
`fetchers/`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


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
