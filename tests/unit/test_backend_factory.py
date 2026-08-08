"""Tests for the `PIPELINE_BACKEND` factory in `pipeline.backends`."""

from pathlib import Path

import pytest

from pipeline.backends import build_backend_set
from pipeline.backends.local import LocalObjectStore, SqliteStateStore
from pipeline.core.config import Backend, Settings


def test_local_backend_resolves_to_filesystem_and_sqlite(tmp_path: Path) -> None:
    settings = Settings(pipeline_backend=Backend.LOCAL)

    backend_set = build_backend_set(settings, local_root=tmp_path / "data")

    assert isinstance(backend_set.object_store, LocalObjectStore)
    assert isinstance(backend_set.state_store, SqliteStateStore)
    assert (tmp_path / "data").exists()
    assert (tmp_path / "data" / "state.db").exists()


@pytest.mark.parametrize("backend", [Backend.FREE, Backend.AWS])
def test_unimplemented_backend_raises_not_implemented_error(
    backend: Backend, tmp_path: Path
) -> None:
    settings = Settings(pipeline_backend=backend)

    with pytest.raises(NotImplementedError, match=backend.value):
        build_backend_set(settings, local_root=tmp_path / "data")
