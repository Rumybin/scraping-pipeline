"""Tests for `pipeline.fetchers.resilience.fetch_with_resilience` — the shared retry +
circuit-breaker orchestration both `HttpFetcher` and `BrowserFetcher` compose (ADR 0003).
"""

from datetime import UTC, datetime

import pytest

from pipeline.core.exceptions import BrowserFetchError, CircuitOpenError, HttpFetchError
from pipeline.core.models import RawResponse
from pipeline.fetchers.circuit_breaker import DomainCircuitBreaker
from pipeline.fetchers.resilience import fetch_with_resilience


def _raw(status_code: int) -> RawResponse:
    return RawResponse(
        url="https://example.invalid/page",
        status_code=status_code,
        headers={},
        body=b"",
        fetched_at=datetime.now(UTC),
        content_type="text/html",
    )


async def _no_sleep(seconds: float) -> None:
    pass


async def test_raises_circuit_open_error_without_calling_attempt_when_open() -> None:
    breaker = DomainCircuitBreaker(fail_max=1, reset_timeout=60.0)
    breaker.record_failure("example.invalid")
    calls = 0

    async def attempt() -> RawResponse:
        nonlocal calls
        calls += 1
        return _raw(200)

    with pytest.raises(CircuitOpenError):
        await fetch_with_resilience(
            "example.invalid", attempt, circuit_breaker=breaker, retry_sleep=_no_sleep
        )

    assert calls == 0


async def test_a_successful_fetch_records_a_breaker_success() -> None:
    breaker = DomainCircuitBreaker(fail_max=1, reset_timeout=60.0)

    async def attempt() -> RawResponse:
        return _raw(200)

    raw = await fetch_with_resilience(
        "example.invalid", attempt, circuit_breaker=breaker, retry_sleep=_no_sleep
    )

    assert raw.status_code == 200
    assert breaker.is_open("example.invalid") is False


async def test_exhausted_server_error_records_a_breaker_failure() -> None:
    breaker = DomainCircuitBreaker(fail_max=1, reset_timeout=60.0)

    async def attempt() -> RawResponse:
        return _raw(503)

    raw = await fetch_with_resilience(
        "example.invalid", attempt, circuit_breaker=breaker, retry_sleep=_no_sleep
    )

    assert raw.status_code == 503
    assert breaker.is_open("example.invalid") is True


async def test_exhausted_http_fetch_error_records_a_breaker_failure_and_reraises() -> None:
    breaker = DomainCircuitBreaker(fail_max=1, reset_timeout=60.0)

    async def attempt() -> RawResponse:
        raise HttpFetchError("connection refused")

    with pytest.raises(HttpFetchError):
        await fetch_with_resilience(
            "example.invalid", attempt, circuit_breaker=breaker, retry_sleep=_no_sleep
        )

    assert breaker.is_open("example.invalid") is True


async def test_exhausted_browser_fetch_error_records_a_breaker_failure_and_reraises() -> None:
    breaker = DomainCircuitBreaker(fail_max=1, reset_timeout=60.0)

    async def attempt() -> RawResponse:
        raise BrowserFetchError("navigation timed out")

    with pytest.raises(BrowserFetchError):
        await fetch_with_resilience(
            "example.invalid", attempt, circuit_breaker=breaker, retry_sleep=_no_sleep
        )

    assert breaker.is_open("example.invalid") is True


async def test_an_ordinary_4xx_does_not_trip_the_breaker() -> None:
    breaker = DomainCircuitBreaker(fail_max=1, reset_timeout=60.0)

    async def attempt() -> RawResponse:
        return _raw(404)

    raw = await fetch_with_resilience(
        "example.invalid", attempt, circuit_breaker=breaker, retry_sleep=_no_sleep
    )

    assert raw.status_code == 404
    assert breaker.is_open("example.invalid") is False
