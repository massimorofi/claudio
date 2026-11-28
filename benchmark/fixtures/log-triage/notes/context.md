# Incident context (2026-05-04 morning)

- The API gateway sits in front of the worker; the gateway times out after 2 s
  whenever a worker task has not finished.
- Workers share one database connection pool (`POOL_SIZE` in `app/worker.py`).
  `store.acquire(timeout=5)` raises `TimeoutError` when the pool is drained.
- On-call notes: "the api timeouts look like symptoms, the worker errors name
  the actual resource".
