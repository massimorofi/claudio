"""Retry helper. See SPEC.md for the required behaviour."""

import time


class RetryExhausted(Exception):
    """Raised when every attempt of a call failed."""


def retry_call(func, attempts=3, delays=(0.1, 0.3), sleep=time.sleep,
               exceptions=(Exception,)):
    """Call ``func`` until it succeeds or ``attempts`` are used up.

    TODO: implement according to SPEC.md.
    """
    raise NotImplementedError("retry_call is not implemented yet")
