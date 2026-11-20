"""Deterministic post-mutation verification (issue #58, Increment 2).

After a write or edit, a lightweight file-scoped syntax check runs inside the
tool wrapper and its verdict is folded into the tool result. This saves the
model a whole follow-up turn ("write file, then run a syntax check") for the
common failure of introducing a syntax error: the error comes back inline on
the same call that made it.

The checks are deliberately cheap, dependency-free and language-scoped -- a
parse failure is reported as a warning inside an otherwise-successful result,
never as a hard error, because a file the model *intends* to be incomplete
(e.g. a partial append) must not fail the write.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path


def check_syntax(path: Path, text: str) -> dict | None:
    """Return a verification dict for ``text`` as ``path``, or ``None``.

    ``None`` means "no check applies" (unknown extension). A dict is
    ``{"ok": bool, "tool": str, "detail": str}``.
    """
    suffix = path.suffix.lower()
    if suffix == ".py":
        try:
            ast.parse(text)
            return {"ok": True, "tool": "python-ast", "detail": "syntax OK"}
        except SyntaxError as exc:
            line = getattr(exc, "lineno", None)
            where = f" (line {line})" if line else ""
            return {
                "ok": False,
                "tool": "python-ast",
                "detail": f"SyntaxError: {exc.msg}{where}",
            }
    if suffix == ".json":
        try:
            json.loads(text)
            return {"ok": True, "tool": "json", "detail": "valid JSON"}
        except ValueError as exc:
            return {"ok": False, "tool": "json", "detail": f"JSON error: {exc}"}
    if suffix in (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"):
        # No JS parser in the stdlib; a bracket-balance smoke test catches the
        # gross cases (unbalanced braces/parens/brackets) without dependencies.
        detail = _balance_check(text)
        if detail is None:
            return {"ok": True, "tool": "bracket-balance", "detail": "balanced"}
        return {"ok": False, "tool": "bracket-balance", "detail": detail}
    return None


def _balance_check(text: str) -> str | None:
    """Return an error string if brackets are unbalanced, else ``None``.

    Dependency-free and deliberately conservative: it strips line and block
    comments and skips string literals before counting, so brackets inside
    comments/strings do not cause false "unbalanced" reports (issue #58
    review). It does not attempt to parse regex literals, so a bracket inside a
    regex may still be miscounted -- which is why the caller treats a failure
    as a non-fatal verification note, never a hard error.
    """
    # Strip block then line comments (best-effort; not string-aware for the
    # comment markers themselves, which is acceptable for a smoke test).
    text = _strip_comments(text)
    pairs = {")": "(", "]": "[", "}": "{"}
    openers = set(pairs.values())
    stack: list[tuple[str, int]] = []
    line = 1
    in_str: str | None = None
    prev = ""
    for ch in text:
        if ch == "\n":
            line += 1
        if in_str:
            if ch == in_str and prev != "\\":
                in_str = None
        elif ch in ("'", '"', "`"):
            in_str = ch
        elif ch in openers:
            stack.append((ch, line))
        elif ch in pairs:
            if not stack or stack[-1][0] != pairs[ch]:
                return f"unbalanced '{ch}' at line {line}"
            stack.pop()
        prev = ch
    if stack:
        ch, ln = stack[-1]
        return f"unclosed '{ch}' opened at line {ln}"
    return None


def _strip_comments(text: str) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    in_str: str | None = None
    prev = ""
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == in_str and prev != "\\":
                in_str = None
            prev = ch
            i += 1
            continue
        if ch in ("'", '"', "`"):
            in_str = ch
            out.append(ch)
            prev = ch
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            # line comment: skip to end of line
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        prev = ch
        i += 1
    return "".join(out)


__all__ = ["check_syntax"]
