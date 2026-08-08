"""Tests for `pipeline.fetchers.circuit_breaker` — the per-domain circuit breaker (FR-10)."""

from pipeline.fetchers.circuit_breaker import DomainCircuitBreaker


class _FakeClock:
    """A controllable clock so cooldown tests don't need to actually sleep."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class TestIsOpen:
    def test_a_fresh_breaker_is_not_open_for_any_domain(self) -> None:
        breaker = DomainCircuitBreaker(fail_max=3, reset_timeout=60.0)

        assert breaker.is_open("example.invalid") is False

    def test_opens_after_fail_max_consecutive_failures(self) -> None:
        breaker = DomainCircuitBreaker(fail_max=3, reset_timeout=60.0)

        breaker.record_failure("example.invalid")
        breaker.record_failure("example.invalid")
        assert breaker.is_open("example.invalid") is False

        breaker.record_failure("example.invalid")
        assert breaker.is_open("example.invalid") is True

    def test_stays_closed_if_failures_stop_short_of_the_threshold(self) -> None:
        breaker = DomainCircuitBreaker(fail_max=3, reset_timeout=60.0)

        breaker.record_failure("example.invalid")
        breaker.record_failure("example.invalid")

        assert breaker.is_open("example.invalid") is False


class TestPerDomainIsolation:
    def test_failures_on_one_domain_do_not_affect_another(self) -> None:
        breaker = DomainCircuitBreaker(fail_max=2, reset_timeout=60.0)

        breaker.record_failure("down.invalid")
        breaker.record_failure("down.invalid")

        assert breaker.is_open("down.invalid") is True
        assert breaker.is_open("healthy.invalid") is False


class TestSuccessResetsTheStreak:
    def test_a_success_between_failures_resets_the_consecutive_count(self) -> None:
        breaker = DomainCircuitBreaker(fail_max=3, reset_timeout=60.0)

        breaker.record_failure("example.invalid")
        breaker.record_failure("example.invalid")
        breaker.record_success("example.invalid")
        breaker.record_failure("example.invalid")
        breaker.record_failure("example.invalid")

        assert breaker.is_open("example.invalid") is False

    def test_recording_success_on_an_untouched_domain_is_a_no_op(self) -> None:
        breaker = DomainCircuitBreaker(fail_max=1, reset_timeout=60.0)

        breaker.record_success("example.invalid")

        assert breaker.is_open("example.invalid") is False


class TestCooldownRecovery:
    def test_stays_open_before_the_cooldown_elapses(self) -> None:
        clock = _FakeClock()
        breaker = DomainCircuitBreaker(fail_max=1, reset_timeout=10.0, clock=clock)
        breaker.record_failure("example.invalid")

        clock.advance(5.0)

        assert breaker.is_open("example.invalid") is True

    def test_allows_a_trial_attempt_once_the_cooldown_elapses(self) -> None:
        clock = _FakeClock()
        breaker = DomainCircuitBreaker(fail_max=1, reset_timeout=10.0, clock=clock)
        breaker.record_failure("example.invalid")

        clock.advance(10.0)

        assert breaker.is_open("example.invalid") is False

    def test_a_successful_trial_closes_the_breaker_and_resets_its_streak(self) -> None:
        clock = _FakeClock()
        breaker = DomainCircuitBreaker(fail_max=2, reset_timeout=10.0, clock=clock)
        breaker.record_failure("example.invalid")
        breaker.record_failure("example.invalid")
        clock.advance(10.0)
        assert breaker.is_open("example.invalid") is False  # enters half-open

        breaker.record_success("example.invalid")

        assert breaker.is_open("example.invalid") is False
        # one failure alone must not reopen a fail_max=2 breaker unless the streak really reset
        breaker.record_failure("example.invalid")
        assert breaker.is_open("example.invalid") is False

    def test_a_failed_trial_reopens_the_breaker_for_a_fresh_cooldown(self) -> None:
        clock = _FakeClock()
        breaker = DomainCircuitBreaker(fail_max=1, reset_timeout=10.0, clock=clock)
        breaker.record_failure("example.invalid")
        clock.advance(10.0)
        assert breaker.is_open("example.invalid") is False  # enters half-open

        breaker.record_failure("example.invalid")

        assert breaker.is_open("example.invalid") is True
        clock.advance(9.0)
        assert breaker.is_open("example.invalid") is True
        clock.advance(1.0)
        assert breaker.is_open("example.invalid") is False
