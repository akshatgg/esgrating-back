# app/bfsi/router_admin.py -- port of the BFSI parts of dashboard/index.php,
# admin/calculator.php, admin/import.php, admin/analyze.php, admin/report.php:17,
# admin/one_pager.php and admin/download.php (docs/analysis/bfsi.md §3).
import csv
import io
import math
import re
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.auth.deps import require_admin
from app.bfsi import store
from app.bfsi.options import INDUSTRIES, LOAN_PURPOSES, LOAN_TYPES, MAX_UPLOAD_MB
from app.bfsi import scoring as bfsi_scoring
from app.bfsi.scoring import bfsi_overall, bfsi_recommendation
from app.bfsi.submission import MAX_UPLOAD_BYTES, run_bfsi_analysis, store_submission
from app.core.config import settings
from app.core.errors import UserError
from app.core.jobs import start_job
from app.core.mail import Attachment, mail_configured, resolve_recipient, send_mail
from app.reports import summary as rating_summary
from app.core.mail_templates import compose, reset_template, save_template, template_response
from app.core.net import client_ip
from app.core.page_scores import csv_response, keywords_cell, kpis_cell
from app.core.uploads import read_limited, upload_path
from app.mailtpl import bfsi_report_mail

router = APIRouter(prefix="/api/admin/bfsi", tags=["admin-bfsi"])

PAGE_SIZE = 50
MAX_SEND_PDF_BYTES = 15 * 1024 * 1024
# admin/import.php had no size cap of its own; this only bounds memory use.
MAX_IMPORT_BYTES = 5 * 1024 * 1024
# Order matches the front end: the detailed report is rendered first, then the
# one-pager (web-task-W7-brief.md "getPdfs renders both ... then generates both PDFs").
SEND_FILE_NAMES = ["bfsi-detailed-report-{id}.pdf", "esg-rating-report-{id}.pdf"]

# admin/import.php's exact 12-column header (bfsi.md §3).
IMPORT_HEADER = [
    "borrower_name", "cin_gstin", "contact_email", "industry", "sub_sector",
    "loan_amount", "loan_purpose", "loan_type", "outstanding_loans",
    "overall_score", "grade", "status",
]
VALID_STATUSES = {"new", "report_generated", "sent"}


def _serialize(obj):
    """ObjectIds -> str, datetimes -> ISO. Port of app.py's convert_mongo_types."""
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(v) for v in obj]
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def _get_submission_or_404(id_: str) -> dict:
    doc = store.get_submission(id_)
    if not doc:
        raise HTTPException(404, "Not found")
    return doc


# --- submissions ---------------------------------------------------------------------

@router.get("/submissions")
def list_submissions(search: str = "", page: int = 1, admin: str = Depends(require_admin)):
    page = max(page, 1)
    total = store.count_submissions(search)
    items = store.list_submissions(search, (page - 1) * PAGE_SIZE, PAGE_SIZE)
    pages = math.ceil(total / PAGE_SIZE) if total else 0
    return {"items": [_serialize(d) for d in items], "total": total, "page": page, "pages": pages}


@router.post("/submissions", status_code=201)
async def create_submission(
    request: Request,
    borrower_name: str = Form(""),
    cin_gstin: str = Form(""),
    contact_email: str = Form(""),
    industry: str = Form(""),
    sub_sector: str = Form(""),
    loan_amount: str = Form(""),
    loan_purpose: str = Form(""),
    loan_type: str = Form(""),
    outstanding_loans: str = Form(""),
    report_file: UploadFile | None = File(None),
    admin: str = Depends(require_admin),
):
    """admin/calculator.php: same fields as the public form, no CSRF, no rate limit,
    no mail; redirects (here: starts analysis) on success."""
    data = (
        await read_limited(report_file, MAX_UPLOAD_BYTES, f"Report must be under {MAX_UPLOAD_MB} MB.")
        if report_file is not None else b""
    )
    filename = report_file.filename if report_file is not None else ""
    form = {
        "borrower_name": borrower_name,
        "cin_gstin": cin_gstin,
        "contact_email": contact_email,
        "industry": industry,
        "sub_sector": sub_sector,
        "loan_amount": loan_amount,
        "loan_purpose": loan_purpose,
        "loan_type": loan_type,
        "outstanding_loans": outstanding_loans,
    }
    ip = client_ip(request)
    result = store_submission(form, filename, data, ip)
    oid = ObjectId(result["id"])
    # A new assessment is always scored fresh, like Analyze's default (use_cache=False).
    start_job("bfsi_submissions", oid, lambda: run_bfsi_analysis(oid, use_cache=False))
    return {"id": result["id"]}


