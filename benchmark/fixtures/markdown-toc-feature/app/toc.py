"""GitHub-style table of contents for markdown documents. See SPEC.md."""


def slugify(title):
    """Turn a heading title into a URL anchor."""
    raise NotImplementedError("slugify is not implemented yet")


def build_toc(markdown_text):
    """Return ``[(level, title, anchor), ...]`` for every heading."""
    raise NotImplementedError("build_toc is not implemented yet")
