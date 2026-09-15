import json
from datetime import datetime, timezone

import pytest
from bson import ObjectId

from app.core import jobs
from app.core.config import settings
from app.core.uploads import save_upload
from app.esg import llm as llm_mod
from app.esg import store as esg_store
from tests.conftest import FakeLLM
from tests.fixtures.make_pdf import make_pdf


VALID_FORM = {
    "name": "Asha Rao",
    "email": "asha@example.com",
    "designation": "CFO",
    "company_name": "Acme Ltd",
    "mobile_number": "9876543210",
    "report_year": "2024-2025",
}


def _pdf_file(data: bytes | None = None):
    return {"file": ("report.pdf", data or make_pdf(["hello world"]), "application/pdf")}


def _seed_submission(db, **overrides):
    stored_name, sha = save_upload("esg", make_pdf(["seed text"]), "pdf")
    doc = {
        "name": "Asha Rao",
        "email": "asha@example.com",
        "designation": "CFO",
        "company_name": "Acme Ltd",
        "mobile_number": "9876543210",
        "report_year": "2024-2025",
        "file_path": stored_name,
        "file_sha256": sha,
        "original_filename": "report.pdf",
        "submit_ip": "1.2.3.4",
        "status": "new",
        "analysis_status": "idle",
        "created_at": datetime.now(timezone.utc),
    }
    doc.update(overrides)
    return db.esg_submissions.insert_one(doc).inserted_id


# --- public submit -------------------------------------------------------------------

def test_public_submit_valid_stores_document(client, db):
    resp = client.post("/api/esg/submissions", data=VALID_FORM, files=_pdf_file())
    assert resp.status_code == 201
    body = resp.json()
    assert body["ok"] is True and body["id"]
    doc = db.esg_submissions.find_one({"_id": ObjectId(body["id"])})
    assert doc["email"] == "asha@example.com"
    assert doc["original_filename"] == "report.pdf"
    assert doc["status"] == "new" and doc["analysis_status"] == "idle"
    assert doc["file_path"]


@pytest.mark.parametrize("field", ["name", "email", "designation", "company_name", "mobile_number", "report_year"])
def test_public_submit_missing_required_field(client, field):
    form = {**VALID_FORM, field: ""}
    resp = client.post("/api/esg/submissions", data=form, files=_pdf_file())
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Please fill in all required fields."


def test_public_submit_invalid_email(client):
    form = {**VALID_FORM, "email": "not-an-email"}
    resp = client.post("/api/esg/submissions", data=form, files=_pdf_file())
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Please enter a valid email"


def test_public_submit_invalid_mobile(client):
    form = {**VALID_FORM, "mobile_number": "12345"}
    resp = client.post("/api/esg/submissions", data=form, files=_pdf_file())
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Please enter a valid phone number"


def test_public_submit_invalid_report_year(client):
    form = {**VALID_FORM, "report_year": "2024"}
    resp = client.post("/api/esg/submissions", data=form, files=_pdf_file())
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Report financial year must look like 2024-2025"


def test_public_submit_missing_file(client):
    resp = client.post("/api/esg/submissions", data=VALID_FORM)
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Please upload your report."


def test_public_submit_file_too_large(client):
    big = b"%PDF" + b"0" * (20 * 1024 * 1024 + 1)
    resp = client.post("/api/esg/submissions", data=VALID_FORM, files={"file": ("report.pdf", big, "application/pdf")})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "The uploaded file is too large."


def test_public_submit_accepts_file_over_old_5mb_limit(client):
    report = b"%PDF" + b"0" * (6 * 1024 * 1024)
    resp = client.post("/api/esg/submissions", data=VALID_FORM, files={"file": ("report.pdf", report, "application/pdf")})
    assert resp.status_code == 201


def test_public_submit_disallowed_extension(client):
    resp = client.post("/api/esg/submissions", data=VALID_FORM, files={"file": ("report.txt", b"hello", "text/plain")})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "You are not allowed to upload files of this type."


def test_public_submit_content_mismatch(client):
    resp = client.post("/api/esg/submissions", data=VALID_FORM, files={"file": ("report.pdf", b"not a pdf", "application/pdf")})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "File content does not match its type."


