"""Data access for the sample workspace. Bottom of the call graph."""
from __future__ import annotations

from . import config
from .models import Order


class OrderRepository:
    """In-memory stand-in for a database. No third-party dependencies."""

    def __init__(self, rows: list[Order] | None = None) -> None:
        self._rows: list[Order] = list(rows or [])

    def add(self, order: Order) -> Order:
        self._rows.append(order)
        return order

    def find_by_id(self, order_id: str) -> Order | None:
        for order in self._rows:
            if order.order_id == order_id:
                return order
        return None

    def find_by_customer(self, customer_id: str) -> list[Order]:
        return [o for o in self._rows if o.customer_id == customer_id]

    def all_orders(self) -> list[Order]:
        return list(self._rows)

    def total_units(self) -> int:
        return sum(o.quantity for o in self._rows)


_DEFAULT: OrderRepository | None = None


def get_repository() -> OrderRepository:
    """Module-level accessor so callers depend on this module, not the class."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = OrderRepository()
        for row in config.SEED_ORDERS:
            _DEFAULT.add(Order(*row))
    return _DEFAULT