class RecordIn(BaseModel):
    """dashboard/index.php POST create_bfsi (the "Add New" modal)."""
    borrower_name: str = ""
    cin_gstin: str = ""
    contact_email: str = ""
    industry: str = ""
    sub_sector: str = ""
    loan_amount: float | None = None
    outstanding_loans: float | None = 0
    loan_purpose: str = ""
    loan_type: str = ""
    overall_score: float | None = None
    grade: str | None = None
    status: str = "new"


@router.post("/records")
def create_record(payload: RecordIn, admin: str = Depends(require_admin)):
    """Validates only industry/purpose/type, matching create_bfsi -- an invalid record
    is silently skipped (no insert, {"inserted": false}), everything else is trusted."""
    if (
        payload.industry not in INDUSTRIES
        or payload.loan_purpose not in LOAN_PURPOSES
        or payload.loan_type not in LOAN_TYPES
    ):
        return {"inserted": False}

    doc = {
        "borrower_name": payload.borrower_name.strip(),
        "cin_gstin": payload.cin_gstin.strip().upper(),
        "contact_email": payload.contact_email.strip(),
        "industry": payload.industry,
        "sub_sector": payload.sub_sector.strip(),
        "loan_amount": payload.loan_amount,
        "outstanding_loans": payload.outstanding_loans if payload.outstanding_loans is not None else 0,
        "loan_purpose": payload.loan_purpose,
        "loan_type": payload.loan_type,
        "overall_score": payload.overall_score,
        "grade": payload.grade or None,
        # dashboard/index.php's create_bfsi whitelists status to these three values,
        # defaulting to "new" for anything else (including missing).
        "status": payload.status if payload.status in VALID_STATUSES else "new",
        "answers": [],
        "file_path": "",
        "file_sha256": "",
        "submit_ip": "",
    }
    sub_id = store.insert_submission(doc)
    return {"inserted": True, "id": sub_id}


def _blank_to_none(value: str) -> str | None:
    value = value.strip()
    return None if value == "" else value


