# Refactor + bug fix: split the legacy report module

`app/legacy_report.py` holds the data model, the totals and the markdown
renderer in one file, and `summarize()` crashes with `ZeroDivisionError` on a
report that has no lines.

## Required end state

Create the package `app/report/` with exactly this layout:

- `app/report/model.py` — `Line`, `Report`, `line_net`, `summarize`
  (no rendering code, no imports of the render module).
- `app/report/render.py` — `render_report`, importing what it needs from
  `.model`.
- `app/report/__init__.py` — re-export `Line`, `Report`, `line_net`,
  `summarize`, `render_report` from those two modules.

Then remove the moved logic from `app/legacy_report.py`: it must no longer
define `render_report` (delete the file or leave a short note in it — the code
must go).

## Definition of done

- `summarize(Report("Empty"))` returns `average == 0.0` instead of raising.
- `python3 -m pytest -q tests/test_report.py` passes.
- Do not edit the tests; they define the public API.
