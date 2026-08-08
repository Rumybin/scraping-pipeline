"""Backend capability protocols.

Concrete implementations live in per-backend modules (`local.py`, `free.py`, `aws.py`) and are
selected at runtime by the factory in `backends/__init__.py`, based on `PIPELINE_BACKEND`. No
module outside `backends/` may import a vendor SDK — see CLAUDE.md §2 Hard Rule 1 and
`docs/adr/0001-backend-abstraction.md`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from pipeline.core.models import Alert, RunManifest, SiteState


class ObjectStore(Protocol):
    """Content-addressable storage for raw responses, staged Parquet, and quarantine data."""

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        """Write `data` under `key`. Raises `ObjectStoreError` on failure."""
        ...

    async def get(self, key: str) -> bytes:
        """Read the object stored at `key`. Raises `ObjectNotFoundError` if it does not exist."""
        ...

    async def exists(self, key: str) -> bool:
        """Return whether an object exists at `key`."""
        ...

    def list(self, prefix: str) -> AsyncIterator[str]:
        """Yield every key under `prefix`.

        Declared as a plain method returning `AsyncIterator[str]`, not `async def`, because
        implementations are async generators (`async def list(...): yield ...`) — callers use
        `async for`, not `await` — matching `BaseScraper.discover` in `core/scraper.py`.
        """
        ...


class StateStore(Protocol):
    """Durable state: dedupe hashes, run manifests, and per-site breaker/baseline state."""

    async def seen(self, content_hash: str) -> bool:
        """Return whether `content_hash` was recorded before its TTL expired."""
        ...

    async def mark_seen(self, content_hash: str, site_id: str, ttl_days: int) -> None:
        """Record `content_hash` as seen for `site_id`, expiring after `ttl_days`."""
        ...

    async def save_run(self, manifest: RunManifest) -> None:
        """Persist a completed run's manifest."""
        ...

    async def get_site_state(self, site_id: str) -> SiteState:
        """Return the persisted state for `site_id`. Raises `SiteStateNotFoundError` if absent."""
        ...

    async def put_site_state(self, state: SiteState) -> None:
        """Persist `state`, overwriting any prior state for the same site."""
        ...


class MetricsSink(Protocol):
    """Emits scalar metrics tagged with dimensions to the active backend's metrics system."""

    async def emit(self, name: str, value: float, dims: dict[str, str]) -> None:
        """Record one observation of metric `name` with `value`, tagged by `dims`."""
        ...


class Notifier(Protocol):
    """Delivers alerts to the active backend's notification channel."""

    async def send(self, alert: Alert) -> None:
        """Deliver `alert`. Raises `NotificationError` if delivery fails."""
        ...
