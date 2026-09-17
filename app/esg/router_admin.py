# app/esg/router_admin.py
import ast
import csv
import io
import math
import re
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.auth.deps import require_admin
from app.core.config import settings
from app.core.errors import UserError
from app.core.jobs import start_job
from app.core.mail import Attachment, mail_configured, resolve_recipient, send_mail
from app.core.mail_templates import compose, reset_template, save_template, template_response
from app.core.net import client_ip
from app.core.page_scores import csv_response, kpis_cell
from app.core.uploads import read_limited, upload_path
from app.esg import store
from app.esg.submissions import MAX_FILE_BYTES, create_esg_submission, esg_submissions_collection, run_esg_analysis, serialize_doc
from app.mailtpl import esg_report_mail
from app.reports.editing import _esg_report_doc

router = APIRouter(prefix="/api/admin/esg", tags=["admin-esg"])

PAGE_SIZE = 50
MAX_SEND_PDF_BYTES = 15 * 1024 * 1024
# Send report attachments, in the order the page sends them: the one-page report
# (its long-standing name), then the Detailed Report.
ESG_SEND_FILE_NAMES = ["esg_report.pdf", "esg-detailed-report-{id}.pdf"]
# Submissions imported by scripts/migrate_legacy_esg.py have no stored upload.
NO_FILE_MESSAGE = "The original file isn't available for this report — it was imported from the old calculator."


def _oid(id_str: str) -> ObjectId:
    if not ObjectId.is_valid(id_str):
        raise HTTPException(404, "Not found")
    return ObjectId(id_str)


def _get_submission_or_404(sub_id: ObjectId) -> dict:
    doc = esg_submissions_collection().find_one({"_id": sub_id})
    if not doc:
        raise HTTPException(404, "Not found")
    return doc


# --- submissions -------------------------------------------------------------------

