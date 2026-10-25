"""Annual reports are often AES-encrypted with no open password: anyone can read them, but
copying and editing are locked. PyPDF2 needs PyCryptodome to decrypt AES; without it every
page failed to read and the analysis stopped with "No text extracted from the input file
or URL" (user, 2026-10-25)."""
import io

from pypdf import PdfReader, PdfWriter

from app.esg.extract import extract_text_from_pdf
from tests.fixtures.make_pdf import make_pdf


def _aes_locked(pdf: bytes) -> bytes:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf)))
    writer.encrypt(user_password="", owner_password="owner-only", algorithm="AES-128")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_reads_an_aes_encrypted_report_with_no_open_password():
    pages = extract_text_from_pdf(io.BytesIO(_aes_locked(make_pdf(["Scope 1 emissions", "Board diversity"]))))

    assert [p["page_no"] for p in pages] == [1, 2]
    assert "Scope 1 emissions" in pages[0]["text"]
    assert "Board diversity" in pages[1]["text"]
