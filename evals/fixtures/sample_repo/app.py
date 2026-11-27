"""Entry point of the sample workspace.

Top of the call graph: ``main`` -> ``routes`` -> ``service`` -> ``repository``.
Run it with ``python -m sample_repo.app`` from ``evals/fixtures``.
"""
from __future__ import annotations

from . import routes, service


def dispatch(method: str, path: str, **kwargs) -> dict:
    """Look the route up and call its handler with the given keyword arguments."""
    handler = routes.ROUTES.get((method.upper(), path))
    if handler is None:
        return {"error": f"no route for {method} {path}"}
    return handler(**kwargs)


def main(argv: list[str] | None = None) -> int:
    """Print one summary per seeded customer, then the catalogue totals."""
    del argv
    for customer_id in ("c-1", "c-2"):
        print(service.order_summary(customer_id))
    print(routes.get_catalogue())
    return 0


if __name__ == "__main__":  # pragma: no cover - fixture entry point
    raise SystemExit(main())
