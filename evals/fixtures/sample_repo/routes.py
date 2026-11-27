"""HTTP-ish route handlers for the sample workspace.

Plain functions returning dicts: no web framework, so the fixture stays
dependency-free while still giving a route module in the call graph.
"""
from __future__ import annotations

from . import service
from .models import Cart, Order


def get_customer_orders(customer_id: str = "c-1") -> dict:
    """GET /customers/{customer_id}/orders"""
    return service.order_summary(customer_id)


def post_checkout(payload: dict) -> dict:
    """POST /checkout -- body: {customer_id, items: [{order_id, sku, quantity}]}"""
    cart = Cart(customer_id=str(payload.get("customer_id", "anonymous")))
    for raw in payload.get("items", []):
        cart.add(
            Order(
                order_id=str(raw["order_id"]),
                customer_id=cart.customer_id,
                sku=str(raw["sku"]),
                quantity=int(raw.get("quantity", 1)),
                unit_price=float(raw.get("unit_price", 1.0)),
            )
        )
    return {"stored": service.checkout(cart)}


def get_catalogue() -> dict:
    """GET /catalogue"""
    return service.catalogue_sizes()


ROUTES: dict[tuple[str, str], object] = {
    ("GET", "/customers/orders"): get_customer_orders,
    ("POST", "/checkout"): post_checkout,
    ("GET", "/catalogue"): get_catalogue,
}
