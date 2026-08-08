"""Run-scoped identity passed to every `BaseScraper.discover` and `.parse` call."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from pipeline.core.config import Backend


class RunContext(BaseModel):
    """Immutable lineage carried through a single scraper run.

    Mirrors the lineage fields (`run_id`, `scraper_version`, `backend`) that every
    `ScrapedRecord` must record, so a scraper's `discover`/`parse` methods have everything
    needed to stamp them without reaching into global state.
    """

    model_config = ConfigDict(frozen=True)

    run_id: str
    site_id: str
    backend: Backend
    scraper_version: str
    started_at: datetime
