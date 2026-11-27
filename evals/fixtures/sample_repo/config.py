"""Static configuration for the sample workspace."""
from __future__ import annotations

APP_NAME = "sample-repo"
DEFAULT_CURRENCY = "EUR"

#: (order_id, customer_id, sku, quantity, unit_price) rows seeded on first access.
SEED_ORDERS: tuple[tuple[str, str, str, int, float], ...] = (
    ("o-1", "c-1", "widget", 2, 9.5),
    ("o-2", "c-1", "gadget", 1, 21.0),
    ("o-3", "c-2", "widget", 5, 9.5),
)

#: Applied by :func:`service.apply_discount` to preferred customers.
PREFERRED_CUSTOMERS: frozenset[str] = frozenset({"c-1"})
DISCOUNT_RATE = 0.1
