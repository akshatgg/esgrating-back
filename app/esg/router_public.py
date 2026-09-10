# app/esg/router_public.py
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from app.core.net import client_ip
from app.core.uploads import read_limited
from app.esg.submissions import MAX_FILE_BYTES, create_esg_submission, esg_submissions_collection

router = APIRouter(prefix="/api/esg", tags=["esg"])

RATE_LIMIT_WINDOW_MINUTES = 60
RATE_LIMIT_MAX_SUBMISSIONS = 5


@router.post("/submissions", status_code=201)
async def submit_esg(
    request: Request,
    name: str = Form(""),
    email: str = Form(""),
    designation: str = Form(""),
    company_name: str = Form(""),
    mobile_number: str = Form(""),
    report_year: str = Form(""),
    file: UploadFile | None = File(None),
):
    ip = client_ip(request)
    since = datetime.now(timezone.utc) - timedelta(minutes=RATE_LIMIT_WINDOW_MINUTES)
    recent = esg_submissions_collection().count_documents({"submit_ip": ip, "created_at": {"$gt": since}})
    if recent >= RATE_LIMIT_MAX_SUBMISSIONS:
        raise HTTPException(429, "Too many submissions — please try again later.")

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
    sub_id = create_esg_submission(form, filename, data, ip, notify=True)
    return {"ok": True, "id": sub_id}
