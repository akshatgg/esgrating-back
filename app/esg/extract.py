# Port of esg_score_calculator-master/utils/read_content_utils.py.
# Divergences (approved): inputs are (filename, bytes) tuples instead of starlette
# UploadFiles; DOCX yields one page instead of extending the list with characters.
# scrape_website is dropped (unreachable: /add_user only ever passed UploadFiles).
# PyPDF2 is pinned to 3.0.1 (pyproject.toml) -- the version unpinned production resolves
# to -- so page text and the sha256 cache hash match production exactly.
import io
import logging

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


def process_files(files: list[tuple[str, bytes]]) -> list[dict] | str:
    normal_text = ""
    all_text = []
    for filename, data in files:
        logger.info(filename)
        if filename.lower().endswith('.pdf'):
            extracted_pages = extract_text_from_pdf(io.BytesIO(data))
            all_text.extend([
                {"page_no": page_data["page_no"], "text": page_data["text"]}
                for page_data in extracted_pages if page_data["text"].strip()
            ])
        elif filename.lower().endswith('.docx'):
            # Agreed fix: the original did `all_text += read_docx(...)`, extending the list
            # with single characters (broken downstream). One page, same empty-page rule.
            content = read_docx(io.BytesIO(data))
            if content.strip():
                all_text.append({"page_no": 1, "text": content})
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
