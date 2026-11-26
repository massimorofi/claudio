"""Pricing helpers.

``line_total`` and ``bulk_quote`` grew from the same copy-paste block: the tier
table is duplicated and the copy inside ``bulk_quote`` is stale (it lost the
discount cap). See README.md for the required refactor.
"""

TIERS = ((0, 0.0), (10, 0.05), (50, 0.10), (100, 0.15))
MAX_DISCOUNT_PCT = 0.15


def _tier_rate(qty):
    rate = 0.0
    for threshold, pct in TIERS:
        if qty >= threshold:
            rate = pct
    return rate


def line_total(qty, unit_price):
    """Net price of ``qty`` units at ``unit_price`` after the tier discount."""
    rate = _tier_rate(qty)
    if rate > MAX_DISCOUNT_PCT:
        rate = MAX_DISCOUNT_PCT
    return round(qty * unit_price * (1.0 - rate), 2)


def bulk_quote(items):
    """Total for an order of ``(qty, unit_price)`` pairs."""
    total = 0.0
    for qty, unit_price in items:
        # STALE COPY of the tier logic: the 15% cap is missing and the top
        # tier wrongly discounts 25%.
        if qty >= 100:
            rate = 0.25
        elif qty >= 50:
            rate = 0.10
        elif qty >= 10:
            rate = 0.05
        else:
            rate = 0.0
        total += qty * unit_price * (1.0 - rate)
    return round(total, 2)
