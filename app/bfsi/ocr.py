# Port of bfsi-calculator/lib/ocr.php (see docs/analysis/bfsi.md §4b).
#
# OCR fallback for PDFs that carry no text layer -- genuine scans, and (far more often)
# "Print to PDF" re-exports where every glyph was flattened into vector outlines. Both
# look fine on screen and both yield zero characters from a text extractor. Rasterising
# every page and reading it back with Tesseract is the only path that recovers both kinds.
#
# This is a deliberate divergence from the ESG calculator, which has no OCR at all. It
# does not change how any text is scored -- it only recovers text that would otherwise be
# lost, so a report that already extracts cleanly takes exactly the same path it always did.
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from app.core.db import get_db

# 200dpi is the working point: enough for Tesseract on clean report typography, and small
# enough that the page bitmaps stay small.
DPI = 200

# A guard against one pathological upload occupying a worker forever. Reports that exceed
# it are truncated, not rejected.
MAX_PAGES = 60

# Leaves headroom under the request's overall time budget for the OpenAI calls that follow.
TIME_BUDGET = 200

_PAGE_NO_RE = re.compile(r"-0*(\d+)\.jpg$")


def _natural_key(name: str) -> list:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]


def bfsi_ocr_available() -> bool:
    """True when the host has the binaries this fallback shells out to."""
    return shutil.which("pdftoppm") is not None and shutil.which("tesseract") is not None


def _ocr_image(image_path: Path) -> str:
    """Read one rendered page. Returns '' when Tesseract finds nothing or fails."""
    try:
        # cwd + a relative filename, not the absolute path: some Tesseract/Leptonica
        # builds mishandle an absolute path ("failed to open locally with tail ...")
        # while a relative one in the right cwd always works.
        proc = subprocess.run(
            ["tesseract", image_path.name, "stdout", "-l", "eng"],
            capture_output=True, text=True, errors="replace", cwd=image_path.parent,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def bfsi_ocr_pdf(path: Path) -> dict:
    """
    Render every page and read it back.

    Returns {"pages": [...], "truncated": bool}, pages in the same shape
    bfsi_extract_detailed returns, so callers downstream cannot tell OCR'd text from
    extracted text.
    """
    if not bfsi_ocr_available():
        return {"pages": [], "truncated": False}

    workdir = Path(tempfile.mkdtemp(prefix="bfsi_ocr_"))
    try:
        started = time.monotonic()
        try:
            proc = subprocess.run(
                [
                    "pdftoppm", "-jpeg", "-jpegopt", "quality=70",
                    "-r", str(DPI), "-l", str(MAX_PAGES),
                    str(path), str(workdir / "pg"),
                ],
                capture_output=True, text=True, errors="replace",
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return {"pages": [], "truncated": False}

        if proc.returncode != 0:
            return {"pages": [], "truncated": False}

        images = sorted(workdir.glob("pg-*.jpg"), key=lambda p: _natural_key(p.name))
        if not images:
            return {"pages": [], "truncated": False}

        # pdftoppm names files by real page number, so the page_no we report stays true to
        # the document even when the first pages turn out blank.
        pages = []
        truncated = False
        for i, img in enumerate(images):
            if time.monotonic() - started > TIME_BUDGET:
                truncated = True
                break
            text = _ocr_image(img)
            if text == "":
                continue
            m = _PAGE_NO_RE.search(img.name)
            page_no = int(m.group(1)) if m else i + 1
            pages.append({"page_no": page_no, "text": text})

        # A document longer than the cap was cut short by pdftoppm before we ever saw it.
        if not truncated and len(images) >= MAX_PAGES:
            truncated = True

        return {"pages": pages, "truncated": truncated}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Cache -- OCR is minutes of CPU, and re-analysing re-extracts on every run.
# ---------------------------------------------------------------------------
#
# Keyed on the file bytes, not the text: the text hash used as the analysis cache key
# cannot be computed until after the OCR that this cache exists to avoid.


def _cache_get(coll, file_sha256: str) -> dict | None:
    try:
        doc = coll.find_one({"file_sha256": file_sha256}, sort=[("_id", -1)])
    except Exception:
        return None
    if not doc or not doc.get("pages"):
        return None
    return {"pages": doc["pages"], "truncated": bool(doc.get("truncated", False))}


def _cache_put(coll, file_sha256: str, filename: str, result: dict) -> None:
    try:
        coll.insert_one({
            "file_sha256": file_sha256,
            "filename": filename,
            "pages": result["pages"],
            "truncated": result["truncated"],
            "dpi": DPI,
        })
    except Exception:
        pass


def bfsi_ocr_pdf_cached(path: Path, file_sha256: str) -> dict:
    """OCR a PDF, reusing an earlier read of the same bytes when there is one."""
    coll = get_db().bfsi_ocr_cache
    hit = _cache_get(coll, file_sha256)
    if hit is not None:
        return hit
    result = bfsi_ocr_pdf(path)
    if result["pages"]:
        _cache_put(coll, file_sha256, path.name, result)
    return result
