"""Tests for `pipeline.fetchers.retry` — per-error-class retry with backoff (FR-9)."""

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest

from pipeline.core.exceptions import HttpFetchError, RobotsDisallowedError
from pipeline.core.models import RawResponse
from pipeline.fetchers.retry import (
    RETRY_ATTEMPTS_PER_CLASS,
    ErrorClass,
    classify,
    fetch_with_retry,
    parse_retry_after,
)


def _raw(status_code: int, headers: dict[str, str] | None = None) -> RawResponse:
    return RawResponse(
        url="https://example.invalid/page",
        status_code=status_code,
        headers=headers or {},
        body=b"",
        fetched_at=datetime.now(UTC),
        content_type="text/html",
    )


class _SleepSpy:
    """Records requested sleep durations instead of actually waiting."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class TestClassify:
    def test_success_is_not_retryable(self) -> None:
        assert classify(_raw(200), None) == ErrorClass.NOT_RETRYABLE

    def test_ordinary_4xx_is_not_retryable(self) -> None:
        assert classify(_raw(404), None) == ErrorClass.NOT_RETRYABLE

    def test_429_is_rate_limited(self) -> None:
        assert classify(_raw(429), None) == ErrorClass.RATE_LIMITED

    @pytest.mark.parametrize("status_code", [500, 503, 599])
    def test_5xx_is_server_error(self, status_code: int) -> None:
        assert classify(_raw(status_code), None) == ErrorClass.SERVER_ERROR

    def test_http_fetch_error_is_timeout_or_network(self) -> None:
        assert classify(None, HttpFetchError("boom")) == ErrorClass.TIMEOUT_OR_NETWORK

    def test_other_exception_is_not_retryable(self) -> None:
        assert classify(None, RobotsDisallowedError("nope")) == ErrorClass.NOT_RETRYABLE


class TestParseRetryAfter:
    def test_missing_header_returns_none(self) -> None:
        assert parse_retry_after(_raw(429)) is None

    def test_numeric_seconds_header(self) -> None:
        assert parse_retry_after(_raw(429, {"Retry-After": "5"})) == 5.0

    def test_http_date_header_converts_to_seconds_from_now(self) -> None:
        target = datetime.now(UTC) + timedelta(seconds=10)
        header = format_datetime(target, usegmt=True)

        seconds = parse_retry_after(_raw(429, {"Retry-After": header}))

        assert seconds is not None
        assert 8.0 <= seconds <= 11.0

    def test_malformed_header_returns_none(self) -> None:
        assert parse_retry_after(_raw(429, {"Retry-After": "not-a-value"})) is None

    def test_large_value_is_clamped_to_a_maximum(self) -> None:
        assert parse_retry_after(_raw(429, {"Retry-After": "600"})) == 60.0


class TestFetchWithRetry:
    async def test_succeeds_on_first_attempt_without_sleeping(self) -> None:
        sleep = _SleepSpy()
        calls = 0

        async def attempt() -> RawResponse:
            nonlocal calls
            calls += 1
            return _raw(200)

        result = await fetch_with_retry(attempt, sleep=sleep)

        assert result.status_code == 200
        assert calls == 1
        assert sleep.calls == []

    async def test_retries_429_honoring_retry_after_header(self) -> None:
        sleep = _SleepSpy()
        responses = [_raw(429, {"Retry-After": "7"}), _raw(200)]

        async def attempt() -> RawResponse:
            return responses.pop(0)

        result = await fetch_with_retry(attempt, sleep=sleep)

        assert result.status_code == 200
        assert sleep.calls == [7.0]

    async def test_retries_429_without_header_using_computed_backoff(self) -> None:
        sleep = _SleepSpy()
        responses = [_raw(429), _raw(200)]

        async def attempt() -> RawResponse:
            return responses.pop(0)

        result = await fetch_with_retry(attempt, sleep=sleep)

        assert result.status_code == 200
        assert len(sleep.calls) == 1
        assert sleep.calls[0] > 0

    async def test_exhausts_server_error_retries_and_returns_the_last_response(self) -> None:
        sleep = _SleepSpy()
        calls = 0

        async def attempt() -> RawResponse:
            nonlocal calls
            calls += 1
            return _raw(503)

        result = await fetch_with_retry(attempt, sleep=sleep)

        assert result.status_code == 503
        assert calls == RETRY_ATTEMPTS_PER_CLASS[ErrorClass.SERVER_ERROR]
        assert len(sleep.calls) == calls - 1

    async def test_does_not_retry_an_ordinary_4xx(self) -> None:
        sleep = _SleepSpy()
        calls = 0

        async def attempt() -> RawResponse:
            nonlocal calls
            calls += 1
            return _raw(404)

        result = await fetch_with_retry(attempt, sleep=sleep)

        assert result.status_code == 404
        assert calls == 1
        assert sleep.calls == []

    async def test_exhausts_timeout_retries_and_reraises_the_last_exception(self) -> None:
        sleep = _SleepSpy()
        calls = 0

        async def attempt() -> RawResponse:
            nonlocal calls
            calls += 1
            raise HttpFetchError(f"timeout #{calls}")

        with pytest.raises(HttpFetchError, match="timeout #4"):
            await fetch_with_retry(attempt, sleep=sleep)

        assert calls == RETRY_ATTEMPTS_PER_CLASS[ErrorClass.TIMEOUT_OR_NETWORK]
        assert len(sleep.calls) == calls - 1

    async def test_recovers_after_a_transient_network_failure(self) -> None:
        sleep = _SleepSpy()
        outcomes: list[RawResponse | Exception] = [HttpFetchError("blip"), _raw(200)]

        async def attempt() -> RawResponse:
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        result = await fetch_with_retry(attempt, sleep=sleep)

        assert result.status_code == 200
        assert len(sleep.calls) == 1
        assert sleep.calls[0] > 0

    async def test_a_non_retryable_exception_propagates_immediately(self) -> None:
        sleep = _SleepSpy()
        calls = 0

        async def attempt() -> RawResponse:
            nonlocal calls
            calls += 1
            raise RobotsDisallowedError("blocked")

        with pytest.raises(RobotsDisallowedError):
            await fetch_with_retry(attempt, sleep=sleep)

        assert calls == 1
        assert sleep.calls == []
