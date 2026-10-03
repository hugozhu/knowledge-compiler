"""Deterministic parsers (CPU first — no LLM here except OCR fallback helpers).

Type dispatch by extension:
  markdown/text → read utf-8
  html          → bs4 if installed, else stdlib HTMLParser
  pdf           → PyMuPDF if installed, else pdftotext; scanned → needs_ocr
  image         → needs_ocr (OCR via VLM orchestrated by compiler)
  docx          → python-docx if installed, else zip + regex fallback
"""

from __future__ import annotations

import html as htmllib
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path


class ParserError(RuntimeError):
    pass


@dataclass
class ParsedDoc:
    title: str
    text: str
    source_type: str
    meta: dict = field(default_factory=dict)


EXT_TYPE = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".text": "text",
    ".rst": "text",
    ".log": "text",
    ".html": "html",
    ".htm": "html",
    ".pdf": "pdf",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".docx": "docx",
}

_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "section", "article",
    "blockquote", "pre", "header", "footer", "nav",
    "h1", "h2", "h3", "h4", "h5", "h6", "table",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._in_title = False
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title += data
        else:
            self.parts.append(data)


def detect_type(path: Path) -> str:
    return EXT_TYPE.get(Path(path).suffix.lower(), "unknown")


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip()[:120]
    return ""


def _first_line(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s:
            return s[:100]
    return ""


def _parse_text(path: Path, source_type: str) -> ParsedDoc:
    raw = Path(path).read_bytes()
    text = raw.decode("utf-8", errors="replace")
    title = _first_heading(text) or _first_line(text) or Path(path).stem
    return ParsedDoc(title=title, text=text, source_type=source_type)


def _parse_html(path: Path) -> ParsedDoc:
    raw = Path(path).read_bytes()
    page = raw.decode("utf-8", errors="replace")
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(page, "html.parser")
        for t in soup(["script", "style"]):
            t.decompose()
            text = soup.get_text("\n")
        title = (soup.title.string or "").strip() if soup.title else ""
    except ImportError:
        p = _TextExtractor()
        p.feed(page)
        text = "".join(p.parts)
        title = p.title.strip()
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    title = title or _first_line(text) or Path(path).stem
    return ParsedDoc(title=title, text=text, source_type="html")


def _parse_pdf(path: Path) -> ParsedDoc:
    text, engine = "", ""
    try:
        import fitz  # type: ignore  # PyMuPDF (optional)

        doc = fitz.open(str(path))
        text = "\n\n".join(page.get_text() for page in doc)
        engine = "pymupdf"
    except ImportError:
        exe = shutil.which("pdftotext")
        if not exe:
            raise ParserError("no PDF engine: install poppler-utils (pdftotext) or pymupdf")
        proc = subprocess.run(
            [exe, "-layout", "-enc", "UTF-8", str(path), "-"],
            capture_output=True,
        )
        text = proc.stdout.decode("utf-8", errors="replace")
        engine = "pdftotext"
    meta = {"engine": engine}
    if len(text.strip()) < 20:  # scanned PDF
        meta["needs_ocr"] = True
        text = ""
    title = _first_line(text) or Path(path).stem
    return ParsedDoc(title=title, text=text, source_type="pdf", meta=meta)


def _parse_image(path: Path) -> ParsedDoc:
    return ParsedDoc(
        title=Path(path).stem,
        text="",
        source_type="image",
        meta={"needs_ocr": True},
    )


def _parse_docx(path: Path) -> ParsedDoc:
    try:
        import docx  # type: ignore  # python-docx (optional)

        d = docx.Document(str(path))
        paras = [p.text for p in d.paragraphs if p.text.strip()]
        text = "\n\n".join(paras)
        title = _first_heading(text) or _first_line(text) or Path(path).stem
        return ParsedDoc(title=title, text=text, source_type="docx", meta={"engine": "python-docx"})
    except ImportError:
        pass
    import zipfile

    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    xml = xml.replace("</w:p>", "\n")
    text = htmllib.unescape(re.sub(r"<[^>]+>", "", xml))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    title = _first_line(text) or Path(path).stem
    return ParsedDoc(title=title, text=text, source_type="docx", meta={"engine": "zip-regex"})


def parse_file(path: Path) -> ParsedDoc:
    t = detect_type(path)
    if t in ("markdown", "text"):
        return _parse_text(path, t)
    if t == "html":
        return _parse_html(path)
    if t == "pdf":
        return _parse_pdf(path)
    if t == "image":
        return _parse_image(path)
    if t == "docx":
        return _parse_docx(path)
    # unknown extension: try as plain text
    try:
        return _parse_text(path, "text")
    except (UnicodeDecodeError, OSError) as e:
        raise ParserError(f"unsupported file type {Path(path).suffix!r}: {e}") from e


def _page_num(p: Path) -> int:
    m = re.search(r"-(\d+)\.png$", p.name)
    return int(m.group(1)) if m else 0


def render_pdf_pages(
    path: Path,
    max_pages: int = 10,
    dpi: int = 150,
    outdir: Path | None = None,
) -> list[Path]:
    """Render first N pages of a PDF to PNG (for VLM OCR). Caller owns outdir."""
    exe = shutil.which("pdftoppm")
    if not exe:
        raise ParserError("pdftoppm not available (install poppler-utils)")
    outdir = Path(outdir) if outdir else Path(".")
    outdir.mkdir(parents=True, exist_ok=True)
    prefix = outdir / "page"
    proc = subprocess.run(
        [exe, "-r", str(dpi), "-png", "-f", "1", "-l", str(max_pages), str(path), str(prefix)],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise ParserError(f"pdftoppm failed: {proc.stderr.decode('utf-8', errors='replace')[:200]}")
    files = sorted(outdir.glob("page-*.png"), key=_page_num)
    if not files:
        raise ParserError("pdftoppm produced no pages")
    return files
