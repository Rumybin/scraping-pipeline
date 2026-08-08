"""Per-error-class retry with backoff for both fetch strategies (FR-9).

A single fetch attempt is classified into one of four outcomes, each retried differently: `429`
honors the server's own `Retry-After` header, `5xx` backs off exponentially with jitter, a
network/timeout failure (`HttpFetchError` or `BrowserFetchError` — the same policy serves both
`HttpFetcher` and `BrowserFetcher`, ADR 0003) retries fast, and any other outcome (2xx/3xx, or a
4xx other than 429) is never retried.

This is a hand-rolled loop rather than a single `tenacity` decorator: honoring a *dynamic*
`Retry-After` value read from the most recent response, while also giving each error class its
own attempt budget and wait formula, is clearer as one explicit loop than as nested
predicate/wait callables. `tenacity`'s wait-strategy helpers are still the right tool for the
formulas themselves (see `_wait_seconds`).
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum

from pipeline.core.exceptions import BrowserFetchError, HttpFetchError
from pipeline.core.models import RawResponse

_RETRYABLE_EXCEPTION_TYPES = (HttpFetchError, BrowserFetchError)

_MAX_RETRY_AFTER_SECONDS = 60.0
_SERVER_ERROR_BASE_SECONDS = 1.0
_SERVER_ERROR_MAX_SECONDS = 30.0
_TIMEOUT_BASE_SECONDS = 0.2
_TIMEOUT_MAX_SECONDS = 2.0


class ErrorClass(StrEnum):
    """The four retry classes a fetch attempt's outcome is sorted into (FR-9)."""

    RATE_LIMITED = "rate_limited"  # HTTP 429
    SERVER_ERROR = "server_error"  # HTTP 5xx
    TIMEOUT_OR_NETWORK = "timeout_or_network"  # HttpFetchError or BrowserFetchError
    NOT_RETRYABLE = "not_retryable"  # 2xx/3xx, other 4xx, or an unrelated exception


RETRY_ATTEMPTS_PER_CLASS: dict[ErrorClass, int] = {
    ErrorClass.RATE_LIMITED: 3,
    ErrorClass.SERVER_ERROR: 3,
    ErrorClass.TIMEOUT_OR_NETWORK: 4,
}

_RETRYABLE = frozenset(RETRY_ATTEMPTS_PER_CLASS)


def classify(result: RawResponse | None, exception: BaseException | None) -> ErrorClass:
    """Classify one fetch attempt's outcome into one of FR-9's four error classes."""
    if exception is not None:
        return (
            ErrorClass.TIMEOUT_OR_NETWORK
            if isinstance(exception, _RETRYABLE_EXCEPTION_TYPES)
            else ErrorClass.NOT_RETRYABLE
        )
    assert result is not None, "classify() requires a result when no exception was raised"
    if result.status_code == 429:
        return ErrorClass.RATE_LIMITED
    if 500 <= result.status_code < 600:
        return ErrorClass.SERVER_ERROR
    return ErrorClass.NOT_RETRYABLE


def parse_retry_after(raw: RawResponse) -> float | None:
    """Parse `raw`'s `Retry-After` header into seconds, clamped to a sane maximum.

    Returns `None` if the header is absent or does not match either of its two valid forms
    (an integer delay in seconds, or an HTTP-date to wait until).
    """
    header = next((v for k, v in raw.headers.items() if k.lower() == "retry-after"), None)
    if not header:
        return None
    header = header.strip()
    if header.isdigit():
        return min(float(header), _MAX_RETRY_AFTER_SECONDS)
    try:
        target = parsedate_to_datetime(header)
    except (TypeError, ValueError, IndexError):
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    delta = (target - datetime.now(UTC)).total_seconds()
    return max(0.0, min(delta, _MAX_RETRY_AFTER_SECONDS))


def _wait_seconds(
    error_class: ErrorClass,
    attempt_number: int,
    last_result: RawResponse | None,
    rng: random.Random,
) -> float:
    if error_class == ErrorClass.RATE_LIMITED:
        assert last_result is not None
        header_wait = parse_retry_after(last_result)
        if header_wait is not None:
            return header_wait
        return min(
            _SERVER_ERROR_BASE_SECONDS * (2.0 ** (attempt_number - 1)), _MAX_RETRY_AFTER_SECONDS
        )
    if error_class == ErrorClass.SERVER_ERROR:
        base = min(
            _SERVER_ERROR_BASE_SECONDS * (2.0 ** (attempt_number - 1)), _SERVER_ERROR_MAX_SECONDS
        )
        return base + rng.uniform(0, base * 0.25)
    if error_class == ErrorClass.TIMEOUT_OR_NETWORK:
        base = min(_TIMEOUT_BASE_SECONDS * (2.0 ** (attempt_number - 1)), _TIMEOUT_MAX_SECONDS)
        return base + rng.uniform(0, base * 0.5)
    return 0.0


async def fetch_with_retry(
    attempt: Callable[[], Awaitable[RawResponse]],
    *,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    rng: random.Random | None = None,
) -> RawResponse:
    """Call `attempt` (one fetch) repeatedly per FR-9's per-error-class retry policy.

    Returns the last `RawResponse` obtained, even if it is still a `429`/`5xx` after retries are
    exhausted — a final bad response is data, not an error, matching `HttpFetcher.fetch`'s
    existing contract (raw responses are always persistable, Hard Rule 5). Raises
    `HttpFetchError`/`BrowserFetchError` if every attempt at a network/timeout failure is
    exhausted. Any exception `attempt` raises that is not one of those two (e.g.
    `RobotsDisallowedError`) is not this function's concern to retry and propagates immediately,
    unmodified, after a single attempt.
    """
    sleep = sleep or _default_sleep
    rng = rng or random.Random()
    attempt_number = 0
    last_result: RawResponse | None = None
    last_exception: HttpFetchError | BrowserFetchError | None = None

    while True:
        attempt_number += 1
        try:
            last_result = await attempt()
        except _RETRYABLE_EXCEPTION_TYPES as exc:
            last_exception = exc
            last_result = None
        else:
            last_exception = None

        error_class = classify(last_result, last_exception)
        if error_class not in _RETRYABLE or attempt_number >= RETRY_ATTEMPTS_PER_CLASS[error_class]:
            break
        await sleep(_wait_seconds(error_class, attempt_number, last_result, rng))

    if last_exception is not None:
        raise last_exception
    assert last_result is not None
    return last_result


async def _default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)
