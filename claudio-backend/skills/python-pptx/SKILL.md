---
name: python-pptx
description: >-
  Diagnosing and fixing tuple-unpacking ValueError in python-pptx slide
  generation. Use when deck-building code raises "too many values to unpack",
  "not enough values to unpack", or a ValueError at a for-loop that reads a
  list of layer/shape entries. Covers finding the failing loop, comparing the
  unpack pattern against each entry's actual shape, inspecting the dependent
  text-box layout code that consumes the unpacked values, updating the loop
  and the data shape together consistently, and rerunning deck generation to
  verify the output.
license: MIT
metadata:
  last_reviewed: '2026-11-13'
---

# python-pptx: tuple-unpacking errors in slide generation

## The cause

Slide-generation code often walks a list of "layer" entries, unpacking each
entry into named variables:

```python
for name, label, color in layers:        # expects exactly 3 values per entry
    add_textbox(slide, name, label, color)
```

A `ValueError` is raised when an entry's actual number of values does not
match the unpack pattern:

- `too many values to unpack (expected 3)` — an entry has **more** values
  than the loop unpacks (e.g. a 4th field was added to the data but not to
  the loop).
- `not enough values to unpack (expected 3, got 2)` — an entry has **fewer**
  values than the loop unpacks (e.g. a field was dropped from the data).

The data structure and the layout code have drifted apart. The error is
raised at the `for` line, but the real problem is a mismatch between the
shape of the data and the shape the loop (and everything downstream)
assumes.

## 1. Locate the failing loop

Read the traceback to the exact `for ... in ...` line that raised. Note the
unpack pattern on the left-hand side and how many names it expects.

```python
# Traceback points here:
for name, label, color in layers:
# ValueError: too many values to unpack (expected 3)
```

## 2. Inspect the actual shape of every entry

Do not assume the data is uniform — inspect each entry's length and content.
The mismatch is often in one or a few entries, not all of them.

```python
for i, entry in enumerate(layers):
    print(i, len(entry), entry)
```

Compare each `len(entry)` against the number of names in the unpack
pattern. Identify which entries differ and what the extra/missing value is.
This tells you whether the data grew a field, lost one, or is inconsistent
entry-to-entry.

## 3. Inspect the dependent layout logic

Before changing anything, find every place that consumes the unpacked
values. The unpack pattern is not the only thing tied to the shape — the
text-box layout code downstream uses those variables, and a field that looks
"extra" may actually be needed by layout logic that was written for a
different shape.

Search for each variable name and for the loop body:

```bash
grep -n "name\|label\|color\|z_order\|add_textbox\|left\|top\|width\|height" build_deck.py
```

Ask specifically:
- Does the text-box positioning (`left`, `top`, `width`, `height`) or
  z-ordering depend on a value the current loop does not unpack?
- Does any consumer expect a field that the data no longer provides?
- Are there other loops over the same `layers` with a *different* unpack
  pattern? They must agree.

This step is what prevents a "fix" that silences the error but corrupts the
deck.

## 4. Update the loop and the data shape together

Pick one canonical shape and make the data, the loop, and every consumer
match it. Do not change the unpack pattern in isolation.

If the canonical entry is 4 fields (name, label, color, z_order):

```python
# 1. Normalize every data entry to the same 4-value shape
layers = [
    ("bg",    "Background", "#FFFFFF", 0),
    ("title", "Title",      "#1A1A1A", 1),
    ("body",  "Body",       "#333333", 2),
]

# 2. Unpack all four
for name, label, color, z_order in layers:
    add_textbox(slide, name, label, color, z_order)

# 3. Update every consumer that touched these values
def add_textbox(slide, name, label, color, z_order):
    box = slide.shapes.add_textbox(...)
    box.text = label
    # ... use z_order for layering ...
```

If instead the extra field is genuinely unwanted, remove it from **every**
entry and from every consumer — not just from the loop pattern.

Guard against inconsistent data at the boundary so a future bad entry fails
loudly with a clear message rather than a cryptic unpack error:

```python
EXPECTED = 4
for i, entry in enumerate(layers):
    if len(entry) != EXPECTED:
        raise ValueError(
            f"layer entry {i} has {len(entry)} values, expected {EXPECTED}: {entry!r}"
        )
    name, label, color, z_order = entry
```

## 5. Rerun deck generation and verify the output

A passing script is necessary but not sufficient — verify the rendered deck.

```bash
python build_deck.py
```

Then check:
- The deck opens and the slide count is correct.
- Text boxes appear where expected, with the right text, color, and layering.
- No shape is missing, misplaced, or has empty text.

If you can, render to images and eyeball them:

```bash
libreoffice --headless --convert-to pdf deck.pptx
```

## What this skill deliberately avoids

- It does **not** change the tuple arity without first checking the dependent
  text-box / layout code that consumes those values.
- It does **not** "fix" the loop by dropping a field that layout logic still
  needs.
- It does **not** stop at "the script runs" — the rendered deck must be
  verified.

## Quick checklist

1. Traceback line = the failing `for` unpack.
2. Printed `len(entry)` for every layer; found which entries differ.
3. Grepped every consumer of the unpacked variables (layout, positioning, z-order).
4. Chose one canonical shape; normalized data + loop + all consumers to it.
5. Added a length guard so bad entries fail with a clear message.
6. Reran generation and verified the rendered deck, not just the exit code.
