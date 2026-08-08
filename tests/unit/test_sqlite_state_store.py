"""Tests for `pipeline.backends.local.SqliteStateStore`, the SQLite `StateStore`.

TTL expiry is tested with `ttl_days=0` rather than a fake clock: an entry with a zero-day TTL
expires essentially immediately, so a `seen()` check made right after `mark_seen()`
deterministically observes it as expired without needing to fast-forward time.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipeline.backends.local import SqliteStateStore
from pipeline.core.exceptions import SiteStateNotFoundError
from pipeline.core.models import RunManifest, SiteState


@pytest.fixture
def store(tmp_path: Path) -> SqliteStateStore:
    return SqliteStateStore(tmp_path / "state" / "pipeline.db")


async def test_seen_returns_false_for_an_unrecorded_hash(store: SqliteStateStore) -> None:
    assert await store.seen("unknown-hash") is False


async def test_mark_seen_then_seen_returns_true(store: SqliteStateStore) -> None:
    await store.mark_seen("hash-1", "books_sandbox", ttl_days=90)

    assert await store.seen("hash-1") is True


async def test_seen_returns_false_once_the_ttl_has_expired(store: SqliteStateStore) -> None:
    await store.mark_seen("hash-1", "books_sandbox", ttl_days=0)

    assert await store.seen("hash-1") is False


async def test_save_run_persists_the_manifest(store: SqliteStateStore, tmp_path: Path) -> None:
    manifest = RunManifest(
        run_id="run-1",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        site_counts={"books_sandbox": 42},
        error_counts={},
        git_sha="deadbeef",
    )

    await store.save_run(manifest)

    with sqlite3.connect(tmp_path / "state" / "pipeline.db") as conn:
        row = conn.execute("SELECT manifest_json FROM runs WHERE run_id = ?", ("run-1",)).fetchone()
    assert row is not None
    assert RunManifest.model_validate_json(row[0]) == manifest


async def test_save_run_overwrites_a_previous_manifest_for_the_same_run_id(
    store: SqliteStateStore, tmp_path: Path
) -> None:
    first = RunManifest(
        run_id="run-1",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        site_counts={"books_sandbox": 1},
        error_counts={},
        git_sha="aaaa",
    )
    second = first.model_copy(update={"site_counts": {"books_sandbox": 2}, "git_sha": "bbbb"})

    await store.save_run(first)
    await store.save_run(second)

    with sqlite3.connect(tmp_path / "state" / "pipeline.db") as conn:
        rows = conn.execute(
            "SELECT manifest_json FROM runs WHERE run_id = ?", ("run-1",)
        ).fetchall()
    assert len(rows) == 1
    assert RunManifest.model_validate_json(rows[0][0]) == second


async def test_get_site_state_raises_when_no_state_persisted_yet(store: SqliteStateStore) -> None:
    with pytest.raises(SiteStateNotFoundError):
        await store.get_site_state("books_sandbox")


async def test_put_site_state_then_get_site_state_roundtrips(store: SqliteStateStore) -> None:
    state = SiteState(
        site_id="books_sandbox",
        last_success_at=datetime.now(UTC),
        consecutive_failures=0,
        breaker_open=False,
    )

    await store.put_site_state(state)

    assert await store.get_site_state("books_sandbox") == state


async def test_put_site_state_overwrites_existing_state_for_the_same_site(
    store: SqliteStateStore,
) -> None:
    first = SiteState(
        site_id="books_sandbox", last_success_at=None, consecutive_failures=1, breaker_open=False
    )
    second = first.model_copy(update={"consecutive_failures": 2, "breaker_open": True})

    await store.put_site_state(first)
    await store.put_site_state(second)

    assert await store.get_site_state("books_sandbox") == second