def _php_float(value: str) -> float:
    """Mimic PHP's (float) cast: parse the longest leading numeric prefix (with an
    optional sign, decimal point, and exponent); blank or non-numeric text becomes 0.0,
    never raises. admin/import.php casts loan_amount this way unconditionally."""
    s = value.strip()
    m = re.match(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?", s)
    if not m or not m.group(0):
        return 0.0
    try:
        return float(m.group(0))
    except ValueError:
        return 0.0


def _float_or_none(value: str) -> float | None:
    """overall_score is only null for a blank cell (import.php:84); a non-blank but
    unparseable cell still becomes 0.0 via PHP's forgiving (float) cast."""
    value = value.strip()
    if value == "":
        return None
    return _php_float(value)


@router.post("/import")
async def import_csv(file: UploadFile = File(...), admin: str = Depends(require_admin)):
    """Port of admin/import.php (bfsi.md §3). Header must match exactly; rows are
    skipped (not rejected) for the listed reasons; everything collected is inserted in
    one go, matching the PHP's single insertMany."""
    filename = file.filename or ""
    if not filename.lower().endswith(".csv"):
        raise UserError("Invalid file type. Please upload a .csv file.")

    data = await read_limited(file, MAX_IMPORT_BYTES, "The uploaded file is too large.")
    if not data:
        raise UserError("File upload failed or file was not received.")

    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise UserError("Failed to open CSV file.")

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise UserError("Failed to open CSV file.")

    if [h.strip() for h in header] != IMPORT_HEADER:
        raise UserError(
            "Error: CSV header row does not match the expected format. Expected: "
            + ",".join(IMPORT_HEADER)
        )

    inserted, skipped = 0, 0
    docs = []
    for row in reader:
        if not row:
            continue
        if len(row) < 12:
            skipped += 1
            continue

        (borrower_name, cin_gstin, contact_email, industry, sub_sector, loan_amount,
         loan_purpose, loan_type, outstanding_loans, overall_score, grade, status) = row[:12]

        borrower_name = borrower_name.strip()
        industry = industry.strip()
        loan_purpose = loan_purpose.strip()
        loan_type = loan_type.strip()

        if not borrower_name or industry not in INDUSTRIES or loan_purpose not in LOAN_PURPOSES \
                or loan_type not in LOAN_TYPES:
            skipped += 1
            continue

        status = status.strip()
        if status not in VALID_STATUSES:
            status = "new"

        docs.append({
            "borrower_name": borrower_name,
            "cin_gstin": cin_gstin.strip().upper(),
            "contact_email": contact_email.strip(),
            "industry": industry,
            "sub_sector": sub_sector.strip(),
            "loan_amount": _php_float(loan_amount),
            "loan_purpose": loan_purpose,
            "loan_type": loan_type,
            "outstanding_loans": _php_float(outstanding_loans),
            "overall_score": _float_or_none(overall_score),
            "grade": _blank_to_none(grade),
            "status": status,
            "answers": [],
            "file_path": "",
            "file_sha256": "",
            "submit_ip": "",
            "imported": True,
            "created_at": datetime.now(timezone.utc),
        })
        inserted += 1

    if docs:
        try:
            store.submissions_collection().insert_many(docs)
        except Exception as e:
            raise UserError(f"Database Error during CSV import: {e}")

    return {"message": f"Import complete: {inserted} row(s) inserted, {skipped} row(s) skipped."}


@router.get("/submissions/{id}")
def get_submission(id: str, admin: str = Depends(require_admin)):
    """{submission, overall, recommendation, previous, industry_label}. Recomputes
    overall from the stored E/S/G on every load, matching report.php:17."""
    doc = _get_submission_or_404(id)

    overall = None
    if all(k in doc for k in ("e_score", "s_score", "g_score")):
        overall = bfsi_overall(doc["e_score"], doc["s_score"], doc["g_score"], doc.get("loan_type", ""),
                               bfsi_scoring.is_kpi_scored(doc.get("ai_analysis")))
    recommendation = bfsi_recommendation(overall["grade"]) if overall else None

    previous = None
    prev_doc = store.previous_scored(doc.get("cin_gstin", ""), doc["_id"])
    if prev_doc:
        previous = {"overall": prev_doc.get("overall_score"), "created_at": _serialize(prev_doc.get("created_at"))}

    industry_label = INDUSTRIES.get(doc.get("industry", ""), {}).get("label", doc.get("industry", ""))

    return {
        "submission": _serialize(doc),
        "overall": overall,
        "recommendation": recommendation,
        "previous": previous,
        "industry_label": industry_label,
    }


@router.post("/submissions/{id}/analyze", status_code=202)
def analyze_submission(id: str, use_cache: bool = False, admin: str = Depends(require_admin)):
    """Scores the report fresh by default; ?use_cache=true reuses a cached result for the
    same text when there is one."""
    doc = _get_submission_or_404(id)
    oid = doc["_id"]
    started = start_job("bfsi_submissions", oid, lambda: run_bfsi_analysis(oid, use_cache=use_cache))
    if not started:
        raise HTTPException(409, "Analysis is already running.")
    return {"started": True}


class MailTemplateIn(BaseModel):
    subject: str
    body: str


@router.get("/mail-template")
def get_mail_template(admin: str = Depends(require_admin)):
    """The Send report email (app/core/mail_templates.py), editable in the Send dialog."""
    return template_response("bfsi_report")


@router.put("/mail-template")
def put_mail_template(payload: MailTemplateIn, admin: str = Depends(require_admin)):
    save_template("bfsi_report", payload.subject, payload.body, admin)
    return template_response("bfsi_report")


@router.delete("/mail-template")
def reset_mail_template(admin: str = Depends(require_admin)):
    reset_template("bfsi_report")
    return template_response("bfsi_report")


@router.get("/submissions/{id}/export_csv")
def export_page_scores(id: str, admin: str = Depends(require_admin)):
    """Page-by-page scores of the latest analysis run, same columns as the ESG export:
    one row per scored page and category, grouped by category, then the KPI summary
    (best KPI scores, category totals, overall score with the loan type's weights)."""
    doc = _get_submission_or_404(id)
    run = store.report_collection().find_one(
        {"submission_id": doc["_id"], "pages": {"$exists": True}}, sort=[("_id", -1)]
    )
    if not run:
        raise HTTPException(
            404, "Page-by-page scores aren't available for this report yet — re-run the analysis to generate them."
        )
    rows = []
    for cat in ("Environment", "Social", "Governance"):
        for unit in run["pages"]:
            result = (unit.get("categories") or {}).get(cat)
            if not result:
                continue
            rows.append([
                run.get("filename", ""),
                cat,
                unit.get("text", ""),
                unit.get("page_no", ""),
                result.get("reason", ""),
                result.get("score", ""),
                kpis_cell(result.get("kpis")),
                keywords_cell(result.get("positive_keywords")),
                keywords_cell(result.get("negative_keywords")),
                result.get("review", ""),
            ])
    if all(k in doc for k in ("e_score", "s_score", "g_score")):
        rows.extend(bfsi_scoring.summary_rows(doc.get("ai_analysis"), doc["e_score"], doc["s_score"],
                                              doc["g_score"], doc.get("loan_type", "")))
    return csv_response(rows, f"bfsi_{id}_page_scores.csv")


@router.delete("/submissions/{id}")
def delete_submission(id: str, admin: str = Depends(require_admin)):
    _get_submission_or_404(id)
    return store.delete_submission(id)


@router.get("/submissions/{id}/file")
def download_submission_file(id: str, admin: str = Depends(require_admin)):
    doc = _get_submission_or_404(id)
    try:
        path = upload_path("bfsi", doc["file_path"])
    except FileNotFoundError:
        raise HTTPException(404, "Not found")
    # download.php sends the stored basename, not the submitter's original filename.
    return FileResponse(path, filename=doc["file_path"], media_type="application/octet-stream")


@router.post("/submissions/{id}/send")
async def send_report(id: str, pdfs: list[UploadFile] = File(...), email: str = Form(""),
                      subject: str = Form(""), body: str = Form(""),
                      admin: str = Depends(require_admin)):
    """Mails both reports to `email` -- the address typed in the Send dialog -- else
    the submission's contact email. `subject`/`body` are the dialog's text; blank
    means the saved template (app/core/mail_templates.py)."""
    doc = _get_submission_or_404(id)
    if not doc.get("ai_analysis"):
        raise HTTPException(409, "Run the analysis before sending the report.")
    to = resolve_recipient(email, doc.get("contact_email"))
    mail_subject, mail_body = compose("bfsi_report", subject, body, {"borrower": doc.get("borrower_name")})
    if not mail_configured():
        raise HTTPException(503, "Mail is not configured")

    attachments = []
    for i, f in enumerate(pdfs[:2]):
        data = await read_limited(f, MAX_SEND_PDF_BYTES, "The uploaded file is too large.")
        if not data.startswith(b"%PDF"):
            raise UserError("File content does not match its type.")
        attachments.append(Attachment(SEND_FILE_NAMES[i].format(id=id), data, "application/pdf"))

    # The Rating Summary (.docx) goes with the reports when the report has KPI scores.
    if rating_summary.available("bfsi", doc):
        attachments.append(Attachment(rating_summary.filename("bfsi", doc),
                                      rating_summary.build_summary("bfsi", doc), rating_summary.DOCX_MIME))

    send_mail([to], mail_subject, mail_body, cc=[settings.team_email], attachments=attachments)

    store.submissions_collection().update_one(
        {"_id": doc["_id"]}, {"$set": {"status": "sent", "sent_at": datetime.now(timezone.utc), "sent_to": to}}
    )
    return {"ok": True}
