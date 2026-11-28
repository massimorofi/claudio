# Feature spec: markdown table of contents

Implement `slugify` and `build_toc` in `app/toc.py`. The tests in
`tests/test_toc.py` are the contract.

## `slugify(title) -> str`

Applied in this exact order:

1. Lower-case the string.
2. Strip leading and trailing whitespace.
3. Delete every character that is **not** a letter, a digit, a space or a hyphen.
4. Replace each remaining space with a single hyphen (consecutive spaces become
   consecutive hyphens).

Examples: `Hello, World!` -> `hello-world`, `Ship It: v2.0` -> `ship-it-v20`,
`snake_case and CAPS` -> `snakecase-and-caps`, `  Trim   Me  ` -> `trim---me`.

## `build_toc(markdown_text) -> list[tuple[int, str, str]]`

Return one `(level, title, anchor)` tuple per ATX heading, in document order:

- A heading is a line with at most three leading spaces, then 1-6 `#`, then at
  least one space, then the title. The title has surrounding whitespace
  stripped; the level is the number of `#`.
- Heading-looking lines **inside a fenced code block are not headings**. A fence
  opens and closes on any line whose stripped content starts with ``` or `~~~`.
- The anchor is `slugify(title)`. If the same anchor already appeared earlier,
  append `-1`, then `-2`, and so on, once per repetition.

Standard library only.
