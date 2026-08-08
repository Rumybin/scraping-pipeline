"""Composes retry (FR-9) and the per-domain circuit breaker (FR-10) around one fetch attempt.

Shared by both `HttpFetcher` and `BrowserFetcher` (ADR 0003) so escalating from `http` to
`browser` changes how a page is retrieved, not whether the pipeline's retry and circuit-breaking
guarantees apply — the alternative is duplicating this exact orchestration in each fetcher.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pipeline.core.exceptions import BrowserFetchError, CircuitOpenError, HttpFetchError
from pipeline.core.models import RawResponse
from pipeline.fetchers.circuit_breaker import DomainCircuitBreaker
from pipeline.fetchers.retry import ErrorClass, classify, fetch_with_retry

_RETRYABLE_FETCH_ERRORS = (HttpFetchError, BrowserFetchError)
_BREAKER_FAILURE_CLASSES = frozenset({ErrorClass.RATE_LIMITED, ErrorClass.SERVER_ERROR})


async def fetch_with_resilience(
    domain: str,
    attempt: Callable[[], Awaitable[RawResponse]],
    *,
    circuit_breaker: DomainCircuitBreaker,
    retry_sleep: Callable[[float], Awaitable[None]] | None = None,
) -> RawResponse:
    """Run `attempt` (one fetcher's full fetch: robots + rate limit + transport) with retry
    (FR-9) and circuit-breaker bookkeeping (FR-10) for `domain`.

    Raises `CircuitOpenError` immediately, without calling `attempt` at all, if `domain`'s
    breaker is open. Otherwise retries `attempt` per FR-9 and reports the final outcome to the
    breaker: a `429`/`5xx` that survives retries, or an exhausted `HttpFetchError`/
    `BrowserFetchError`, counts as a failure; anything else counts as a success.
    """
    if circuit_breaker.is_open(domain):
        raise CircuitOpenError(f"circuit open for {domain!r}, skipping this fetch")

    try:
        raw = await fetch_with_retry(attempt, sleep=retry_sleep)
    except _RETRYABLE_FETCH_ERRORS:
        circuit_breaker.record_failure(domain)
        raise

    if classify(raw, None) in _BREAKER_FAILURE_CLASSES:
        circuit_breaker.record_failure(domain)
    else:
        circuit_breaker.record_success(domain)
    return raw