@router.get("/submissions")
def list_submissions(search: str = "", page: int = 1, admin: str = Depends(require_admin)):
    query = {}
    search = search.strip()
    if search:
        rx = {"$regex": re.escape(search), "$options": "i"}
        query = {"$or": [{"name": rx}, {"company_name": rx}, {"email": rx}]}
    total = esg_submissions_collection().count_documents(query)
    page = max(page, 1)
    cursor = (
        esg_submissions_collection()
        .find(query)
        .sort("_id", -1)
        .skip((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    )
    items = [serialize_doc(d) for d in cursor]
    pages = math.ceil(total / PAGE_SIZE) if total else 0
    return {"items": items, "total": total, "page": page, "pages": pages}


@router.post("/submissions", status_code=201)
async def create_submission(
    request: Request,
    name: str = Form(""),
    email: str = Form(""),
    designation: str = Form(""),
    company_name: str = Form(""),
    mobile_number: str = Form(""),
    report_year: str = Form(""),
    file: UploadFile | None = File(None),
    admin: str = Depends(require_admin),
):
    data = await read_limited(file, MAX_FILE_BYTES, "The uploaded file is too large.") if file is not None else b""
    filename = file.filename if file is not None else ""
    form = {
        "name": name,
        "email": email,
        "designation": designation,
        "company_name": company_name,
        "mobile_number": mobile_number,
        "report_year": report_year,
    }
    ip = client_ip(request)
    sub_id = create_esg_submission(form, filename, data, ip, notify=False)
    oid = ObjectId(sub_id)
    # A new assessment is always scored fresh, like Analyze's default (use_cache=False):
    # a cache hit has no page rows, so no page scores, KPI tables or CSV.
    start_job("esg_submissions", oid, lambda: run_esg_analysis(oid, use_cache=False))
    return {"id": sub_id}


@router.get("/submissions/{id}")
def get_submission(id: str, admin: str = Depends(require_admin)):
    doc = _get_submission_or_404(_oid(id))
    return serialize_doc(doc)


@router.post("/submissions/{id}/analyze", status_code=202)
def analyze_submission(id: str, use_cache: bool = False, admin: str = Depends(require_admin)):
    """Scores the report fresh by default; ?use_cache=true reuses a cached result for the
    same text when there is one."""
    oid = _oid(id)
    doc = _get_submission_or_404(oid)
    if not doc.get("file_path"):
        raise HTTPException(409, NO_FILE_MESSAGE)
    started = start_job("esg_submissions", oid, lambda: run_esg_analysis(oid, use_cache=use_cache))
    if not started:
        raise HTTPException(409, "Analysis is already running.")
    return {"started": True}


@router.delete("/submissions/{id}")
def delete_submission(id: str, admin: str = Depends(require_admin)):
    oid = _oid(id)
    doc = _get_submission_or_404(oid)
    esg_submissions_collection().delete_one({"_id": oid})
    if doc.get("file_path"):  # imported legacy submissions have no stored file
        try:
            upload_path("esg", doc["file_path"]).unlink()
        except FileNotFoundError:
            pass
    return {"ok": True}


@router.get("/submissions/{id}/file")
def download_submission_file(id: str, admin: str = Depends(require_admin)):
    oid = _oid(id)
    doc = _get_submission_or_404(oid)
    if not doc.get("file_path"):
        raise HTTPException(404, NO_FILE_MESSAGE)
    try:
        path = upload_path("esg", doc["file_path"])
    except FileNotFoundError:
        raise HTTPException(404, "Not found")
    filename = doc.get("original_filename") or doc["file_path"]
    return FileResponse(path, filename=filename)


@router.post("/submissions/{id}/send")
async def send_report(id: str, pdfs: list[UploadFile] = File(...), email: str = Form(""),
                      subject: str = Form(""), body: str = Form(""),
                      admin: str = Depends(require_admin)):
    """Mails the one-page ESG Rating Report and, when sent, the Detailed Report (in that
    order) to `email` -- the address typed in the Send dialog -- else the submitter's
    own. `subject`/`body` are the dialog's text; blank means the saved template
    (app/core/mail_templates.py). Same check order as the BFSI send."""
    oid = _oid(id)
    doc = _get_submission_or_404(oid)
    if not doc.get("final"):
        raise HTTPException(409, "Run the analysis before sending the report.")
    to = resolve_recipient(email, doc.get("email"))
    mail_subject, mail_body = compose("esg_report", subject, body, {
        "name": doc.get("name"), "company": doc.get("company_name"), "year": doc.get("report_year"),
    })
    if not mail_configured():
        raise HTTPException(503, "Mail is not configured")

    attachments = []
    for i, f in enumerate(pdfs[:2]):
        data = await read_limited(f, MAX_SEND_PDF_BYTES, "The uploaded file is too large.")
        if not data.startswith(b"%PDF"):
            raise UserError("File content does not match its type.")
        attachments.append(Attachment(ESG_SEND_FILE_NAMES[i].format(id=id), data, "application/pdf"))

    send_mail([to], mail_subject, mail_body, cc=[settings.team_email], attachments=attachments)
    esg_submissions_collection().update_one(
        {"_id": oid}, {"$set": {"status": "sent", "sent_at": datetime.now(timezone.utc), "sent_to": to}}
    )
    return {"ok": True}


# --- Send report email template (editable in the Send dialog) ---------------------

class MailTemplateIn(BaseModel):
    subject: str
    body: str


@router.get("/mail-template")
def get_mail_template(admin: str = Depends(require_admin)):
    return template_response("esg_report")


@router.put("/mail-template")
def put_mail_template(payload: MailTemplateIn, admin: str = Depends(require_admin)):
    save_template("esg_report", payload.subject, payload.body, admin)
    return template_response("esg_report")


@router.delete("/mail-template")
def reset_mail_template(admin: str = Depends(require_admin)):
    reset_template("esg_report")
    return template_response("esg_report")


# --- legacy (admin-only; app.py:91-214, 271-310) ------------------------------------

@router.get("/users")
def legacy_list_users(admin: str = Depends(require_admin)):
    users = []
    for user in store.user_collection().find({}):
        user["_id"] = str(user["_id"])
        users.append(user)
    return {"status": "success", "response": users}


@router.get("/reports")
def legacy_list_reports(admin: str = Depends(require_admin)):
    docs = list(store.report_hash().find({}))
    if not docs:
        raise HTTPException(404, "No reports found.")
    return {"status": "success", "response": [serialize_doc(d) for d in docs]}


@router.get("/reports/{id}")
def legacy_get_report(id: str, admin: str = Depends(require_admin)):
    oid = _oid(id)
    report = store.report_hash().find_one({"_id": oid})
    if not report:
        raise HTTPException(404, "Report not found")
    return {"status": "success", "response": serialize_doc(report)}


class ReportUpdateRequest(BaseModel):
    environmental_keywords: list[str]
    social_keywords: list[str]
    governance_keywords: list[str]
    environmental_score: float
    social_score: float
    governance_score: float
    composite_score: float
    sector: str
    industry: str
    report_date: str
    environmental_score_performance: str
    social_score_performance: str
    governance_score_performance: str
    composite_score_performance: str
    environmental_score_performance_label: str
    social_score_performance_label: str
    governance_score_performance_label: str
    composite_score_performance_label: str


@router.post("/reports/{id}")
def legacy_update_report(id: str, payload: ReportUpdateRequest, admin: str = Depends(require_admin)):
    oid = _oid(id)
    update_payload = {
        "$set": {
            "llm_response.environmental_score": payload.environmental_score,
            "llm_response.social_score": payload.social_score,
            "llm_response.governance_score": payload.governance_score,
            "llm_response.composite_score": payload.composite_score,
            "llm_response.sector": payload.sector,
            "llm_response.industry": payload.industry,
            "llm_response.environmental_top_keywords": payload.environmental_keywords,
            "llm_response.social_top_keywords": payload.social_keywords,
            "llm_response.governance_top_keywords": payload.governance_keywords,
            "llm_response.report_date": payload.report_date,
            "llm_response.environmental_score_performance": payload.environmental_score_performance,
            "llm_response.social_score_performance": payload.social_score_performance,
            "llm_response.governance_score_performance": payload.governance_score_performance,
            "llm_response.composite_score_performance": payload.composite_score_performance,
            "llm_response.environmental_score_performance_label": payload.environmental_score_performance_label,
            "llm_response.social_score_performance_label": payload.social_score_performance_label,
            "llm_response.governance_score_performance_label": payload.governance_score_performance_label,
            "llm_response.composite_score_performance_label": payload.composite_score_performance_label,
            "last_updated": datetime.now(timezone.utc),
        }
    }
    result = store.report_hash().update_one({"_id": oid}, update_payload)
    if result.matched_count == 0:
        raise HTTPException(404, "No report found with the provided ID.")
    return {"message": f"Report with ID {id} updated successfully."}


@router.get("/export_csv/{company_id}")
def legacy_export_csv(company_id: str, submission_id: str | None = None,
                      admin: str = Depends(require_admin)):
    """One analysis run's page scores. The original (app.py) exported every run of the
    company together, which put a re-run's pages next to the old ones. Now: the run behind
    ?submission_id= when given (the report editor's own lookup), else the newest run."""
    cid = _oid(company_id)
    run = None
    if submission_id:
        sub = _get_submission_or_404(_oid(submission_id))
        if sub.get("company_id") == cid and sub.get("final"):
            run = _esg_report_doc(sub, sub["final"])
    if run is None:
        run = store.esg_collection().find_one({"company_id": cid}, sort=[("_id", -1)])
    if not run:
        raise HTTPException(404, "No data found for the given company_id.")
    documents = [run]

    rows = []
    for doc in documents:
        for analysis in doc.get("analysis", []):
            try:
                # Divergence (approved): ast.literal_eval, not eval() (app.py:193-194) or
                # json.loads -- same parser pipeline.py uses, so True/False/None LLM output
                # still drops the row exactly as eval() did.
                analysis_data = ast.literal_eval(analysis) if isinstance(analysis, str) else analysis
                raw_reason = analysis_data.get("analysis")
                analysis_reason = ast.literal_eval(raw_reason) if isinstance(raw_reason, str) else raw_reason
                rows.append([
                    analysis_data.get("filename", ""),
                    analysis_data.get("category", ""),
                    analysis_data.get("text", ""),
                    analysis_data.get("page_no", ""),
                    analysis_reason.get("reason", ""),
                    analysis_reason.get("score", ""),
                    kpis_cell(analysis_data.get("kpis")),
                    ", ".join(analysis_reason.get("positive_keywords", [])),
                    ", ".join(analysis_reason.get("negative_keywords", [])),
                ])
            except Exception:
                continue

    return csv_response(rows, f"company_{company_id}_esg.csv")
