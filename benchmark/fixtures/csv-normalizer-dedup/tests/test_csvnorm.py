from decimal import Decimal

from app.csvnorm import dedupe, parse_rows, round_money, total_amount

CSV = """id,customer,amount
A-1,Acme,10.005
a-1 ,Acme,11.00
B-2,Bo,5.00
"""


def test_round_money_uses_half_up():
    assert round_money("2.665") == Decimal("2.67")
    assert round_money("0.005") == Decimal("0.01")
    assert round_money("2.674") == Decimal("2.67")


def test_blank_rows_are_dropped():
    text = "id,customer,amount\nA-1,Acme,1.00\n,,\n"
    assert [r["id"] for r in parse_rows(text)] == ["A-1"]


def test_dedupe_is_case_and_space_insensitive():
    rows = parse_rows(CSV)
    kept = dedupe(rows)
    assert len(kept) == 2
    assert sorted(r["amount"] for r in kept) == ["11.00", "5.00"]


def test_total_rounds_each_row_half_up():
    rows = [{"id": "x", "customer": "c", "amount": "2.665"},
            {"id": "y", "customer": "c", "amount": "0.005"}]
    assert total_amount(rows) == Decimal("2.68")


def test_missing_column_raises():
    import pytest

    with pytest.raises(ValueError):
        parse_rows("id,customer\nA-1,Acme\n")
