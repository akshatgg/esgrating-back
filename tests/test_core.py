import hashlib
from bson import ObjectId
import pytest
from starlette.requests import Request

from app.core import jobs, mail, net
from app.core.uploads import save_upload, upload_path


def test_save_upload_and_path(upload_dir):
    name, sha = save_upload("bfsi", b"hello", "pdf")
    assert name.endswith(".pdf") and "_" in name
    assert sha == hashlib.sha256(b"hello").hexdigest()
    assert upload_path("bfsi", name).read_bytes() == b"hello"
    with pytest.raises(FileNotFoundError):
        upload_path("bfsi", "../../etc/passwd")
    with pytest.raises(FileNotFoundError):
        upload_path("bfsi", "missing.pdf")


def test_mail_not_configured_is_best_effort(monkeypatch):
    monkeypatch.setattr(mail.settings, "smtp_host", "")
    assert mail.mail_configured() is False
    assert mail.send_mail_best_effort(["a@b.c"], "s", "b") is False
    with pytest.raises(mail.MailError):
        mail.send_mail(["a@b.c"], "s", "b")


def test_job_success_and_failure(db, monkeypatch):
    monkeypatch.setattr(jobs, "RUN_INLINE", True)
    oid = db.things.insert_one({"x": 1}).inserted_id
    assert jobs.start_job("things", oid, lambda: None) is True
    assert db.things.find_one({"_id": oid})["analysis_status"] == "done"

    def boom():
        raise RuntimeError("OpenAI API key is invalid.")
    assert jobs.start_job("things", oid, boom) is True
    doc = db.things.find_one({"_id": oid})
    assert doc["analysis_status"] == "failed" and doc["analysis_error"] == "OpenAI API key is invalid."


def test_job_refuses_when_running(db, monkeypatch):
    monkeypatch.setattr(jobs, "RUN_INLINE", True)
    oid = db.things.insert_one({"analysis_status": "running"}).inserted_id
    assert jobs.start_job("things", oid, lambda: None) is False


def test_reset_interrupted(db):
    oid = db.esg_submissions.insert_one({"analysis_status": "running"}).inserted_id
    jobs.reset_interrupted_jobs()
    doc = db.esg_submissions.find_one({"_id": oid})
    assert doc["analysis_status"] == "failed"
    assert doc["analysis_error"] == "Interrupted by a server restart — run the analysis again."


def test_client_ip_trust_proxy_false(monkeypatch):
    monkeypatch.setattr(net.settings, "trust_proxy", False)
    scope = {
        "type": "http",
        "headers": [(b"cf-connecting-ip", b"1.2.3.4"), (b"x-forwarded-for", b"5.6.7.8")],
        "client": ("9.9.9.9", 1234),
    }
    request = Request(scope)
    assert net.client_ip(request) == "9.9.9.9"


def test_client_ip_trust_proxy_true_cf_connecting_ip(monkeypatch):
    monkeypatch.setattr(net.settings, "trust_proxy", True)
    monkeypatch.setattr(net.settings, "trust_cloudflare", True)
    scope = {
        "type": "http",
        "headers": [(b"cf-connecting-ip", b"1.2.3.4"), (b"x-forwarded-for", b"5.6.7.8")],
        "client": ("9.9.9.9", 1234),
    }
    request = Request(scope)
    assert net.client_ip(request) == "1.2.3.4"


def test_client_ip_trust_proxy_true_x_forwarded_for(monkeypatch):
    monkeypatch.setattr(net.settings, "trust_proxy", True)
    monkeypatch.setattr(net.settings, "trusted_proxy_hops", 1)
    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"5.6.7.8, 6.6.6.6")],
        "client": ("9.9.9.9", 1234),
    }
    request = Request(scope)
    # The rightmost entry is the one our own proxy appended; the leftmost is client-forgeable.
    assert net.client_ip(request) == "6.6.6.6"


def test_client_ip_trust_proxy_true_x_forwarded_for_with_spaces(monkeypatch):
    monkeypatch.setattr(net.settings, "trust_proxy", True)
    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"  5.6.7.8  ")],
        "client": ("9.9.9.9", 1234),
    }
    request = Request(scope)
    assert net.client_ip(request) == "5.6.7.8"


def test_client_ip_trust_proxy_true_fallback_to_client(monkeypatch):
    monkeypatch.setattr(net.settings, "trust_proxy", True)
    scope = {
        "type": "http",
        "headers": [],
        "client": ("9.9.9.9", 1234),
    }
    request = Request(scope)
    assert net.client_ip(request) == "9.9.9.9"


def test_send_mail_success_with_smtp_ssl(monkeypatch):
    monkeypatch.setattr(mail.settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(mail.settings, "smtp_user", "user@example.com")
    monkeypatch.setattr(mail.settings, "smtp_password", "password123")
    monkeypatch.setattr(mail.settings, "smtp_ssl", True)
    monkeypatch.setattr(mail.settings, "mail_from", "sender@example.com")

    login_args = []
    send_message_calls = []

    class FakeSMTPSSL:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def login(self, user, password):
            login_args.append((user, password))

        def send_message(self, msg):
            send_message_calls.append(msg)

    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", FakeSMTPSSL)

    mail.send_mail(
        ["recipient@example.com"],
        "Test Subject",
        "Test body",
        cc=["cc@example.com"],
        reply_to="reply@example.com",
        attachments=[mail.Attachment("test.pdf", b"%PDF-1", "application/pdf")],
    )

    assert login_args == [("user@example.com", "password123")]
    assert len(send_message_calls) == 1
    msg = send_message_calls[0]
    assert msg["From"] == "sender@example.com"
    assert msg["To"] == "recipient@example.com"
    assert msg["Cc"] == "cc@example.com"
    assert msg["Reply-To"] == "reply@example.com"
    assert msg["Subject"] == "Test Subject"
    # For multipart message, get the first part (text content)
    msg_iter = msg.iter_parts()
    text_part = next(msg_iter)
    assert text_part.get_content() == "Test body\n"
    # Check attachment
    attachments = list(msg.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "test.pdf"
    assert attachments[0].get_content_type() == "application/pdf"
    assert attachments[0].get_payload(decode=True) == b"%PDF-1"


def test_send_mail_success_without_smtp_ssl(monkeypatch):
    monkeypatch.setattr(mail.settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(mail.settings, "smtp_user", "user@example.com")
    monkeypatch.setattr(mail.settings, "smtp_password", "password123")
    monkeypatch.setattr(mail.settings, "smtp_ssl", False)
    monkeypatch.setattr(mail.settings, "mail_from", "sender@example.com")

    login_args = []
    starttls_calls = []
    send_message_calls = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def starttls(self, **kwargs):
            starttls_calls.append(True)

        def login(self, user, password):
            login_args.append((user, password))

        def send_message(self, msg):
            send_message_calls.append(msg)

    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)

    mail.send_mail(
        ["recipient@example.com"],
        "Test Subject",
        "Test body",
    )

    assert starttls_calls == [True]
    assert login_args == [("user@example.com", "password123")]
    assert len(send_message_calls) == 1
    msg = send_message_calls[0]
    assert msg["From"] == "sender@example.com"
    assert msg["To"] == "recipient@example.com"
    assert msg["Subject"] == "Test Subject"
    assert msg.get_content() == "Test body\n"
