import logging, re, smtplib, ssl
from dataclasses import dataclass
from email.message import EmailMessage
from app.core.config import settings
from app.core.errors import UserError

log = logging.getLogger(__name__)

# One address only: no spaces, commas or semicolons, so a recipient field cannot
# smuggle in extra addresses.
_EMAIL_RE = re.compile(r"^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$")


def resolve_recipient(override: str | None, stored: str | None) -> str:
    """Who a report is sent to: the address the admin typed in the Send dialog, else
    the one the submitter gave on the form. Raises UserError (422) when neither is a
    valid single address."""
    to = (override or "").strip() or (stored or "").strip()
    if not to:
        raise UserError("Enter an email address to send the report to.")
    if not _EMAIL_RE.match(to):
        raise UserError("Please enter a valid email")
    return to


class MailError(Exception):
    pass


@dataclass
class Attachment:
    filename: str
    content: bytes
    mime: str = "application/octet-stream"


def mail_configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_user and settings.smtp_password)


def send_mail(to, subject, body, *, cc=None, reply_to=None, attachments=None) -> None:
    if not mail_configured():
        raise MailError("Mail is not configured")
    try:
        msg = EmailMessage()
        msg["From"] = settings.mail_from
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        if reply_to:
            msg["Reply-To"] = reply_to
        msg["Subject"] = subject
        msg.set_content(body)
        for a in attachments or []:
            maintype, _, subtype = a.mime.partition("/")
            msg.add_attachment(a.content, maintype=maintype, subtype=subtype or "octet-stream", filename=a.filename)
        if settings.smtp_ssl:
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=ssl.create_default_context(), timeout=30) as s:
                s.login(settings.smtp_user, settings.smtp_password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(settings.smtp_user, settings.smtp_password)
                s.send_message(msg)
    except Exception as e:
        raise MailError(str(e)) from e


def send_mail_best_effort(to, subject, body, **kw) -> bool:
    try:
        send_mail(to, subject, body, **kw)
        return True
    except Exception as e:  # never break the caller's request over mail
        log.warning("mail not sent (%s): %s", subject, e)
        return False
