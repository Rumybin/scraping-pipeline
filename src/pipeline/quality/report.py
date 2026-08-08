"""Renders a `DqReport` to a single self-contained static HTML page (FR-16)."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from pipeline.quality.dq_engine import DqReport

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_TEMPLATE_NAME = "dq_report.html.j2"

_environment = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html"]),
)


def render_html(report: DqReport) -> str:
    """Render `report` to a single HTML page suitable for publishing as-is (FR-16)."""
    template = _environment.get_template(_TEMPLATE_NAME)
    return template.render(report=report)
