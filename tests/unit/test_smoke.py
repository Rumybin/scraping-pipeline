"""Smoke test confirming the package installs and the test suite is wired up."""

import pipeline


def test_pipeline_package_is_importable() -> None:
    assert pipeline.__name__ == "pipeline"
