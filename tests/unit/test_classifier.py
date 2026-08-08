"""Tests for `pipeline.fetchers.classifier` — soft-block / JS-shell detection (FR-2, §4.2)."""

from datetime import UTC, datetime

import httpx
from tests.hostile_server.app import create_app

from pipeline.core.models import RawResponse
from pipeline.fetchers.classifier import ResponseClassification, classify_response


def _raw(
    body: str, *, status_code: int = 200, content_type: str | None = "text/html"
) -> RawResponse:
    return RawResponse(
        url="https://example.invalid/page",
        status_code=status_code,
        headers={},
        body=body.encode("utf-8"),
        fetched_at=datetime.now(UTC),
        content_type=content_type,
    )


class TestOrdinaryContent:
    def test_a_normal_content_page_is_classified_ok(self) -> None:
        raw = _raw(
            "<html><body><article><h1>A Light in the Attic</h1>"
            "<p>" + "A genuine book description. " * 10 + "</p></article></body></html>"
        )

        assert classify_response(raw) == ResponseClassification.OK

    def test_a_non_2xx_status_is_always_ok_regardless_of_body(self) -> None:
        raw = _raw("<html><body><h1>Just a moment...</h1></body></html>", status_code=503)

        assert classify_response(raw) == ResponseClassification.OK

    def test_a_non_html_content_type_is_always_ok(self) -> None:
        raw = _raw('{"unexpected": true}', content_type="application/json")

        assert classify_response(raw) == ResponseClassification.OK

    def test_a_missing_content_type_falls_back_to_inspecting_the_body(self) -> None:
        raw = _raw("<html><body><h1>Just a moment...</h1></body></html>", content_type=None)

        assert classify_response(raw) == ResponseClassification.SOFT_BLOCK


class TestSoftBlock:
    def test_a_just_a_moment_challenge_page_is_a_soft_block(self) -> None:
        raw = _raw(
            "<!doctype html><html><body>\n"
            "<h1>Just a moment...</h1>\n"
            "<p>Checking your browser before accessing this site.</p>\n"
            "</body></html>"
        )

        assert classify_response(raw) == ResponseClassification.SOFT_BLOCK

    def test_a_captcha_page_is_a_soft_block(self) -> None:
        raw = _raw("<html><body><h1>Please complete the CAPTCHA below</h1></body></html>")

        assert classify_response(raw) == ResponseClassification.SOFT_BLOCK

    def test_detection_is_case_insensitive(self) -> None:
        raw = _raw("<html><body><h1>ARE YOU A ROBOT?</h1></body></html>")

        assert classify_response(raw) == ResponseClassification.SOFT_BLOCK


class TestAgainstTheHostileServer:
    async def test_the_challenge_scenario_endpoint_is_classified_as_a_soft_block(self) -> None:
        app = create_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://hostile.invalid") as c:
            response = await c.get("/challenge")

        raw = RawResponse(
            url="http://hostile.invalid/challenge",
            status_code=response.status_code,
            headers=dict(response.headers),
            body=response.content,
            fetched_at=datetime.now(UTC),
            content_type=response.headers.get("content-type"),
        )

        assert classify_response(raw) == ResponseClassification.SOFT_BLOCK


class TestEmptyOrJsShell:
    def test_a_bare_react_root_div_is_an_empty_js_shell(self) -> None:
        raw = _raw('<html><body><div id="root"></div></body></html>')

        assert classify_response(raw) == ResponseClassification.EMPTY_OR_JS_SHELL

    def test_a_body_with_only_whitespace_is_an_empty_js_shell(self) -> None:
        raw = _raw("<html><body>   \n   </body></html>")

        assert classify_response(raw) == ResponseClassification.EMPTY_OR_JS_SHELL

    def test_a_response_with_no_body_tag_at_all_is_an_empty_js_shell(self) -> None:
        raw = _raw("<html></html>")

        assert classify_response(raw) == ResponseClassification.EMPTY_OR_JS_SHELL
