# sample_repo architecture

A synthetic package that exists to be indexed. Nothing here is meant to be a
good application; it is meant to be a *small, unambiguous call graph* that a
code-graph server can be pointed at.

## Layers

| File | Role | Calls |
| --- | --- | --- |
| `app.py` | entry point, `main` / `dispatch` | `routes`, `service` |
| `routes.py` | route handlers, `ROUTES` table | `service`, `models` |
| `service.py` | business rules | `repository`, `models`, `config` |
| `repository.py` | in-memory store | `models`, `config` |
| `models.py` | `Order`, `Cart` | (nothing) |
| `config.py` | constants and seed rows | (nothing) |

The one path every structural question in the eval walks:

```
app.main -> service.order_summary -> repository.get_repository
                                   -> repository.OrderRepository.find_by_customer
```

## Properties the eval relies on

- Standard library only. No install step, no network, no third-party import.
- Every cross-module call is a plain `from . import x` / `from .x import Y`, so
  an import-graph walk over the package reproduces the table above exactly.
- No generated code, no dynamic attribute access, no plugin registry: a static
  index and a grep of the same tree agree.
- Vendor-free text everywhere, including this file.

## Running it

```
cd evals/fixtures
python3 -m sample_repo.app
```
