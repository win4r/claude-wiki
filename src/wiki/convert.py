"""Convert documents to text that starts with a [SOURCE: ...] line.

Standard library: .txt .md .html .htm .csv .eml. With the `convert` extra: .docx .xlsx .pptx and text-layer .pdf.
The HTML extractor and the Office readers are adapted from the knowledge-wiki quickstart (MIT, see NOTICE).
"""

from __future__ import annotations

import email
import email.policy
import re
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

from . import WikiError

STDLIB_TYPES = {".txt", ".md", ".html", ".htm", ".csv", ".eml"}
EXTRA_TYPES = {".docx", ".xlsx", ".pptx", ".pdf"}
SUPPORTED = STDLIB_TYPES | EXTRA_TYPES
MIN_PDF_CHARS_PER_PAGE = 40  # below this a PDF is treated as scanned


class TextExtractor(HTMLParser):
    """Readable text from HTML, keeping rough table structure (cells joined with ' | ')."""

    SKIP = {"script", "style", "head"}
    BLOCK = {"p", "div", "tr", "table", "br", "h1", "h2", "h3", "h4", "li", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            self.skip_depth = 0  # a <head> that was never closed (valid HTML) must not swallow the page
        if tag in self.SKIP:
            self.skip_depth += 1
        elif tag in ("td", "th"):
            self.out.append(" | ")
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip_depth = max(0, self.skip_depth - 1)
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip_depth:
            self.out.append(data)

    def text(self) -> str:
        raw = "".join(self.out).replace("\xa0", " ")
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r" ?\| (?:\| )+", " | ", raw)
        lines = [ln.strip(" |") for ln in raw.split("\n")]
        text = "\n".join(ln for ln in lines if ln)
        return re.sub(r"\n{3,}", "\n\n", text)


def html_to_text(html: str) -> str:
    p = TextExtractor()
    p.feed(html)
    return p.text()


def _need(module: str):
    try:
        return __import__(module)
    except ImportError:
        raise WikiError(
            f"Converting this file needs {module}: install the convert extra with pip install -e '.[convert]'"
        ) from None


def docx_to_text(path: Path) -> str:
    docx = _need("docx")
    doc = docx.Document(str(path))
    parts = []
    for para in doc.paragraphs:
        if not para.text.strip():
            continue
        prefix = "# " if para.style is not None and para.style.name.startswith("Heading 1") else ""
        parts.append(prefix + para.text)
    for table in doc.tables:
        rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
        if not rows:
            continue
        parts.append("| " + " | ".join(rows[0]) + " |")
        parts.append("|" + "---|" * len(rows[0]))
        for row in rows[1:]:
            parts.append("| " + " | ".join(row) + " |")
    return "\n\n".join(parts)


def xlsx_to_text(path: Path) -> str:
    openpyxl = _need("openpyxl")
    wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    parts = []
    for ws in wb.worksheets:
        parts.append(f"## Sheet: {ws.title}")
        for row in ws.iter_rows(values_only=True):
            cells = ["" if v is None else str(v) for v in row]
            if any(c.strip() for c in cells):
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def pptx_to_text(path: Path) -> str:
    _need("pptx")
    from pptx import Presentation

    prs = Presentation(str(path))
    parts = []
    for i, slide in enumerate(prs.slides, 1):
        parts.append(f"## Slide {i}")
        for shape in slide.shapes:
            if shape.has_text_frame:
                parts.append(shape.text_frame.text)
    return "\n".join(parts)


def pdf_to_text(path: Path) -> str:
    pypdf = _need("pypdf")
    reader = pypdf.PdfReader(str(path))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    text = "\n\n".join(f"## Page {i}\n{t}" for i, t in enumerate(pages, 1) if t)
    chars = sum(len(t) for t in pages)
    if not pages or chars < MIN_PDF_CHARS_PER_PAGE * len(pages):
        raise WikiError(
            f"{path.name} looks like a scanned PDF (about {chars // max(1, len(pages))} characters of text "
            "per page). Transcribing it would need a paid model call, so wiki add refuses it: convert it to "
            "text with OCR first and add the text."
        )
    return text


def eml_to_text(path: Path) -> str:
    msg = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
    hdr = "\n".join(f"{k}: {msg.get(k, '')}" for k in ("From", "To", "Date", "Subject"))
    body = msg.get_body(preferencelist=("plain", "html"))
    content = ""
    if body is not None:
        try:
            content = body.get_content()
        except LookupError:  # a charset Python does not know
            content = (body.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
        if body.get_content_type() == "text/html":
            content = html_to_text(content)
    return hdr + "\n\n" + content


def read_text(path: Path) -> str:
    """A text file as UTF-8, or as UTF-16 when it starts with that byte-order mark (Excel's "Unicode Text", a
    PowerShell redirect): read as UTF-8, such a file became its letters with a NUL between each."""
    data = Path(path).read_bytes()
    text = data.decode("utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


READERS = {".eml": eml_to_text, ".docx": docx_to_text, ".xlsx": xlsx_to_text, ".pptx": pptx_to_text,
           ".pdf": pdf_to_text}


def convert(path: Path) -> str:
    """The document's text, without a source line."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md", ".csv"):
        return read_text(path)
    if suffix in (".html", ".htm"):
        return html_to_text(read_text(path))
    if suffix not in READERS:
        raise WikiError(f"{path.name}: unsupported type {suffix or '(none)'}; supported: {', '.join(sorted(SUPPORTED))}")
    try:
        return READERS[suffix](path)
    except WikiError:
        raise
    except Exception as why:  # a damaged file: the reader's own error, as a message instead of a traceback
        raise WikiError(f"{path.name}: could not be read as {suffix}: {type(why).__name__}: {why}") from None


def with_source_line(text: str, name: str, source_line: str | None = None, today: str | None = None) -> str:
    """Text that starts with exactly one [SOURCE: ...] line."""
    text = text.replace("\r\n", "\n").lstrip("﻿")
    first, _, rest = text.partition("\n")
    if source_line:
        if first.startswith("[SOURCE:"):
            text = rest.lstrip("\n")
        return source_line.strip() + "\n\n" + text.strip("\n") + "\n"
    if first.startswith("[SOURCE:") and first.rstrip().endswith("]"):
        return text if text.endswith("\n") else text + "\n"
    header = f"[SOURCE: {name} | added: {today or date.today().isoformat()}]"
    return header + "\n\n" + text.strip("\n") + "\n"
