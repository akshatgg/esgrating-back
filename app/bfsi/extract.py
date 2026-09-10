# Port of bfsi-calculator/lib/pdf_text.php plus bfsi_extract_and_fingerprint
# (lib/openai.php:362). See docs/analysis/bfsi.md §4a.
#
# Why a read came back with nothing. These used to be indistinguishable: an unsupported
# file type, a parser crash and a PDF with no text in it all produced the same empty
# result, which told the submitter nothing they could act on. Reasons make each case
# reportable separately.
import hashlib
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

from pypdf import PdfReader

from app.bfsi.ocr import bfsi_ocr_available, bfsi_ocr_pdf_cached
from app.core.errors import UserError

OK = "ok"
UNSUPPORTED = "unsupported"  # not a PDF or DOCX
NO_TEXT_LAYER = "no_text_layer"  # scanned or flattened, and OCR could not help
PARSE_FAILED = "parse_failed"  # the file is damaged or unreadable
EMPTY = "empty"  # parsed fine, genuinely contains no words

_TAG_RE = re.compile(r"<[^>]*>")


def _pdf_font_count(path: Path) -> int | None:
    """
    How many fonts a PDF declares, or None when poppler is unavailable to ask.

    This is the cheap, decisive probe for "is there any text in here at all". A PDF with
    zero fonts cannot contain a single character, so it goes straight to OCR rather than
    handing an image-heavy file to the parser.
    """
    if shutil.which("pdffonts") is None:
        return None
    try:
        proc = subprocess.run(["pdffonts", str(path)], capture_output=True, text=True, errors="replace")
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return max(0, len(proc.stdout.splitlines()) - 2)  # two header lines, then one row per font


def _pdf_text_layer(path: Path) -> tuple[list[dict], str | None]:
    """Pull the text layer out of a PDF. Empty list when there is nothing to pull."""
    try:
        reader = PdfReader(str(path))
        pages = []
        for i, page in enumerate(reader.pages):
            text = (page.extract_text() or "").strip()
            if text:
                pages.append({"page_no": i + 1, "text": text})
        return pages, None
    except Exception as e:
        return [], str(e)


def _docx_text(path: Path) -> tuple[list[dict], str | None]:
    """Pull the text out of a DOCX. Empty list when there is nothing to pull."""
    try:
        with zipfile.ZipFile(path) as zf:
            try:
                xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
            except KeyError:
                xml = ""
    except (zipfile.BadZipFile, OSError):
        return [], "the file could not be opened as a DOCX archive"
    text = _TAG_RE.sub("", xml.replace("</w:p>", "\n")).strip()
    return ([{"page_no": 1, "text": text}], None) if text else ([], None)


def bfsi_extract_detailed(path: Path) -> dict:
    """
    Read a report, falling back to OCR when the file holds no machine-readable text.

    Returns {"pages": [...], "source": "text"|"ocr", "reason": <one of the constants
    above>, "truncated": bool, "detail": str}.
    """
    def result(pages, source, reason, truncated=False, detail=""):
        return {"pages": pages, "source": source, "reason": reason,
                "truncated": truncated, "detail": detail}

    if not path.is_file() or not os.access(path, os.R_OK):
        return result([], "text", PARSE_FAILED, False, "the uploaded file is missing or unreadable")

    ext = path.suffix.lower().lstrip(".")

    if ext == "docx":
        pages, err = _docx_text(path)
        if pages:
            return result(pages, "text", OK)
        return result([], "text", PARSE_FAILED if err is not None else EMPTY, False, err or "")

    if ext != "pdf":
        return result([], "text", UNSUPPORTED, False, f"{(ext.upper() if ext else 'that file type')} is not supported")

    # Only ask the parser for text when the document actually declares fonts. None means
    # poppler is not installed to answer, in which case we try the parser as we always did.
    fonts = _pdf_font_count(path)
    parse_error = None
    pages = []

    if fonts is None or fonts > 0:
        pages, parse_error = _pdf_text_layer(path)
        if pages:
            return result(pages, "text", OK)

    # Nothing readable in the file itself. Render it and read it back.
    file_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    ocr = bfsi_ocr_pdf_cached(path, file_sha256)
    if ocr["pages"]:
        return result(ocr["pages"], "ocr", OK, ocr["truncated"])

    if parse_error is not None:
        return result([], "text", PARSE_FAILED, False, parse_error)
    detail = (
        "the pages were rendered and read, but no words could be recognised"
        if bfsi_ocr_available()
        else "OCR is not installed on this server, so a text-less PDF cannot be recovered"
    )
    return result([], "text", NO_TEXT_LAYER, False, detail)


def bfsi_extract_and_fingerprint(path: Path) -> dict:
    """
    Read the report and fingerprint what it actually says.

    The text -- not the file bytes -- is what gets fingerprinted, matching the ESG
    calculator (helper.py joins the page texts with a space, then SHA-256s them).
    Re-saving or re-exporting a PDF changes its bytes while leaving every word intact,
    so a byte hash would miss that case and re-run the whole analysis.

    Raises UserError with a message naming which of the four failures this was, and what
    the submitter can do about it, when nothing could be extracted.
    """
    r = bfsi_extract_detailed(path)

    if not r["pages"]:
        detail = f" ({r['detail']})" if r["detail"] else ""
        reason = r["reason"]
        if reason == UNSUPPORTED:
            raise UserError(f"That file type cannot be read{detail}. Please upload a PDF or DOCX.")
        if reason == NO_TEXT_LAYER:
            raise UserError(
                "This PDF contains no readable text — it was scanned, or flattened by a "
                f'"Print to PDF" export{detail}. Please upload the original report.'
            )
        if reason == PARSE_FAILED:
            raise UserError(
                f"The uploaded report could not be opened{detail}. The file may be damaged "
                "or password-protected."
            )
        raise UserError(f"The uploaded report contains no text to analyse{detail}.")

    return {
        "pages": r["pages"],
        "text_sha256": hashlib.sha256(" ".join(p["text"] for p in r["pages"]).encode("utf-8")).hexdigest(),
        "source": r["source"],
        "truncated": r["truncated"],
    }
