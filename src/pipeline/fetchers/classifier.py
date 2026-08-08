"""Classifies a fetched response for signals `HttpFetcher.fetch` cannot see from status code
alone: a soft block (PRD §4.2 scenario 5) or an empty/JS-shell page.

This module only detects — it does not decide policy. Wiring a `SOFT_BLOCK`/`EMPTY_OR_JS_SHELL`
classification into an actual retry, alert, or http→browser escalation (FR-2) is a later phase's
concern (ADR 0003); this classifier's job is to make that signal observable in the first place, so
the pipeline stops silently recording "success" on a page that is actually a challenge screen or a
blank client-rendered shell.
"""

from __future__ import annotations

from enum import StrEnum

from selectolax.parser import HTMLParser

from pipeline.core.models import RawResponse

_SOFT_BLOCK_MARKERS = (
    "just a moment",
    "checking your browser",
    "attention required",
    "verify you are human",
    "are you a robot",
    "captcha",
    "access denied",
    "please enable javascript and cookies",
    "unusual traffic",
)

_MIN_CONTENT_TEXT_LENGTH = 50


class ResponseClassification(StrEnum):
    """What a fetched response actually contains, beyond its HTTP status code."""

    OK = "ok"
    SOFT_BLOCK = "soft_block"
    EMPTY_OR_JS_SHELL = "empty_or_js_shell"


def classify_response(raw: RawResponse) -> ResponseClassification:
    """Classify `raw`'s body as ordinary content, a soft block, or an empty/JS-shell page.

    Only meaningful for HTML 2xx responses: a non-2xx status is always `OK` here, since the
    retry/circuit-breaker layer already owns that failure signal, and a non-HTML content type is
    always `OK` since the soft-block/JS-shell distinction only applies to HTML pages.
    """
    if not (200 <= raw.status_code < 300):
        return ResponseClassification.OK
    if raw.content_type and "html" not in raw.content_type.lower():
        return ResponseClassification.OK

    # `<script>`/`<style>` content must not count as visible text — a JS-shell page's whole
    # defining trait is that its hydration script is often longer than the "content" it renders,
    # which would otherwise defeat the length check below on exactly the pages it exists to catch.
    tree = HTMLParser(raw.body)
    tree.strip_tags(["script", "style"])
    body_node = tree.body
    visible_text = body_node.text(deep=True, strip=True) if body_node is not None else ""

    lowered = visible_text.lower()
    if any(marker in lowered for marker in _SOFT_BLOCK_MARKERS):
        return ResponseClassification.SOFT_BLOCK

    if len(visible_text) < _MIN_CONTENT_TEXT_LENGTH:
        return ResponseClassification.EMPTY_OR_JS_SHELL

    return ResponseClassification.OK
