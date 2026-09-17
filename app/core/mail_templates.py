# app/core/mail_templates.py
# The Send report email templates, editable from the admin's Send dialog and saved
# for future sends (mail_templates collection, one document per template). Until an
# admin saves their own, the defaults in app/mailtpl.py are used. {placeholders} are
# filled with each report's own values when it is sent.
from datetime import datetime, timezone

from app.core.db import get_db
from app.core.errors import UserError
from app.mailtpl import BFSI_REPORT_BODY, BFSI_REPORT_SUBJECT, ESG_REPORT_BODY, ESG_REPORT_SUBJECT

MAX_SUBJECT = 200
MAX_BODY = 10000

TEMPLATES = {
    "esg_report": {
        "subject": ESG_REPORT_SUBJECT,
        "body": ESG_REPORT_BODY,
        "placeholders": {
            "name": "the submitter's name",
            "company": "the company name",
            "year": "the report's financial year",
        },
    },
    "bfsi_report": {
        "subject": BFSI_REPORT_SUBJECT,
        "body": BFSI_REPORT_BODY,
        "placeholders": {"borrower": "the borrower's name"},
    },
}


def _collection():
    return get_db()["mail_templates"]


def get_template(key: str) -> dict:
    """{subject, body, is_default, updated_at, updated_by}: the saved version, else the default."""
    saved = _collection().find_one({"_id": key})
    if saved:
        return {"subject": saved["subject"], "body": saved["body"], "is_default": False,
                "updated_at": saved.get("updated_at"), "updated_by": saved.get("updated_by")}
    default = TEMPLATES[key]
    return {"subject": default["subject"], "body": default["body"], "is_default": True,
            "updated_at": None, "updated_by": None}


def template_response(key: str) -> dict:
    t = get_template(key)
    return {
        "subject": t["subject"],
        "body": t["body"],
        "is_default": t["is_default"],
        "updated_at": t["updated_at"].isoformat() if t["updated_at"] else None,
        "updated_by": t["updated_by"],
        "placeholders": [{"key": k, "label": v} for k, v in TEMPLATES[key]["placeholders"].items()],
    }


def clean(subject: str, body: str) -> tuple[str, str]:
    """A subject on one line (it is a mail header) and a body with plain newlines."""
    subject = " ".join((subject or "").split())
    body = (body or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not subject:
        raise UserError("Enter a subject for the email.")
    if not body:
        raise UserError("Enter a message for the email.")
    if len(subject) > MAX_SUBJECT:
        raise UserError(f"The subject must be {MAX_SUBJECT} characters or fewer.")
    if len(body) > MAX_BODY:
        raise UserError(f"The message must be {MAX_BODY} characters or fewer.")
    return subject, body


def save_template(key: str, subject: str, body: str, admin: str) -> None:
    subject, body = clean(subject, body)
    _collection().update_one(
        {"_id": key},
        {"$set": {"subject": subject, "body": body,
                  "updated_at": datetime.now(timezone.utc), "updated_by": admin}},
        upsert=True,
    )


def reset_template(key: str) -> None:
    """Back to the default text in app/mailtpl.py."""
    _collection().delete_one({"_id": key})


def render(text: str, values: dict) -> str:
    """Fill {placeholders} by plain replacement: braces elsewhere in the text are left
    alone, and an unknown {placeholder} stays as typed."""
    for k, v in values.items():
        text = text.replace("{" + k + "}", str(v or ""))
    return text


def compose(key: str, subject: str, body: str, values: dict) -> tuple[str, str]:
    """The subject and body to send: the text typed in the Send dialog when there is
    any, else the saved (or default) template -- with the placeholders filled in."""
    if (subject or "").strip() or (body or "").strip():
        subject, body = clean(subject, body)
    else:
        t = get_template(key)
        subject, body = t["subject"], t["body"]
    return " ".join(render(subject, values).split()), render(body, values)
