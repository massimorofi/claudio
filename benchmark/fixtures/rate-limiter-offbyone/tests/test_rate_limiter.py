import pytest

from app.rate_limiter import RateLimiter


class FakeClock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def test_burst_allows_exactly_capacity():
    rl = RateLimiter(capacity=3, refill_per_sec=1, clock=FakeClock())
    assert [rl.allow() for _ in range(3)] == [True, True, True]
    assert rl.allow() is False


def test_wait_seconds_is_zero_when_tokens_available():
    rl = RateLimiter(capacity=2, refill_per_sec=4, clock=FakeClock())
    assert rl.wait_seconds(2) == 0.0


def test_wait_seconds_uses_refill_rate():
    rl = RateLimiter(capacity=4, refill_per_sec=2, clock=FakeClock())
    assert rl.allow(4) is True
    assert rl.wait_seconds(1) == pytest.approx(0.5)
    assert rl.wait_seconds(3) == pytest.approx(1.5)


def test_refill_accumulates_over_time():
    clock = FakeClock()
    rl = RateLimiter(capacity=2, refill_per_sec=1, clock=clock)
    assert rl.allow(2) is True
    assert rl.allow(1) is False
    clock.advance(1.0)
    assert rl.allow(1) is True
    clock.advance(10.0)
    assert rl.allow(2) is True
    assert rl.allow(1) is False
