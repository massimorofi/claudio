"""Core helpers of the ``pkg`` example package."""

from .util import clamp, humanize_bytes, slugify


def make_slug(title, max_len=40):
    return slugify(title)[: clamp(max_len, 1, 100)]


def format_size(num):
    return humanize_bytes(num)


def pipeline(items):
    return [make_slug(item) for item in items]
