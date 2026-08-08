"""Tests for the per-domain token-bucket rate limiter in `pipeline.fetchers.rate_limiter`.

A fake monotonic clock is injected so these tests never sleep in wall-clock time; `asyncio.sleep`
is patched to advance the fake clock instead of actually waiting.
"""

from unittest.mock import AsyncMock

import pytest

from pipeline.core.models import RateLimitConfig
from pipeline.fetchers.rate_limiter import RateLimiter, TokenBucket


class FakeClock:
    """A controllable monotonic clock: advances only when `advance()` is called."""

    def __init__(self) -> None:
        self._now = 0.0

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(autouse=True)
def _patch_sleep(monkeypatch: pytest.MonkeyPatch, fake_clock: FakeClock) -> AsyncMock:
    async def _fake_sleep(seconds: float) -> None:
        fake_clock.advance(seconds)

    mock_sleep = AsyncMock(side_effect=_fake_sleep)
    monkeypatch.setattr("pipeline.fetchers.rate_limiter.asyncio.sleep", mock_sleep)
    return mock_sleep


async def test_burst_capacity_requests_do_not_sleep(
    fake_clock: FakeClock, _patch_sleep: AsyncMock
) -> None:
    bucket = TokenBucket(rate=1.0, capacity=3, clock=fake_clock)

    await bucket.acquire()
    await bucket.acquire()
    await bucket.acquire()

    _patch_sleep.assert_not_called()


async def test_exceeding_burst_waits_for_a_token_to_refill(
    fake_clock: FakeClock, _patch_sleep: AsyncMock
) -> None:
    bucket = TokenBucket(rate=2.0, capacity=1, clock=fake_clock)

    await bucket.acquire()
    await bucket.acquire()

    _patch_sleep.assert_awaited_once()
    (waited_seconds,) = _patch_sleep.await_args.args
    assert waited_seconds == pytest.approx(0.5)


async def test_rate_limiter_gives_each_domain_an_independent_bucket(
    fake_clock: FakeClock, _patch_sleep: AsyncMock
) -> None:
    limiter = RateLimiter(clock=fake_clock)
    config = RateLimitConfig(rps=1.0, burst=1)

    await limiter.acquire("a.example", config)
    await limiter.acquire("b.example", config)

    _patch_sleep.assert_not_called()


async def test_rate_limiter_reuses_the_same_bucket_for_repeated_calls_on_one_domain(
    fake_clock: FakeClock, _patch_sleep: AsyncMock
) -> None:
    limiter = RateLimiter(clock=fake_clock)
    config = RateLimitConfig(rps=2.0, burst=1)

    await limiter.acquire("a.example", config)
    await limiter.acquire("a.example", config)

    _patch_sleep.assert_awaited_once()
    (waited_seconds,) = _patch_sleep.await_args.args
    assert waited_seconds == pytest.approx(0.5)
