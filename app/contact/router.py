# app/contact/router.py
# Public contact form (Formidable 7 on /contact-03/, docs/analysis/content.md "Contact
# form") reimplemented as a JSON API: validated, rate-limited, stored in
# `contact_messages`, and best-effort emailed to the team.
import math
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth.deps import require_admin
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import UserError
from app.core.mail import send_mail_best_effort
from app.core.net import client_ip
from app.esg.submissions import EMAIL_RE
from app.mailtpl import contact_notice

router = APIRouter(tags=["contact"])

RATE_LIMIT_WINDOW_MINUTES = 60
RATE_LIMIT_MAX = 5
PAGE_SIZE = 50


def _collection():
    return get_db()["contact_messages"]


class ContactIn(BaseModel):
    name: str = ""
    email: str = ""
    number: str = ""
    message: str = ""


def _serialize(doc: dict) -> dict:
    out = dict(doc)
    out["_id"] = str(out["_id"])
    if isinstance(out.get("created_at"), datetime):
        out["created_at"] = out["created_at"].isoformat()
    return out


@router.post("/api/contact", status_code=201)
def submit_contact(payload: ContactIn, request: Request):
    name = payload.name.strip()
    email = payload.email.strip()
    number = payload.number.strip()
    message = payload.message.strip()

    if not name or not email or not message:
        raise UserError("Please fill in all required fields.")
    if not EMAIL_RE.match(email):
        raise UserError("Please enter a valid email")

    ip = client_ip(request)
    since = datetime.now(timezone.utc) - timedelta(minutes=RATE_LIMIT_WINDOW_MINUTES)
    recent = _collection().count_documents({"submit_ip": ip, "created_at": {"$gt": since}})
    if recent >= RATE_LIMIT_MAX:
        raise HTTPException(429, "Too many submissions — please try again later.")

    doc = {
        "name": name,
        "email": email,
        "number": number,
        "message": message,
        "submit_ip": ip,
        "created_at": datetime.now(timezone.utc),
    }
    _collection().insert_one(doc)

    subject, body = contact_notice({"name": name, "email": email, "number": number, "message": message})
    send_mail_best_effort([settings.team_email], subject, body)

    return {"ok": True}


@router.get("/api/admin/contacts")
def list_contacts(page: int = 1, admin: str = Depends(require_admin)):
    page = max(page, 1)
    total = _collection().count_documents({})
    items = list(
        _collection().find().sort("_id", -1).skip((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    )
    pages = math.ceil(total / PAGE_SIZE) if total else 0
    return {"items": [_serialize(d) for d in items], "total": total, "page": page, "pages": pages}
