"""Backend factory: resolves the concrete `ObjectStore`/`StateStore` pair from `PIPELINE_BACKEND`.

See CLAUDE.md §5 — this is the only place a `PIPELINE_BACKEND` value is mapped to a concrete
backend module; scraper and orchestrator code depends only on the protocols in `backends/base.py`,
never on `local.py`/`free.py`/`aws.py` directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pipeline.backends.base import ObjectStore, StateStore
from pipeline.backends.local import LocalObjectStore, SqliteStateStore
from pipeline.core.config import Backend, Settings


@dataclass(frozen=True)
class BackendSet:
    """The concrete `ObjectStore`/`StateStore` pair selected for the active backend."""

    object_store: ObjectStore
    state_store: StateStore


def build_backend_set(settings: Settings, *, local_root: Path = Path("data")) -> BackendSet:
    """Construct the `ObjectStore`/`StateStore` pair for `settings.pipeline_backend`.

    `local_root` only applies to the `local` backend. Raises `NotImplementedError` for `free` and
    `aws`, whose backends are implemented in Phase 3.
    """
    if settings.pipeline_backend == Backend.LOCAL:
        return BackendSet(
            object_store=LocalObjectStore(local_root),
            state_store=SqliteStateStore(local_root / "state.db"),
        )
    raise NotImplementedError(
        f"backend {settings.pipeline_backend.value!r} is not implemented yet (Phase 3)"
    )
