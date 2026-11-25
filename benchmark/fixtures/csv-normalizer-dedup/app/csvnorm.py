"""Normalise exported CSV order rows: drop blanks, dedupe by id, total amounts.

The finance exporter emits ids with inconsistent case/padding and amounts with
three decimals. Reported defects are listed in README.md.
"""

import csv
import io
from decimal import Decimal

REQUIRED_COLUMNS = ("id", "customer", "amount")


def parse_rows(text):
    """Return a list of dicts with every value stripped of surrounding space."""
    reader = csv.DictReader(io.StringIO(text))
    missing = set(REQUIRED_COLUMNS) - set(reader.fieldnames or [])
    if missing:
        raise ValueError("missing columns: " + ", ".join(sorted(missing)))
    rows = []
    for raw in reader:
        row = {k: (v.strip() if isinstance(v, str) else v) for k, v in raw.items()}
        # BUG: the exporter pads files with empty rows; those are kept here.
        rows.append(row)
    return rows


def dedupe(rows):
    """Keep the last row seen for each order id."""
    out = {}
    for row in rows:
        # BUG: ids are compared verbatim, so "A-1" and "a-1 " count as distinct.
        out[row["id"]] = row
    return list(out.values())


def round_money(value):
    """Round an amount to cents the way the finance team rounds (half up)."""
    # BUG: quantize() defaults to ROUND_HALF_EVEN, so 2.665 becomes 2.66.
    return Decimal(str(value)).quantize(Decimal("0.01"))


def total_amount(rows):
    total = Decimal("0.00")
    for row in rows:
        total += round_money(row["amount"])
    return total.quantize(Decimal("0.01"))
