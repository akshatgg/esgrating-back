# Port of esg_score_calculator-master/utils/read_content_utils.py.
# Divergences (approved): inputs are (filename, bytes) tuples instead of starlette
# UploadFiles; DOCX yields one page instead of extending the list with characters.
# scrape_website is dropped (unreachable: /add_user only ever passed UploadFiles).
# PyPDF2 is pinned to 3.0.1 (pyproject.toml) -- the version unpinned production resolves
# to -- so page text and the sha256 cache hash match production exactly.
import io
import logging
from collections import Counter

from PyPDF2 import PdfReader
from docx import Document

logger = logging.getLogger(__name__)


# Function to extract text from a PDF
def extract_text_from_pdf(file):
    text_with_pages = []
    try:
        reader = PdfReader(file)
        logger.info(f"Number of pages: {len(reader.pages)}")
        for page_no, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            text_with_pages.append({"page_no": page_no, "text": text})
    except Exception as e:
        logger.error(f"Error reading PDF: {e}")
    return text_with_pages


# Function to extract text from a DOCX
def read_docx(file):
    content = ""
    try:
        doc = Document(file)
        for para in doc.paragraphs:
            content += para.text
    except Exception as e:
        logger.error(f"Error reading DOCX: {e}")
    return content


# --- The number printed on the page ------------------------------------------------------
#
# enumerate() above counts SHEETS of the PDF, from the cover. A report prints its own folio
# on the page, and that numbering begins after the front matter, so the two differ by a
# constant: on the annual report this was first measured against, sheet 34 is the page
# printed 32. Every citation we wrote therefore read two pages ahead of where the reader
# looked (user, 2026-09-29).
#
# The offset belongs to the document, not to a page, so it is measured once across the whole
# file by voting. On each sheet, a number standing alone on its own line is a candidate
# folio and votes for the offset it implies. The real folio appears on nearly every content
# page and wins by a wide margin; stray numbers out of tables scatter across offsets and
# cancel out.
#
# A file whose folios cannot be read -- an image-only scan, a deck, a DOCX -- yields no
# winner, and its pages are cited by sheet exactly as before.

# A folio further than this from its sheet is not front matter, it is a coincidence.
FOLIO_MAX_OFFSET = 60
# Below either of these the winner is noise rather than a numbering scheme.
MIN_FOLIO_VOTES = 5
MIN_FOLIO_SHARE = 0.25


def _folio_candidates(text: str) -> set[int]:
    """Numbers standing alone on a line -- how a printed page folio extracts."""
    found = set()
    for line in (text or "").splitlines():
        token = line.strip().strip(".|-\u2013\u2014 ").strip()
        if token.isdigit() and len(token) <= 4:
            found.add(int(token))
    return found


def folio_offset(pages: list[dict]) -> int:
    """sheet - printed folio for this document, or 0 when it cannot be established.

    Only non-negative offsets are considered: front matter means the folio runs behind the
    sheet, never ahead of it, and allowing the other direction would let a table of figures
    outvote the real numbering."""
    votes: Counter[int] = Counter()
    voted = 0
    for page in pages:
        sheet = page.get("page_no")
        if not isinstance(sheet, int):
            continue
        offsets = {sheet - n for n in _folio_candidates(page.get("text") or "")
                   if 0 <= sheet - n <= FOLIO_MAX_OFFSET}
        if offsets:
            voted += 1
            votes.update(offsets)
    if not votes:
        return 0
    offset, count = votes.most_common(1)[0]
    if count < MIN_FOLIO_VOTES or count < voted * MIN_FOLIO_SHARE:
        return 0
    return offset


def printed_page(sheet: int, offset: int) -> int | None:
    """The number printed on that sheet, or None for front matter -- the cover and whatever
    else sits before the report starts counting, which carries no folio to cite."""
    if not isinstance(sheet, int):
        return None
    printed = sheet - offset
    return printed if printed >= 1 else None


def number_pages(pages: list[dict]) -> list[dict]:
    """Attach `printed_no` to each page of ONE file: the folio the reader sees, or None.

    Done per file because the offset is a property of that document's front matter; two
    uploads do not share one."""
    offset = folio_offset(pages)
    for page in pages:
        page["printed_no"] = printed_page(page.get("page_no"), offset)
    return pages


def process_files(files: list[tuple[str, bytes]]) -> list[dict] | str:
    normal_text = ""
    all_text = []
    for filename, data in files:
        logger.info(filename)
        if filename.lower().endswith('.pdf'):
            extracted_pages = extract_text_from_pdf(io.BytesIO(data))
            # Numbered before the empty pages are dropped: a blank sheet still occupies a
            # sheet, so removing it first would shift every folio after it.
            all_text.extend([
                {"page_no": page_data["page_no"], "text": page_data["text"],
                 "printed_no": page_data["printed_no"]}
                for page_data in number_pages(extracted_pages) if page_data["text"].strip()
            ])
        elif filename.lower().endswith('.docx'):
            # Agreed fix: the original did `all_text += read_docx(...)`, extending the list
            # with single characters (broken downstream). One page, same empty-page rule.
            content = read_docx(io.BytesIO(data))
            if content.strip():
                # One page, and no folio to read off it: cited as sheet 1.
                all_text.append({"page_no": 1, "text": content, "printed_no": None})
        else:
            logger.info(f"Unsupported file type: {filename}")
    return all_text if all_text else normal_text


# Function to split text into manageable chunks
def split_text_into_chunks(text, max_tokens=2000):
    words = text.split()
    chunks = []
    chunk = []
    token_count = 0

    for word in words:
        token_count += 1  # Approximate tokens as words
        chunk.append(word)
        if token_count >= max_tokens:
            chunks.append(" ".join(chunk))
            chunk = []
            token_count = 0

    if chunk:  # Append the last chunk if not empty
        chunks.append(" ".join(chunk))

    return chunks
