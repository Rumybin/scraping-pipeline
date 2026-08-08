"""The `local` backend: filesystem `ObjectStore`, SQLite `StateStore`.

See CLAUDE.md §5 — this is the only place these two concrete types are constructed; scraper and
orchestrator code depends on the `ObjectStore`/`StateStore` protocols in `backends/base.py`, never
on these classes directly.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pipeline.core.exceptions import ObjectNotFoundError, SiteStateNotFoundError
from pipeline.core.models import RunManifest, SiteState


class LocalObjectStore:
    """`ObjectStore` backed by the local filesystem, rooted at a configured directory."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        """Write `data` under `key`. `content_type` is unused by this backend."""
        await asyncio.to_thread(self._put_sync, key, data)

    async def get(self, key: str) -> bytes:
        """Read the object stored at `key`. Raises `ObjectNotFoundError` if absent."""
        return await asyncio.to_thread(self._get_sync, key)

    async def exists(self, key: str) -> bool:
        """Return whether an object exists at `key`."""
        return await asyncio.to_thread(self._resolve(key).exists)

    async def list(self, prefix: str) -> AsyncIterator[str]:
        """Yield every key under `prefix`."""
        for key in await asyncio.to_thread(self._list_sync, prefix):
            yield key

    def _put_sync(self, key: str, data: bytes) -> None:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def _get_sync(self, key: str) -> bytes:
        path = self._resolve(key)
        if not path.exists():
            raise ObjectNotFoundError(key)
        return path.read_bytes()

    def _list_sync(self, prefix: str) -> Sequence[str]:
        matched_keys: list[str] = []
        for path in self._root.rglob("*"):
            if not path.is_file():
                continue
            key = path.relative_to(self._root).as_posix()
            if key.startswith(prefix):
                matched_keys.append(key)
        return matched_keys

    def _resolve(self, key: str) -> Path:
        return self._root / key


class SqliteStateStore:
    """`StateStore` backed by a local SQLite database.

    Each operation opens and closes its own connection rather than holding one open across the
    lifetime of the store, since `asyncio.to_thread` may run different calls on different worker
    threads and `sqlite3` connections are not safe to share across them.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    async def seen(self, content_hash: str) -> bool:
        """Return whether `content_hash` was recorded before its TTL expired."""
        return await asyncio.to_thread(self._seen_sync, content_hash)

    async def mark_seen(self, content_hash: str, site_id: str, ttl_days: int) -> None:
        """Record `content_hash` as seen for `site_id`, expiring after `ttl_days`."""
        await asyncio.to_thread(self._mark_seen_sync, content_hash, site_id, ttl_days)

    async def save_run(self, manifest: RunManifest) -> None:
        """Persist a completed run's manifest, overwriting any prior manifest for its run_id."""
        await asyncio.to_thread(self._save_run_sync, manifest)

    async def get_site_state(self, site_id: str) -> SiteState:
        """Return the persisted state for `site_id`. Raises `SiteStateNotFoundError` if absent."""
        return await asyncio.to_thread(self._get_site_state_sync, site_id)

    async def put_site_state(self, state: SiteState) -> None:
        """Persist `state`, overwriting any prior state for the same site."""
        await asyncio.to_thread(self._put_site_state_sync, state)

    def _initialize_schema(self) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dedupe (
                    content_hash TEXT PRIMARY KEY,
                    site_id TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    manifest_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS site_state (
                    site_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL
                )
                """
            )

    def _seen_sync(self, content_hash: str) -> bool:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT expires_at FROM dedupe WHERE content_hash = ?", (content_hash,)
            ).fetchone()
        if row is None:
            return False
        return datetime.now(UTC) < datetime.fromisoformat(row[0])

    def _mark_seen_sync(self, content_hash: str, site_id: str, ttl_days: int) -> None:
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=ttl_days)
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO dedupe (content_hash, site_id, first_seen, expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (content_hash) DO UPDATE SET
                    site_id = excluded.site_id,
                    first_seen = excluded.first_seen,
                    expires_at = excluded.expires_at
                """,
                (content_hash, site_id, now.isoformat(), expires_at.isoformat()),
            )

    def _save_run_sync(self, manifest: RunManifest) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO runs (run_id, manifest_json) VALUES (?, ?)
                ON CONFLICT (run_id) DO UPDATE SET manifest_json = excluded.manifest_json
                """,
                (manifest.run_id, manifest.model_dump_json()),
            )

    def _get_site_state_sync(self, site_id: str) -> SiteState:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT state_json FROM site_state WHERE site_id = ?", (site_id,)
            ).fetchone()
        if row is None:
            raise SiteStateNotFoundError(site_id)
        return SiteState.model_validate_json(row[0])

    def _put_site_state_sync(self, state: SiteState) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO site_state (site_id, state_json) VALUES (?, ?)
                ON CONFLICT (site_id) DO UPDATE SET state_json = excluded.state_json
                """,
                (state.site_id, state.model_dump_json()),
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self._db_path)) as conn, conn:
            yield conn
