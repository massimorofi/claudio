# Refactor + bug fix: one tier table

`app/pricing.py` computes order lines. `bulk_quote` is a stale copy-paste of the
tier logic inside `line_total`: it lost the `MAX_DISCOUNT_PCT` cap and gives the
top tier 25% instead of 15%, so the two functions disagree.

## Required end state

- Both public functions must compute a line through **one** shared helper named
  `_apply_tier(qty, unit_price)` in `app/pricing.py`.
- The inlined `if qty >= 100: ... elif qty >= 50: ...` ladder must be deleted
  from `bulk_quote` (no duplicated tier table anywhere).
- The cap and the `TIERS` table stay the single source of truth.

## Definition of done

- `python3 -m pytest -q tests/test_pricing.py` passes.
- `grep -c "_apply_tier" app/pricing.py` reports at least 3 lines (the
  definition plus both call sites).
- Do not edit the tests; they define the required behaviour.
