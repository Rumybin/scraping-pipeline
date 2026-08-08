"""Per-domain circuit breaker (FR-10): after N consecutive failures, a domain is skipped until
its cooldown elapses, while every other domain keeps running.

`pybreaker` is the stack's nominal choice for this, but its shape doesn't fit how this module is
used. Its async support (`call_async`) requires `tornado`, which is not a declared dependency of
this project (Hard Rule 8). And its synchronous `.call()` API couples the open/half-open decision
to *running* the guarded operation itself — but here the real async fetch happens in
`HttpFetcher`, which only reports its outcome back to this breaker afterward. Forcing that
"check first, report later" shape through `.call()` would let a same-tick "is it open?" check
silently consume the one half-open trial with a fake no-op before the real fetch ever runs. A
small hand-rolled state machine avoids that mismatch and needs no extra dependency.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class _State(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class _DomainState:
    state: _State = _State.CLOSED
    consecutive_failures: int = 0
    opened_at: float | None = None


class DomainCircuitBreaker:
    """Tracks one closed/open/half-open state machine per domain (FR-10)."""

    def __init__(
        self,
        *,
        fail_max: int = 5,
        reset_timeout: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fail_max = fail_max
        self._reset_timeout = reset_timeout
        self._clock = clock
        self._domains: dict[str, _DomainState] = {}

    def is_open(self, domain: str) -> bool:
        """Return whether `domain` should currently be skipped.

        A domain whose cooldown has elapsed is lazily moved from `open` to `half_open` here, and
        `half_open` returns `False` — one trial attempt is allowed through. Calling this does not
        itself consume that trial; only `record_success`/`record_failure` do.
        """
        entry = self._domains.get(domain)
        if entry is None or entry.state != _State.OPEN:
            return False
        assert entry.opened_at is not None
        if self._clock() - entry.opened_at >= self._reset_timeout:
            entry.state = _State.HALF_OPEN
            return False
        return True

    def record_success(self, domain: str) -> None:
        """Report a successful fetch against `domain`: closes the breaker, resets its streak."""
        entry = self._get_or_create(domain)
        entry.consecutive_failures = 0
        entry.state = _State.CLOSED
        entry.opened_at = None

    def record_failure(self, domain: str) -> None:
        """Report a failed fetch against `domain`.

        Trips the breaker open once `fail_max` consecutive failures accumulate. A failed
        half-open trial reopens it immediately, starting a fresh full cooldown.
        """
        entry = self._get_or_create(domain)
        entry.consecutive_failures += 1
        if entry.state == _State.HALF_OPEN or entry.consecutive_failures >= self._fail_max:
            entry.state = _State.OPEN
            entry.opened_at = self._clock()

    def _get_or_create(self, domain: str) -> _DomainState:
        if domain not in self._domains:
            self._domains[domain] = _DomainState()
        return self._domains[domain]
