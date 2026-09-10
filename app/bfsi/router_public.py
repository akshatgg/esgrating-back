# app/bfsi/router_public.py -- port of bfsi-calculator/index.php + submit.php
# (docs/analysis/bfsi.md §1). GET /options serves the form's dropdowns; POST
# /submissions stores the submission and best-effort notifies the team (submit.php:43-57).
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from app.bfsi import store
from app.bfsi.options import INDUSTRIES, options_payload
from app.bfsi.submission import store_submission
from app.core.config import settings
from app.core.mail import Attachment, send_mail_best_effort
from app.core.net import client_ip
from app.mailtpl import bfsi_team_notice

router = APIRouter(prefix="/api/bfsi", tags=["bfsi"])

RATE_LIMIT_WINDOW_MINUTES = 60
RATE_LIMIT_MAX_SUBMISSIONS = 5


@router.get("/options")
def get_options():
    return options_payload()


@router.post("/submissions", status_code=201)
async def submit_bfsi(
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
):
    ip = client_ip(request)
    if store.count_recent_by_ip(ip, RATE_LIMIT_WINDOW_MINUTES) >= RATE_LIMIT_MAX_SUBMISSIONS:
        raise HTTPException(429, "Too many submissions — please try again later.")

    data = await report_file.read() if report_file is not None else b""
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
    result = store_submission(form, filename, data, ip)

    industry_label = INDUSTRIES.get(form["industry"], {}).get("label", form["industry"])
    subject, body = bfsi_team_notice(form, industry_label)
    mime = (
        "application/pdf"
        if filename.lower().endswith(".pdf")
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    # submit.php:64 attaches the stored filename (uploads/$fname), not the submitter's
    # original filename.
    send_mail_best_effort(
        [settings.team_email],
        subject,
        body,
        reply_to=form["contact_email"],
        attachments=[Attachment(result["file"], data, mime)],
    )
    return {"ok": True, "id": result["id"]}
