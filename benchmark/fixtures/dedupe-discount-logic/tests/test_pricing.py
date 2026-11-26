from app.pricing import bulk_quote, line_total


def test_line_total_applies_tier():
    assert line_total(5, 10.0) == 50.0
    assert line_total(50, 10.0) == 450.0
    assert line_total(100, 10.0) == 850.0


def test_bulk_quote_matches_line_total_per_item():
    items = [(5, 4.0), (12, 3.0), (60, 2.0), (120, 1.0)]
    assert bulk_quote(items) == round(sum(line_total(q, p) for q, p in items), 2)


def test_bulk_quote_respects_discount_cap():
    assert bulk_quote([(120, 10.0)]) == line_total(120, 10.0) == 1020.0


def test_bulk_quote_empty_order_is_zero():
    assert bulk_quote([]) == 0.0
