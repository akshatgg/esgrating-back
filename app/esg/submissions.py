# app/esg/submissions.py
# Validation, storage and the /add_user-equivalent analysis run for the public ESG
# submission form. Regexes and messages are verbatim from esg-report.php / the CF7 form
# (docs/analysis/esg.md B1, B4).
import re
from datetime import datetime, timezone

from bson import ObjectId

from app.core.config import settings
from app.core.db import get_db
from app.core.errors import UserError
from app.core.mail import Attachment, send_mail_best_effort
from app.core.uploads import save_upload, upload_path
from app.esg import store
from app.esg.pipeline import calculate_esg_score_concurrent
from app.mailtpl import esg_team_notice
from app.reports.logo import delete_logo_file

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
# Any country: optional +, then 7-15 digits (E.164 maximum). Separators the user may
# type are ignored before matching.
MOBILE_RE = re.compile(r"^\+?\d{7,15}$")
PHONE_SEPARATORS_RE = re.compile(r"[\s\-().]")
REPORT_YEAR_RE = re.compile(r"^\d{4}-\d{4}$")

REQUIRED_FIELDS = ("name", "email", "designation", "company_name", "mobile_number", "report_year")

MAX_FILE_BYTES = 20 * 1024 * 1024
ALLOWED_EXTENSIONS = {
    "pdf": b"%PDF",
    "docx": b"PK",
}


def esg_submissions_collection():
    return get_db()["esg_submissions"]


def validate_esg_fields(form: dict) -> dict:
    for field in REQUIRED_FIELDS:
        if not (form.get(field) or "").strip():
            raise UserError("Please fill in all required fields.")
    if not EMAIL_RE.match(form["email"].strip()):
        raise UserError("Please enter a valid email")
    if not MOBILE_RE.match(PHONE_SEPARATORS_RE.sub("", form["mobile_number"].strip())):
        raise UserError("Please enter a valid phone number")
    if not REPORT_YEAR_RE.match(form["report_year"].strip()):
        raise UserError("Report financial year must look like 2024-2025")
    return form


def validate_esg_file(filename: str, data: bytes) -> str:
    if not filename or not data:
        raise UserError("Please upload your report.")
    if len(data) > MAX_FILE_BYTES:
        raise UserError("The uploaded file is too large.")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    magic = ALLOWED_EXTENSIONS.get(ext)
    if magic is None:
        raise UserError("You are not allowed to upload files of this type.")
    if not data.startswith(magic):
        raise UserError("File content does not match its type.")
    return ext


def create_esg_submission(form: dict, filename: str, data: bytes, ip: str, notify: bool) -> str:
    validate_esg_fields(form)
    ext = validate_esg_file(filename, data)
    stored_name, sha256 = save_upload("esg", data, ext)
    doc = {
        "name": form["name"].strip(),
        "email": form["email"].strip(),
        "designation": form["designation"].strip(),
        "company_name": form["company_name"].strip(),
        "mobile_number": form["mobile_number"].strip(),
        "report_year": form["report_year"].strip(),
        "file_path": stored_name,
        "file_sha256": sha256,
        "original_filename": filename,
        "submit_ip": ip,
        "status": "new",
        "analysis_status": "idle",
        "created_at": datetime.now(timezone.utc),
    }
    result = esg_submissions_collection().insert_one(doc)
    if notify:
        subject, body = esg_team_notice(form, filename)
        send_mail_best_effort(
            [settings.team_email],
            subject,
            body,
            attachments=[Attachment(filename, data, "application/pdf" if ext == "pdf" else
                                     "application/vnd.openxmlformats-officedocument.wordprocessingml.document")],
        )
    return str(result.inserted_id)


def run_esg_analysis(sub_id: ObjectId) -> None:
    """Reproduces /add_user (esg_score_calculator-master/app.py:218-267)."""
    sub = esg_submissions_collection().find_one({"_id": sub_id})
    if not sub:
        raise RuntimeError("Submission not found")

    data = upload_path("esg", sub["file_path"]).read_bytes()
    company_id = store.insert_user(
        sub["name"], sub["email"], sub["company_name"], sub["mobile_number"], [sub["original_filename"]]
    )
    if not company_id:
        raise RuntimeError("User insertion failed.")

    result = calculate_esg_score_concurrent([(sub["original_filename"], data)], company_id, sub["report_year"])
    if "error" in result or result.get("status") == "error":
        raise RuntimeError(result.get("message") or result.get("error"))

    year_score = store.get_esg_score(company_id)

    # A re-run starts clean: report edits and their snapshot belong to the previous result.
    before = esg_submissions_collection().find_one_and_update(
        {"_id": sub_id},
        {"$set": {
            "final": result,
            "year_score": year_score,
            "company_id": company_id,
            "status": "report_generated",
            "analyzed_at": datetime.now(timezone.utc),
        }, "$unset": {"report_edits": "", "report_original": ""}},
        projection={"report_edits": 1},
    )
    delete_logo_file(((before or {}).get("report_edits") or {}).get("logo"))


def serialize_doc(obj):
    """ObjectIds -> str, datetimes -> ISO. Port of app.py's convert_mongo_types."""
    if isinstance(obj, dict):
        return {k: serialize_doc(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [serialize_doc(v) for v in obj]
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj
