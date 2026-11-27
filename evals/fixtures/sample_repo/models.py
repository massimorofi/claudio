"""Domain objects for the sample workspace."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Order:
    """One line of business: a customer bought ``quantity`` of ``sku``."""

    order_id: str
    customer_id: str
    sku: str
    quantity: int
    unit_price: float = 1.0

    @property
    def line_total(self) -> float:
        return self.quantity * self.unit_price


@dataclass
class Cart:
    """Mutable accumulation of orders before they are persisted."""

    customer_id: str
    items: list[Order] = field(default_factory=list)

    def add(self, order: Order) -> None:
        self.items.append(order)

    def subtotal(self) -> float:
        return sum(item.line_total for item in self.items)
