"""Worker that produced logs/service.txt (context only - do not change it)."""

import logging

LOG = logging.getLogger("worker")
POOL_SIZE = 4


def handle(task_id, store):
    """Fetch a task payload; every failure in the incident window was a pool wait."""
    try:
        conn = store.acquire(timeout=5)
    except TimeoutError as exc:  # pragma: no cover - incident path
        LOG.error('task_failed task=%s err="%s"', task_id, exc)
        raise
    try:
        return store.run(conn, task_id)
    finally:
        store.release(conn)
