"""The scraper plugin contract: one file per site, no changes to core required (FR-5)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Literal

from pydantic import BaseModel

from pipeline.core.context import RunContext
from pipeline.core.models import (
    HealthStatus,
    QuarantinedRecord,
    RateLimitConfig,
    RawResponse,
    Target,
)


class BaseScraper(ABC):
    """Contract every site-specific scraper plugin implements.

    `discover` is implemented as an async generator by subclasses (`async def discover(...):
    yield ...`); it is declared here as a plain method returning `AsyncIterator[Target]` because
    that is the type callers see when they call it, not when they await it.
    """

    site_id: str
    strategy: Literal["http", "browser"]
    rate_limit: RateLimitConfig
    record_model: type[BaseModel]

    def __init__(self) -> None:
        self.quarantined: list[QuarantinedRecord] = []

    @abstractmethod
    def discover(self, ctx: RunContext) -> AsyncIterator[Target]:
        """Yield the URLs/targets to fetch for this run (pagination, sitemap, API index)."""
        raise NotImplementedError

    @abstractmethod
    async def parse(self, raw: RawResponse, ctx: RunContext) -> list[BaseModel]:
        """Turn one raw fetched response into validated records.

        A record that fails validation is appended to `self.quarantined` with a reason instead of
        raising (Hard Rule 6) — the whole response is skipped only if its expected structure is
        entirely absent, in which case this raises `ParsingError`.
        """
        raise NotImplementedError

    async def health_check(self) -> HealthStatus:
        """Check a single sample page to confirm this scraper's selectors still work.

        The default implementation performs no check; override it for a real pre-run signal.
        """
        return HealthStatus(healthy=True, message="no site-specific health check implemented")
