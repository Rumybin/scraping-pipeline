"""Tests for `pipeline.quality.report` — the DQ report HTML renderer (FR-16)."""

from datetime import UTC, datetime

from pipeline.quality.dq_engine import DimensionResult, DqReport
from pipeline.quality.report import render_html


def _report(*, gate_status: str = "pass") -> DqReport:
    return DqReport(
        run_id="run-1",
        site_id="books_sandbox",
        generated_at=datetime.now(UTC),
        total_records=20,
        dimensions=[
            DimensionResult(
                dimension="completeness",
                score=1.0,
                warn_threshold=0.99,
                fail_threshold=0.95,
                status="pass",
                detail="mean per-record completeness across 20 records",
            ),
        ],
        gate_status=gate_status,  # type: ignore[arg-type]  # test passes a plain str for the Literal
    )


def test_render_html_includes_run_and_site_identifiers() -> None:
    html = render_html(_report())

    assert "run-1" in html
    assert "books_sandbox" in html


def test_render_html_includes_each_dimension_row() -> None:
    html = render_html(_report())

    assert "completeness" in html
    assert "mean per-record completeness across 20 records" in html


def test_render_html_reflects_failing_gate_status() -> None:
    html = render_html(_report(gate_status="fail"))

    assert "FAIL" in html


def test_render_html_is_a_complete_document() -> None:
    html = render_html(_report())

    assert html.strip().startswith("<!doctype html>")
    assert "</html>" in html
