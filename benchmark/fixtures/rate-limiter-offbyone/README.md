# Bug fix: rate limiter off-by-one

`app/rate_limiter.py` implements the token bucket used by the notification
dispatcher. Two defects were reported from production:

1. A brand-new bucket with `capacity=3` only lets through **two** requests
   instead of three, and `allow()` rejects a request whose cost equals the
   tokens that are available.
2. `wait_seconds()` returns nonsense when the refill rate differs from the
   bucket size (e.g. `capacity=4, refill_per_sec=2` reports 0.25 s for one
   missing token instead of 0.5 s).

## Definition of done

- `python3 -m pytest -q tests/test_rate_limiter.py` passes.
- Only `app/rate_limiter.py` changes; the tests describe the intended
  behaviour and must not be edited.
