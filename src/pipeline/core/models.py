"""Shared data models referenced by the backend protocol signatures in `backends/base.py`.

These are minimal placeholders sized to make the Phase 0 protocol signatures type-check; their
full field set is designed in Phase 1+ alongside the orchestrator and quality engine.
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
