import hashlib
from bson import ObjectId
import pytest

from app.core import jobs, mail
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
