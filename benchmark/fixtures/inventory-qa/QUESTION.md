# Read-only audit: package inventory

Read `pkg/__init__.py`, `pkg/core.py` and `pkg/util.py`. **Do not modify any
file in `pkg/`.** Answer three questions by writing a file named
`findings.txt` in the sandbox root.

`findings.txt` must contain exactly these three lines, in this order, with no
extra text:

```
dead_code: <name of the function defined in pkg/util.py that no other file in
            this fixture ever references>
util_functions: <number of top-level functions defined in pkg/util.py>
public_api: <the names exported by pkg/__init__.py __all__, sorted
             alphabetically, comma-separated, no spaces>
```

The `dead_code:` line must be a single token on one line, i.e.
`dead_code: some_name`. Write the file with plain text (no markdown fences).
