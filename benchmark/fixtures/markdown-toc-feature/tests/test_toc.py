from app.toc import build_toc, slugify

DOC = """# Title
intro text

## Hello, World!

```text
## Inside a fence
```

### Sub *emph*
### Sub *emph*
####   Padded heading
"""


def test_slugify_examples():
    assert slugify("Hello, World!") == "hello-world"
    assert slugify("Ship It: v2.0") == "ship-it-v20"
    assert slugify("snake_case and CAPS") == "snakecase-and-caps"
    assert slugify("  Trim   Me  ") == "trim---me"


def test_headings_in_order_with_levels():
    entries = build_toc(DOC)
    assert [(lvl, title) for lvl, title, _ in entries] == [
        (1, "Title"),
        (2, "Hello, World!"),
        (3, "Sub *emph*"),
        (3, "Sub *emph*"),
        (4, "Padded heading"),
    ]


def test_code_fences_are_skipped():
    titles = [title for _, title, _ in build_toc(DOC)]
    assert "Inside a fence" not in titles


def test_duplicate_anchors_get_numeric_suffixes():
    assert [anchor for _, _, anchor in build_toc(DOC)] == [
        "title",
        "hello-world",
        "sub-emph",
        "sub-emph-1",
        "padded-heading",
    ]


def test_empty_document_has_no_entries():
    assert build_toc("just a paragraph\n") == []