def test_public_submit_rate_limited_after_five(client):
    for _ in range(5):
        resp = client.post("/api/esg/submissions", data=VALID_FORM, files=_pdf_file())
        assert resp.status_code == 201
    resp = client.post("/api/esg/submissions", data=VALID_FORM, files=_pdf_file())
    assert resp.status_code == 429
    assert resp.json()["detail"] == "Too many submissions — please try again later."


# --- admin: auth ----------------------------------------------------------------------

def test_admin_list_requires_auth(client):
    resp = client.get("/api/admin/esg/submissions")
    assert resp.status_code == 401


# --- admin: submissions CRUD + analysis ------------------------------------------------

def test_admin_list_search_and_pagination(admin_client, db):
    _seed_submission(db, name="Wipro Contact", email="a@wipro.com")
    _seed_submission(db, name="Someone Else", company_name="Infosys")
    resp = admin_client.get("/api/admin/esg/submissions", params={"search": "wipro"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1 and len(body["items"]) == 1
    assert body["items"][0]["name"] == "Wipro Contact"
    assert isinstance(body["items"][0]["_id"], str)


def test_admin_get_submission_invalid_id_404(admin_client):
    resp = admin_client.get("/api/admin/esg/submissions/not-an-object-id")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Not found"


def test_admin_get_submission_missing_404(admin_client):
    resp = admin_client.get(f"/api/admin/esg/submissions/{ObjectId()}")
    assert resp.status_code == 404


def test_admin_create_submission_starts_analysis(admin_client, db, prompts, monkeypatch):
    monkeypatch.setattr(jobs, "RUN_INLINE", True)
    fake = FakeLLM({"Environment": 90, "Social": 60, "Governance": 50})
    monkeypatch.setattr(llm_mod, "get_llm", lambda: fake)

    resp = admin_client.post("/api/admin/esg/submissions", data=VALID_FORM, files=_pdf_file())
    assert resp.status_code == 201
    sub_id = resp.json()["id"]
    doc = db.esg_submissions.find_one({"_id": ObjectId(sub_id)})
    assert doc["status"] == "report_generated"
    assert doc["analysis_status"] == "done"
    assert doc["final"]["composite_score"] == pytest.approx(0.3 * 90 + 0.3 * 60 + 0.4 * 50)


def test_admin_analyze_runs_job_to_done(admin_client, db, prompts, monkeypatch):
    monkeypatch.setattr(jobs, "RUN_INLINE", True)
    fake = FakeLLM({"Environment": 90, "Social": 60, "Governance": 50})
    monkeypatch.setattr(llm_mod, "get_llm", lambda: fake)

    sub_id = _seed_submission(db)
    resp = admin_client.post(f"/api/admin/esg/submissions/{sub_id}/analyze")
    assert resp.status_code == 202
    assert resp.json() == {"started": True}

    doc = db.esg_submissions.find_one({"_id": sub_id})
    assert doc["analysis_status"] == "done"
    assert doc["status"] == "report_generated"
    assert "composite_score" in doc["final"]
    assert doc["company_id"] is not None


def test_admin_analyze_conflict_while_running(admin_client, db):
    sub_id = _seed_submission(db, analysis_status="running")
    resp = admin_client.post(f"/api/admin/esg/submissions/{sub_id}/analyze")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Analysis is already running."


def test_admin_delete_submission_removes_doc_and_file(admin_client, db):
    from app.core.uploads import upload_path

    sub_id = _seed_submission(db)
    doc = db.esg_submissions.find_one({"_id": sub_id})
    stored_path = upload_path("esg", doc["file_path"])
    assert stored_path.is_file()

    resp = admin_client.delete(f"/api/admin/esg/submissions/{sub_id}")
    assert resp.status_code == 200 and resp.json() == {"ok": True}
    assert db.esg_submissions.find_one({"_id": sub_id}) is None
    assert not stored_path.is_file()


def test_admin_download_submission_file(admin_client, db):
    sub_id = _seed_submission(db)
    resp = admin_client.get(f"/api/admin/esg/submissions/{sub_id}/file")
    assert resp.status_code == 200
    assert 'filename="report.pdf"' in resp.headers["content-disposition"]


def test_admin_download_submission_missing_file_is_404(admin_client, db):
    """Mirrors app/bfsi/router_admin.py's download route: a resolved-but-missing file
    returns a clean 404, not a 500."""
    from app.core.uploads import upload_path

    sub_id = _seed_submission(db)
    doc = db.esg_submissions.find_one({"_id": sub_id})
    upload_path("esg", doc["file_path"]).unlink()

    resp = admin_client.get(f"/api/admin/esg/submissions/{sub_id}/file")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Not found"


# --- admin: send report ----------------------------------------------------------------

def test_admin_send_without_smtp_is_503(admin_client, db):
    sub_id = _seed_submission(db, final={"composite_score": 70})
    resp = admin_client.post(
        f"/api/admin/esg/submissions/{sub_id}/send",
        files={"pdf": ("esg_report.pdf", b"%PDF-1.4 fake report", "application/pdf")},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "Mail is not configured"


def test_admin_send_requires_final(admin_client, db, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_user", "user@example.com")
    monkeypatch.setattr(settings, "smtp_password", "pw")

    sub_id = _seed_submission(db)  # no "final"
    resp = admin_client.post(
        f"/api/admin/esg/submissions/{sub_id}/send",
        files={"pdf": ("esg_report.pdf", b"%PDF-1.4 fake report", "application/pdf")},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Run the analysis before sending the report."


def test_admin_send_success_captures_mail_and_marks_sent(admin_client, db, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_user", "user@example.com")
    monkeypatch.setattr(settings, "smtp_password", "pw")

    sub_id = _seed_submission(db, final={"composite_score": 70}, company_id=ObjectId())

    calls = []

    def fake_send_mail(to, subject, body, *, cc=None, reply_to=None, attachments=None):
        calls.append({"to": to, "subject": subject, "body": body, "cc": cc, "attachments": attachments})

    monkeypatch.setattr("app.esg.router_admin.send_mail", fake_send_mail)

    resp = admin_client.post(
        f"/api/admin/esg/submissions/{sub_id}/send",
        files={"pdf": ("esg_report.pdf", b"%PDF-1.4 fake report", "application/pdf")},
    )
    assert resp.status_code == 200 and resp.json() == {"ok": True}
    assert len(calls) == 1
    call = calls[0]
    assert call["to"] == ["asha@example.com"]
    assert call["cc"] == [settings.team_email]
    assert call["attachments"][0].filename == "esg_report.pdf"

    doc = db.esg_submissions.find_one({"_id": sub_id})
    assert doc["status"] == "sent"
    assert doc["sent_at"] is not None


# --- admin: legacy routes ---------------------------------------------------------------

def test_legacy_export_csv_header(admin_client, db):
    company_id = ObjectId()
    db.esg_report.insert_one({
        "company_id": company_id,
        "analysis": [
            {
                "filename": "report.pdf",
                "category": "Environment",
                "text": "some page text",
                "page_no": 1,
                "analysis": json.dumps({
                    "reason": "good disclosure",
                    "score": 80,
                    "positive_keywords": ["renewables", "recycling"],
                    "negative_keywords": ["emissions"],
                }),
            },
            # This entry has no "analysis" key (like the aggregate row appended by the
            # pipeline) and must be silently skipped, exactly as eval(None) would have
            # raised and been caught in app.py.
            {"environmental_score": 80},
        ],
    })

    resp = admin_client.get(f"/api/admin/esg/export_csv/{company_id}")
    assert resp.status_code == 200
    lines = resp.text.splitlines()
    assert lines[0] == "Filename,Category,Text,Page No,Reason,Score,Positive Keywords,Negative Keywords"
    assert lines[1] == "report.pdf,Environment,some page text,1,good disclosure,80,\"renewables, recycling\",emissions"
    assert len(lines) == 2


def test_legacy_export_csv_no_data_404(admin_client, db):
    resp = admin_client.get(f"/api/admin/esg/export_csv/{ObjectId()}")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "No data found for the given company_id."


def test_legacy_list_users(admin_client, db):
    db.user_details.insert_one({"fullname": "Asha", "email": "a@x.com"})
    resp = admin_client.get("/api/admin/esg/users")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["response"][0]["email"] == "a@x.com"
    assert isinstance(body["response"][0]["_id"], str)


def test_legacy_get_and_update_report(admin_client, db):
    report_id = db.esg_hashes.insert_one({
        "company_id": ObjectId(),
        "filename": "r.pdf",
        "hash": "abc",
        "llm_response": {"environmental_score": 10, "social_score": 10, "governance_score": 10,
                          "composite_score": 10, "sector": "x", "industry": "y", "report_date": "2026-01-01",
                          "environmental_top_keywords": [], "social_top_keywords": [], "governance_top_keywords": [],
                          "environmental_score_performance": "D", "social_score_performance": "D",
                          "governance_score_performance": "D", "composite_score_performance": "D",
                          "environmental_score_performance_label": "Below Average",
                          "social_score_performance_label": "Below Average",
                          "governance_score_performance_label": "Below Average",
                          "composite_score_performance_label": "Below Average"},
    }).inserted_id

    resp = admin_client.get(f"/api/admin/esg/reports/{report_id}")
    assert resp.status_code == 200
    assert resp.json()["response"]["llm_response"]["sector"] == "x"

    payload = {
        "environmental_keywords": ["a"], "social_keywords": ["b"], "governance_keywords": ["c"],
        "environmental_score": 90.0, "social_score": 80.0, "governance_score": 70.0, "composite_score": 81.0,
        "sector": "finance", "industry": "banking", "report_date": "2026-02-01",
        "environmental_score_performance": "A+", "social_score_performance": "A",
        "governance_score_performance": "B+", "composite_score_performance": "A",
        "environmental_score_performance_label": "Outstanding", "social_score_performance_label": "Excellent",
        "governance_score_performance_label": "Very Good", "composite_score_performance_label": "Excellent",
    }
    resp = admin_client.post(f"/api/admin/esg/reports/{report_id}", json=payload)
    assert resp.status_code == 200
    assert resp.json() == {"message": f"Report with ID {report_id} updated successfully."}

    updated = db.esg_hashes.find_one({"_id": report_id})
    assert updated["llm_response"]["sector"] == "finance"
    assert updated["llm_response"]["composite_score"] == 81.0


def test_legacy_get_report_malformed_id_is_not_found(admin_client):
    resp = admin_client.get("/api/admin/esg/reports/not-an-object-id")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Not found"


def test_legacy_get_report_valid_id_missing_document(admin_client, db):
    resp = admin_client.get(f"/api/admin/esg/reports/{ObjectId()}")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Report not found"


def test_legacy_update_report_malformed_id_is_not_found(admin_client):
    payload = {
        "environmental_keywords": [], "social_keywords": [], "governance_keywords": [],
        "environmental_score": 1.0, "social_score": 1.0, "governance_score": 1.0, "composite_score": 1.0,
        "sector": "x", "industry": "y", "report_date": "2026-01-01",
        "environmental_score_performance": "A", "social_score_performance": "A",
        "governance_score_performance": "A", "composite_score_performance": "A",
        "environmental_score_performance_label": "Outstanding", "social_score_performance_label": "Outstanding",
        "governance_score_performance_label": "Outstanding", "composite_score_performance_label": "Outstanding",
    }
    resp = admin_client.post("/api/admin/esg/reports/not-an-object-id", json=payload)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Not found"


def test_legacy_update_report_valid_id_missing_document(admin_client, db):
    payload = {
        "environmental_keywords": [], "social_keywords": [], "governance_keywords": [],
        "environmental_score": 1.0, "social_score": 1.0, "governance_score": 1.0, "composite_score": 1.0,
        "sector": "x", "industry": "y", "report_date": "2026-01-01",
        "environmental_score_performance": "A", "social_score_performance": "A",
        "governance_score_performance": "A", "composite_score_performance": "A",
        "environmental_score_performance_label": "Outstanding", "social_score_performance_label": "Outstanding",
        "governance_score_performance_label": "Outstanding", "composite_score_performance_label": "Outstanding",
    }
    resp = admin_client.post(f"/api/admin/esg/reports/{ObjectId()}", json=payload)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "No report found with the provided ID."


# --- admin: run_esg_analysis guards against a failed user insert ----------------------

def test_analyze_fails_when_user_insertion_fails(admin_client, db, prompts, monkeypatch):
    monkeypatch.setattr(jobs, "RUN_INLINE", True)
    monkeypatch.setattr(esg_store, "insert_user", lambda *a, **k: None)

    sub_id = _seed_submission(db)
    resp = admin_client.post(f"/api/admin/esg/submissions/{sub_id}/analyze")
    assert resp.status_code == 202
    assert resp.json() == {"started": True}

    doc = db.esg_submissions.find_one({"_id": sub_id})
    assert doc["analysis_status"] == "failed"
    assert doc["analysis_error"] == "User insertion failed."
