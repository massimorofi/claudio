# Bug fix: CSV order normaliser

`app/csvnorm.py` cleans the nightly exporter file before it is loaded into the
ledger. Three defects were reported:

1. Padded/blank rows from the exporter end up in the output.
2. Order ids are matched verbatim, so `A-1`, `a-1` and `a-1 ` are treated as
   three different orders instead of one.
3. Money is rounded with banker's rounding, so `2.665` lands as `2.66` where
   finance expects `2.67` (round half up).

## Definition of done

- `python3 -m pytest -q tests/test_csvnorm.py` passes.
- Only `app/csvnorm.py` changes; the tests state the required behaviour.
