"""Business rules for the sample workspace. Sits between routes and the store."""
from __future__ import annotations

from . import config
from .models import Cart, Order
from .repository import OrderRepository, get_repository


def apply_discount(order: Order) -> Order:
    """Preferred customers get ``config.DISCOUNT_RATE`` off the unit price."""
    if order.customer_id not in config.PREFERRED_CUSTOMERS:
        return order
    return Order(
        order_id=order.order_id,
        customer_id=order.customer_id,
        sku=order.sku,
        quantity=order.quantity,
        unit_price=order.unit_price * (1.0 - config.DISCOUNT_RATE),
    )


def order_summary(customer_id: str, repo: OrderRepository | None = None) -> dict:
    """Totals for one customer, read through the repository."""
    store = repo or get_repository()
    orders = [apply_discount(o) for o in store.find_by_customer(customer_id)]
    return {
        "customer_id": customer_id,
        "orders": [o.order_id for o in orders],
        "total": round(sum(o.line_total for o in orders), 2),
        "currency": config.DEFAULT_CURRENCY,
    }


def checkout(cart: Cart, repo: OrderRepository | None = None) -> list[str]:
    """Persist every line of a cart and return the stored ids."""
    store = repo or get_repository()
    stored = []
    for item in cart.items:
        store.add(apply_discount(item))
        stored.append(item.order_id)
    return stored


def catalogue_sizes(repo: OrderRepository | None = None) -> dict:
    """Distinct skus and total units currently stored."""
    store = repo or get_repository()
    rows = store.all_orders()
    return {"skus": len({r.sku for r in rows}), "units": store.total_units()}
