# Feature spec: `retry_call`

Implement `retry_call` in `app/retry.py`. Required behaviour (the tests in
`tests/test_retry.py` are the contract):

```python
retry_call(func, attempts=3, delays=(0.1, 0.3), sleep=time.sleep,
           exceptions=(Exception,))
```

1. Call `func()` with no arguments. Return its result as soon as it succeeds.
2. `attempts` is the **total** number of calls, so `attempts=1` means "no
   retry". `attempts < 1` raises `ValueError` before `func` is called.
3. When `func` raises an exception whose type is in `exceptions`, sleep and try
   again. `sleep(seconds)` is called with the delay for the gap that was just
   waited: the first retry waits `delays[0]`, the second `delays[1]`, and if
   there are more retries than delays, the **last** delay is reused.
4. `sleep` must never be called when the call succeeds or when the exception is
   not in `exceptions`.
5. An exception whose type is not in `exceptions` propagates immediately,
   without any further attempt.
6. If every attempt fails, raise `RetryExhausted` whose message contains the
   number of attempts and whose `__cause__` is the last exception raised.

Do not import anything beyond the standard library, and do not sleep for real in
the tests: they inject their own `sleep`.
