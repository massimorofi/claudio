"""Extract readable text from uploaded files.

The goal is to turn whatever the user drops into the chat into text the model can
reason over, without pulling in a heavy dependency stack. Plain text, code,
Markdown, CSV/JSON/YAML and HTML are read directly. The Office Open XML formats
(``.docx``/``.xlsx``) are plain ZIP archives, so their text is pulled with the
standard library. PDFs use ``pypdf`` when it is installed and report a clear
note when it is not. Images and other binary blobs are stored as-is and reported
as non-text — the agent can still open them from the workspace with its file
tools.

Every path here is best-effort: an extraction that fails returns a short note
rather than raising, so one unreadable file never breaks the request.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

# Extensions read as plain UTF-8 text.
_TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl", ".ndjson",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".log", ".env",
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".html", ".htm", ".css", ".scss", ".less",
    ".sh", ".bash", ".zsh", ".sql", ".c", ".h", ".cpp", ".hpp", ".cc",
    ".java", ".go", ".rs", ".rb", ".php", ".xml", ".vue", ".svelte",
    ".gitignore", ".dockerignore", ".editorconfig",
}
_IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico", ".tiff",
}
_DOCX_EXTS = {".docx"}
_XLSX_EXTS = {".xlsx"}
_PDF_EXTS = {".pdf"}


@dataclass
class ExtractResult:
    """Outcome of extracting text from one file.

    ``kind`` is the detected category (text/docx/xlsx/pdf/image/binary/error).
    ``text`` is the extracted content (possibly empty). ``note`` carries a
    human-readable explanation when extraction was partial or impossible.
    """

    text: str
    kind: str
    note: str = ""
    chars: int = 0


def _looks_binary(raw: bytes) -> bool:
    """Heuristic: a NUL byte in the leading chunk means binary, not text."""
    return b"\x00" in raw[:8192]


def _unescape(text: str) -> str:
    return (
        text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&amp;", "&")
    )


def _docx_text(path: Path, max_chars: int) -> str:
    """Pull text out of a .docx (a ZIP of XML parts)."""
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    # Preserve paragraph and line breaks before stripping the tags.
    xml = xml.replace("</w:p>", "\n")
    xml = re.sub(r"<w:br\b[^>]*/>", "\n", xml)
    xml = re.sub(r"<w:tab\b[^>]*/>", "\t", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    text = _unescape(text)
    # Collapse runs of blank lines for readability.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:max_chars]


def _xlsx_text(path: Path, max_chars: int) -> str:
    """Pull the shared strings out of an .xlsx workbook."""
    with zipfile.ZipFile(path) as z:
        if "xl/sharedStrings.xml" not in z.namelist():
            return ""
        xml = z.read("xl/sharedStrings.xml").decode("utf-8", errors="replace")
    cells = re.findall(r"<t[^>]*>(.*?)</t>", xml, flags=re.DOTALL)
    out = "\n".join(c for c in cells if c and c.strip())
    out = _unescape(out)
    return out[:max_chars]


def _pdf_text(path: Path, max_chars: int) -> str | None:
    """Extract PDF text with pypdf. Returns None when pypdf is unavailable."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    reader = PdfReader(str(path))
    parts: list[str] = []
    total = 0
    for page in reader.pages:
        chunk = page.extract_text() or ""
        parts.append(chunk)
        total += len(chunk)
        if total >= max_chars:
            break
    return "\n".join(parts)[:max_chars]


def extract_text(path: Path, max_chars: int = 30_000) -> ExtractResult:
    """Extract up to ``max_chars`` of readable text from ``path``.

    Never raises: a failure to read or parse yields an empty ``text`` plus a
    ``note`` describing what went wrong.
    """
    suffix = path.suffix.lower()
    try:
        if suffix in _DOCX_EXTS:
            text = _docx_text(path, max_chars)
            return ExtractResult(
                text=text,
                kind="docx",
                chars=len(text),
                note="" if text.strip() else "no text found in document",
            )
        if suffix in _XLSX_EXTS:
            text = _xlsx_text(path, max_chars)
            return ExtractResult(
                text=text,
                kind="xlsx",
                chars=len(text),
                note="" if text.strip() else "no text found in workbook",
            )
        if suffix in _PDF_EXTS:
            text = _pdf_text(path, max_chars)
            if text is None:
                return ExtractResult(
                    text="",
                    kind="pdf",
                    note="PDF text extraction unavailable (install pypdf)",
                )
            return ExtractResult(
                text=text,
                kind="pdf",
                chars=len(text),
                note="" if text.strip() else "no text found in PDF",
            )
        if suffix in _IMAGE_EXTS:
            return ExtractResult(
                text="",
                kind="image",
                note="image stored in the workspace (not added as text)",
            )

        raw = path.read_bytes()
        if _looks_binary(raw):
            return ExtractResult(
                text="",
                kind="binary",
                note="binary file stored in the workspace (not added as text)",
            )
        text = raw.decode("utf-8", errors="replace")[:max_chars]
        return ExtractResult(text=text, kind="text", chars=len(text), note="")
    except zipfile.BadZipFile:
        return ExtractResult(text="", kind="error", note="corrupt archive")
    except FileNotFoundError:
        return ExtractResult(text="", kind="error", note="file not found")
    except Exception as exc:  # noqa: BLE001 - extraction must never break the request
        return ExtractResult(text="", kind="error", note=f"extraction failed: {exc}")
