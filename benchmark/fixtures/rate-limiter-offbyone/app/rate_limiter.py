"""Token-bucket rate limiter used by the notification dispatcher.

Two bugs are reported against this module (see README.md). Fix them without
changing the public API or the tests.
"""

import time


class RateLimiter:
    """Allow up to ``capacity`` requests, refilling continuously."""

    def __init__(self, capacity, refill_per_sec, clock=time.monotonic):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if refill_per_sec <= 0:
            raise ValueError("refill_per_sec must be positive")
        self.capacity = float(capacity)
        self.refill_per_sec = float(refill_per_sec)
        self.clock = clock
        self.tokens = float(capacity)
        self._last = clock()

    def _refill(self):
        now = self.clock()
        elapsed = now - self._last
        if elapsed < 0:
            raise ValueError("clock went backwards")
        self._last = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_sec)

    def allow(self, cost=1):
        """Spend ``cost`` tokens if the bucket holds them."""
        self._refill()
        if cost > self.capacity:
            return False
        # BUG (off-by-one): a bucket holding exactly ``cost`` tokens is rejected.
        if self.tokens > cost:
            self.tokens -= cost
            return True
        return False

    def wait_seconds(self, cost=1):
        """Seconds to wait until ``cost`` tokens are available."""
        self._refill()
        missing = cost - self.tokens
        if missing <= 0:
            return 0.0
        # BUG: divides by the bucket size instead of the refill rate.
        return missing / self.capacity
