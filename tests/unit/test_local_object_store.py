"""Tests for `pipeline.backends.local.LocalObjectStore`, the filesystem `ObjectStore`."""

from pathlib import Path

import pytest

from pipeline.backends.local import LocalObjectStore
from pipeline.core.exceptions import ObjectNotFoundError


@pytest.fixture
def store(tmp_path: Path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "object-store")


async def test_put_then_get_roundtrips_bytes(store: LocalObjectStore) -> None:
    await store.put("raw/site=books/file.html.gz", b"payload", content_type="application/gzip")

    assert await store.get("raw/site=books/file.html.gz") == b"payload"


async def test_exists_is_false_before_put_and_true_after(store: LocalObjectStore) -> None:
    assert await store.exists("staging/part-1.parquet") is False

    await store.put("staging/part-1.parquet", b"data", content_type="application/octet-stream")

    assert await store.exists("staging/part-1.parquet") is True


async def test_get_missing_key_raises_object_not_found_error(store: LocalObjectStore) -> None:
    with pytest.raises(ObjectNotFoundError):
        await store.get("does/not/exist")


async def test_put_creates_nested_parent_directories(
    store: LocalObjectStore, tmp_path: Path
) -> None:
    key = "raw/site=books_sandbox/dt=2026-08-08/run=abc123/deadbeef.html.gz"

    await store.put(key, b"gzipped-html", content_type="application/gzip")

    assert (tmp_path / "object-store" / key).read_bytes() == b"gzipped-html"


async def test_list_yields_only_keys_matching_the_prefix(store: LocalObjectStore) -> None:
    await store.put("raw/site=books/a.html.gz", b"a", content_type="application/gzip")
    await store.put("raw/site=books/b.html.gz", b"b", content_type="application/gzip")
    await store.put("staging/part-1.parquet", b"c", content_type="application/octet-stream")

    keys = {key async for key in store.list("raw/site=books/")}

    assert keys == {"raw/site=books/a.html.gz", "raw/site=books/b.html.gz"}


async def test_list_returns_nothing_for_an_unmatched_prefix(store: LocalObjectStore) -> None:
    await store.put("raw/site=books/a.html.gz", b"a", content_type="application/gzip")

    keys = [key async for key in store.list("curated/")]

    assert keys == []


async def test_list_on_a_store_with_nothing_written_yet_returns_nothing(
    store: LocalObjectStore,
) -> None:
    keys = [key async for key in store.list("")]

    assert keys == []
