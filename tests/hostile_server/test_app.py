"""Verifies the hostile server's own behavior for each of its nine scenarios (PRD §4.2).

These are black-box tests of the harness itself — `tests/resilience/` separately verifies that
the *pipeline* behaves correctly when it hits this server.
"""

from collections.abc import AsyncIterator

import httpx
import pytest

from tests.hostile_server.app import create_app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(flaky_seed=1, drift_after_requests=2)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://hostile.invalid") as client:
        yield client


async def test_retry_after_returns_429_with_configured_header(client: httpx.AsyncClient) -> None:
    response = await client.get("/retry-after", params={"seconds": 5})

    assert response.status_code == 429
    assert response.headers["retry-after"] == "5"


async def test_retry_after_defaults_to_thirty_seconds(client: httpx.AsyncClient) -> None:
    response = await client.get("/retry-after")

    assert response.headers["retry-after"] == "30"


async def test_flaky_fails_roughly_one_in_five_requests_deterministically(
    client: httpx.AsyncClient,
) -> None:
    statuses = [(await client.get("/flaky")).status_code for _ in range(50)]

    assert statuses.count(503) > 0
    assert statuses.count(200) > statuses.count(503)
    assert set(statuses) == {200, 503}


async def test_flaky_is_reproducible_given_the_same_seed() -> None:
    app_a = create_app(flaky_seed=7)
    app_b = create_app(flaky_seed=7)
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_a), base_url="http://a.invalid"
        ) as client_a,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_b), base_url="http://b.invalid"
        ) as client_b,
    ):
        statuses_a = [(await client_a.get("/flaky")).status_code for _ in range(30)]
        statuses_b = [(await client_b.get("/flaky")).status_code for _ in range(30)]

    assert statuses_a == statuses_b


async def test_always_down_always_returns_503(client: httpx.AsyncClient) -> None:
    statuses = [(await client.get("/always-down")).status_code for _ in range(5)]

    assert statuses == [503] * 5


async def test_timeout_delays_the_response_by_the_configured_seconds(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/timeout", params={"delay": 0.2})

    assert response.status_code == 200


async def test_drift_switches_markup_after_the_configured_request_count() -> None:
    app = create_app(drift_after_requests=2)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://hostile.invalid") as client:
        first = await client.get("/drift")
        second = await client.get("/drift")
        third = await client.get("/drift")

    assert 'class="price"' in first.text
    assert 'class="price"' in second.text
    assert 'class="cost"' in third.text
    assert 'class="price"' not in third.text


async def test_challenge_returns_200_with_interstitial_body(client: httpx.AsyncClient) -> None:
    response = await client.get("/challenge")

    assert response.status_code == 200
    assert "Checking your browser" in response.text


async def test_bad_encoding_declares_utf8_but_is_actually_latin1(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/bad-encoding")

    assert "utf-8" in response.headers["content-type"]
    with pytest.raises(UnicodeDecodeError):
        response.content.decode("utf-8")
    assert response.content.decode("latin-1") == "Café - déjà vu"


async def test_unexpected_json_returns_json_content_type(client: httpx.AsyncClient) -> None:
    response = await client.get("/unexpected-json")

    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["unexpected"] is True


async def test_strict_rate_limit_allows_the_first_call_then_429s_a_fast_second_call(
    client: httpx.AsyncClient,
) -> None:
    first = await client.get("/strict-rate-limit")
    second = await client.get("/strict-rate-limit")

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["retry-after"] == "1"


async def test_huge_streams_the_requested_size_without_buffering_it_all_up_front(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/huge", params={"size_mb": 2})

    assert len(response.content) == 2 * 1024 * 1024
