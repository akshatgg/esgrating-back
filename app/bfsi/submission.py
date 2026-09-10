# Port of bfsi-calculator/lib/submission.php::bfsi_store_submission (shared by the
# public form and the admin calculator) plus admin/analyze.php's sequence (see
# docs/analysis/bfsi.md §1c and §3 "admin/analyze.php"). Validation order and messages
# are verbatim.
import re

from bson import ObjectId

from app.bfsi import store
from app.bfsi.extract import bfsi_extract_and_fingerprint
from app.bfsi.options import INDUSTRIES, LOAN_PURPOSES, LOAN_TYPES, MAX_UPLOAD_MB
from app.bfsi.pipeline import bfsi_analyze
from app.bfsi.scoring import bfsi_overall
from app.core.errors import UserError
from app.core.uploads import save_upload, upload_path

MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

# PHP mime_content_type() checks; we use magic bytes instead (spec-approved divergence).
_MAGIC = {"pdf": b"%PDF", "docx": b"PK\x03\x04"}

_CTRL_RE = re.compile(r"[\x00-\x1F\x7F]")
_CIN_RE = re.compile(r"^([A-Z0-9]{21}|[0-9A-Z]{15})$")
# Rough equivalent of PHP's FILTER_VALIDATE_EMAIL: one "@", something before and after,
# a dot in the domain part.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_float(value) -> float | None:
    """PHP FILTER_VALIDATE_FLOAT: a parseable decimal string, else false (-> None)."""
    if value is None:
        return None
    s = str(value).strip()
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _normalize_and_validate(form: dict) -> dict:
    borrower = _CTRL_RE.sub("", (form.get("borrower_name") or "").strip())
    cin = (form.get("cin_gstin") or "").strip().upper()
    email = form.get("contact_email") or ""
    industry = form.get("industry") or ""
    sub_sector = (form.get("sub_sector") or "").strip()
    amount = _validate_float(form.get("loan_amount"))
    loan_purpose = form.get("loan_purpose") or ""
    loan_type = form.get("loan_type") or ""
    outstanding = _validate_float(form.get("outstanding_loans"))

    if not borrower or len(borrower) > 255:
        raise UserError("Borrower name is required.")
    if not _CIN_RE.match(cin):
        raise UserError("CIN must be 21 characters or GSTIN 15 characters.")
    if not _EMAIL_RE.match(email):
        raise UserError("A valid contact email is required.")
    if len(email) > 255:
        raise UserError("Contact email is too long.")
    if industry not in INDUSTRIES:
        raise UserError("Invalid industry.")
    if not sub_sector or sub_sector not in INDUSTRIES[industry]["sub_sectors"]:
        raise UserError("Invalid sub-sector.")
    if amount is None or amount <= 0:
        raise UserError("Loan amount must be positive.")
    if loan_purpose not in LOAN_PURPOSES:
        raise UserError("Invalid loan purpose.")
    if loan_type not in LOAN_TYPES:
        raise UserError("Invalid loan type.")
    if outstanding is None or outstanding < 0:
        raise UserError("Outstanding loans must be 0 or more.")

    return {
        "borrower_name": borrower,
        "cin_gstin": cin,
        "contact_email": email,
        "industry": industry,
        "sub_sector": sub_sector,
        "loan_amount": amount,
        "loan_purpose": loan_purpose,
        "loan_type": loan_type,
        "outstanding_loans": outstanding,
    }


def _validate_file(filename: str, data: bytes) -> str:
    if not filename or not data:
        raise UserError("Report upload failed.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UserError(f"Report must be under {MAX_UPLOAD_MB} MB.")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _MAGIC:
        raise UserError("Only PDF or DOCX accepted.")
    if not data.startswith(_MAGIC[ext]):
        raise UserError("File content does not match its type.")
    return ext


def store_submission(form: dict, filename: str, data: bytes, ip: str) -> dict:
    """Validate + persist a BFSI submission. Returns {"id": <hex>, "file": <stored name>}.
    Any unexpected failure becomes the same generic message submission.php shows."""
    try:
        normalized = _normalize_and_validate(form)
        ext = _validate_file(filename, data)
        try:
            stored_name, file_sha256 = save_upload("bfsi", data, ext)
        except OSError as e:
            raise UserError("Could not store the file.") from e

        doc = {
            **normalized,
            "answers": [],
            "file_path": stored_name,
            "file_sha256": file_sha256,
            "submit_ip": ip,
            "status": "new",
        }
        sub_id = store.insert_submission(doc)
        return {"id": sub_id, "file": stored_name}
    except UserError:
        raise
    except Exception as e:
        raise UserError("Could not process your submission — please try again.") from e


def run_bfsi_analysis(sub_id: ObjectId) -> None:
    """Reproduces admin/analyze.php's sequence (bfsi.md §3): extract, then a cache
    lookup by text hash, then (on a miss) bfsi_analyze + store_llm_response, then
    bfsi_overall, then the $set of scored fields, then report_insert on every run."""
    sub = store.get_submission(str(sub_id))
    if not sub:
        raise RuntimeError("Not found")

    path = upload_path("bfsi", sub["file_path"])
    extracted = bfsi_extract_and_fingerprint(path)

    ai = store.get_llm_response(extracted["text_sha256"])
    if ai is None:
        ai = bfsi_analyze(sub, extracted["pages"])
        store.store_llm_response(sub_id, sub["file_path"], extracted["text_sha256"], ai)

    ov = bfsi_overall(ai["e_score"], ai["s_score"], ai["g_score"], sub["loan_type"])

    store.submissions_collection().update_one(
        {"_id": sub_id},
        {"$set": {
            "e_score": float(ai["e_score"]),
            "s_score": float(ai["s_score"]),
            "g_score": float(ai["g_score"]),
            "overall_score": ov["overall"],
            "grade": ov["grade"],
            "ai_analysis": ai,
            "text_sha256": extracted["text_sha256"],
            "text_source": extracted["source"],
            "text_truncated": extracted["truncated"],
            "status": "report_generated",
        }},
    )

    store.report_insert(sub_id, sub["file_path"], ai.get("reasons") or [], ov["overall"])
