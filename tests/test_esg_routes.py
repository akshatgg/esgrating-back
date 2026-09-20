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


@pytest.mark.parametrize("number", [
    "+14155552671",        # US
    "+44 7911 123456",     # UK, spaces
    "+971501234567",       # UAE
    "+49 (30) 1234-5678",  # Germany, brackets and dash
    "+91 9876543210",      # India with country code
    "9876543210",          # India, bare 10 digits
])
def test_public_submit_accepts_international_mobile(client, number):
    form = {**VALID_FORM, "mobile_number": number}
    resp = client.post("/api/esg/submissions", data=form, files=_pdf_file())
    assert resp.status_code == 201


@pytest.mark.parametrize("number", ["12345", "abcdefghij", "+12345678901234567", "++919876543210"])
def test_public_submit_rejects_bad_mobile(client, number):
    form = {**VALID_FORM, "mobile_number": number}
    resp = client.post("/api/esg/submissions", data=form, files=_pdf_file())
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Please enter a valid phone number"


def test_public_submit_invalid_mobile(client):
    form = {**VALID_FORM, "mobile_number": "12345"}
    resp = client.post("/api/esg/submissions", data=form, files=_pdf_file())
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Please enter a valid phone number"


@pytest.mark.parametrize("bad", ["20245", "2024-", "2024-20255", "FY2024", "2024/2025", "2024 2025"])
def test_public_submit_invalid_report_year(client, bad):
    form = {**VALID_FORM, "report_year": bad}
    resp = client.post("/api/esg/submissions", data=form, files=_pdf_file())
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Report financial year must look like 2024-2025 or 2025"


@pytest.mark.parametrize("year", ["2024-2025", "2025"])
def test_public_submit_accepts_single_year_and_range(client, db, year):
    """A company whose reporting period is one calendar year submits "2025"; the
    financial-year range still works (user, 2026-09-20)."""
    form = {**VALID_FORM, "report_year": year}
    resp = client.post("/api/esg/submissions", data=form, files=_pdf_file())
    assert resp.status_code == 201
    assert db.esg_submissions.find_one({"_id": ObjectId(resp.json()["id"])})["report_year"] == year


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
    assert doc["final"]["composite_score"] == pytest.approx(0.35 * 90 + 0.30 * 60 + 0.35 * 50)


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


def test_admin_analyze_use_cache_false_scores_fresh(admin_client, db, prompts, monkeypatch):
    monkeypatch.setattr(jobs, "RUN_INLINE", True)
    first = FakeLLM({"Environment": 90, "Social": 60, "Governance": 50})
    monkeypatch.setattr(llm_mod, "get_llm", lambda: first)
    sub_id = _seed_submission(db)
    admin_client.post(f"/api/admin/esg/submissions/{sub_id}/analyze")
    n = len(first.calls)
    # Opt-in cache: the same text is served from the cache, with no new calls.
    admin_client.post(f"/api/admin/esg/submissions/{sub_id}/analyze?use_cache=true")
    assert len(first.calls) == n

    fresh = FakeLLM({"Environment": 10, "Social": 10, "Governance": 10})
    monkeypatch.setattr(llm_mod, "get_llm", lambda: fresh)
    resp = admin_client.post(f"/api/admin/esg/submissions/{sub_id}/analyze?use_cache=false")
    assert resp.status_code == 202
    assert any("score this" in c for c in fresh.calls)
    assert db.esg_submissions.find_one({"_id": sub_id})["final"]["environmental_score"] == 10

    # The fresh result supersedes the old cache entry for later cached runs.
    n = len(fresh.calls)
    admin_client.post(f"/api/admin/esg/submissions/{sub_id}/analyze?use_cache=true")
    assert len(fresh.calls) == n
    assert db.esg_submissions.find_one({"_id": sub_id})["final"]["environmental_score"] == 10


def test_admin_analyze_skips_cache_by_default(admin_client, db, prompts, monkeypatch):
    monkeypatch.setattr(jobs, "RUN_INLINE", True)
    fake = FakeLLM({"Environment": 90, "Social": 60, "Governance": 50})
    monkeypatch.setattr(llm_mod, "get_llm", lambda: fake)
    sub_id = _seed_submission(db)
    admin_client.post(f"/api/admin/esg/submissions/{sub_id}/analyze")
    n = len(fake.calls)
    # No use_cache param (e.g. a stale browser tab): scored again, not served from cache.
    admin_client.post(f"/api/admin/esg/submissions/{sub_id}/analyze")
    assert len(fake.calls) > n


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
            {
                "filename": "report.pdf",
                "category": "Social",
                "text": "hr page",
                "page_no": 2,
                "analysis": json.dumps({
                    "reason": "policies",
                    "score": 60,
                    "positive_keywords": ["posh"],
                    "negative_keywords": [],
                }),
                # Set by pipeline.attach_page_kpis; "; " keeps comma-bearing names apart.
                "kpis": ["Diversity, equity, and inclusion efforts.", "Employee welfare and safety.", " "],
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
    assert lines[0] == "Filename,Category,Text,Page No,Reason,Score,KPIs Present,Positive Keywords,Negative Keywords,Review"
    # A page scored before KPI tagging existed exports an empty KPI cell.
    assert lines[1] == "report.pdf,Environment,some page text,1,good disclosure,80,,\"renewables, recycling\",emissions,"
    assert lines[2] == ("report.pdf,Social,hr page,2,policies,60,"
                        "\"Diversity, equity, and inclusion efforts.; Employee welfare and safety.\",posh,,")
    assert len(lines) == 3


def test_export_csv_exports_one_run_not_every_run(admin_client, db):
    cid = ObjectId()

    def run(text, composite):
        return {"company_id": cid, "filename": ["report.pdf"], "composite_score": composite, "analysis": [{
            "filename": "report.pdf", "category": "Environment", "text": text, "page_no": 1,
            "analysis": json.dumps({"reason": "r", "score": 50, "positive_keywords": [], "negative_keywords": []}),
        }]}

    db.esg_report.insert_one(run("old run", 40.0))
    db.esg_report.insert_one(run("new run", 60.0))
    # No submission: the company's newest run only.
    body = admin_client.get(f"/api/admin/esg/export_csv/{cid}").text
    assert "new run" in body and "old run" not in body
    # With a submission: the run behind its report (matched on composite score).
    sub_id = _seed_submission(db, company_id=cid, original_filename="report.pdf", final={"composite_score": 40.0})
    body = admin_client.get(f"/api/admin/esg/export_csv/{cid}?submission_id={sub_id}").text
    assert "old run" in body and "new run" not in body


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


# --- KPI scores: report editor and CSV summary ---------------------------------------------

def _kpi_submission(db, method=True):
    """A report scored per KPI (method) or an older strong/partial one (100 / 50 / 0)."""
    from app.esg import scoring
    scores = ({"Environment": [80, 40], "Social": [60, 0], "Governance": [90, 50]} if method
              else {"Environment": [100, 50], "Social": [100, 0], "Governance": [50, 50]})
    coverage, final = {}, {"sector": "Finance", "industry": "Banking"}
    for cat, values in scores.items():
        detail = scoring.category_detail(
            [(1, {f"{cat[0]}{i + 1}": v for i, v in enumerate(values) if v})],
            [f"{cat[0]}{i + 1}" for i in range(len(values))])
        if not method:
            detail.pop("method")
            for r in detail["kpis"]:
                r.pop("score")
        coverage[cat] = detail
        final[f"{scoring.PREFIX[cat]}_score"] = detail["score"]
    final["kpi_coverage"] = coverage
    if method:
        final["scoring_method"] = "kpi_score"
    weights = scoring.weights_for(final)
    final["composite_score"] = scoring.composite_score(
        final["environmental_score"], final["social_score"], final["governance_score"], weights)
    scoring.grade_all(final)
    return db.esg_submissions.insert_one({"company_name": "Acme", "final": final}).inserted_id


def test_report_kpi_score_edit_recomputes_pillar_overall_and_grade(admin_client, db):
    sid = _kpi_submission(db)
    base = f"/api/admin/esg/submissions/{sid}/report"
    got = admin_client.get(base).json()
    assert got["kpis_editable"] == {"E": True, "S": True, "G": True}
    assert got["kpis"]["E"] == [
        {"kpi": "E1", "score": 80.0, "original_score": 80.0, "pages": [1]},
        {"kpi": "E2", "score": 40.0, "original_score": 40.0, "pages": [1]},
    ]

    body = {"kpi_scores": {"E": {"E2": 100, "E1": 80}}}  # E1 equals the AI score: no override
    prev = admin_client.post(f"{base}/preview", json=body).json()
    f = prev["effective"]["final"]
    assert f["environmental_score"] == 90  # (80 + 100) / 200
    assert f["composite_score"] == pytest.approx(0.35 * 90 + 0.30 * 30 + 0.35 * 70)
    assert f["kpi_coverage"]["Environment"]["kpis"][1] == {
        "kpi": "E2", "score": 100.0, "points": 100.0, "level": "strong", "pages": [1]}
    assert f["environmental_score_performance"] == "A"
    assert prev["kpis"]["E"][1] == {"kpi": "E2", "score": 100.0, "original_score": 40.0, "pages": [1]}

    saved = admin_client.put(f"{base}/edits", json=body).json()
    assert saved["edits"]["kpi_scores"]["E"] == {"E2": 100.0} and saved["edited"] is True
    assert db.esg_submissions.find_one({"_id": sid})["final"]["environmental_score"] == 90

    # A pillar set by hand still wins over KPI scores.
    over = admin_client.post(f"{base}/preview", json={**body, "pillar_overrides": {"E": 50}}).json()
    assert over["effective"]["final"]["environmental_score"] == 50

    assert admin_client.delete(f"{base}/edits").json()["effective"]["final"]["environmental_score"] == 60


def test_report_kpi_score_validation(admin_client, db):
    sid = _kpi_submission(db)
    url = f"/api/admin/esg/submissions/{sid}/report/preview"
    for bad in ({"E": {"E1": 101}}, {"E": {"E1": -1}}, {"E": {"E1": "8"}}, {"E": {"Nope": 5}}, {"X": {"E1": 5}}):
        assert admin_client.post(url, json={"kpi_scores": bad}).status_code == 422


def test_report_kpi_score_on_older_report_keeps_old_weights(admin_client, db):
    sid = _kpi_submission(db, method=False)
    base = f"/api/admin/esg/submissions/{sid}/report"
    got = admin_client.get(base).json()
    assert got["kpis_editable"] == {"E": True, "S": True, "G": True}
    assert [r["score"] for r in got["kpis"]["S"]] == [100.0, 0.0]
    f = admin_client.post(f"{base}/preview", json={"kpi_scores": {"S": {"S2": 70}}}).json()["effective"]["final"]
    assert f["social_score"] == 85
    assert f["composite_score"] == pytest.approx(0.30 * 75 + 0.30 * 85 + 0.40 * 50)


def test_report_kpi_score_locked_without_kpi_assessment(admin_client, db):
    sid = db.esg_submissions.insert_one({"company_name": "Old", "final": {
        "environmental_score": 50, "social_score": 50, "governance_score": 50, "composite_score": 50,
        "sector": "", "industry": "",
    }}).inserted_id
    base = f"/api/admin/esg/submissions/{sid}/report"
    assert admin_client.get(base).json()["kpis_editable"] == {"E": False, "S": False, "G": False}
    assert admin_client.post(f"{base}/preview", json={"kpi_scores": {"E": {"E1": 5}}}).status_code == 422


def test_export_csv_adds_page_scores_and_kpi_summary(admin_client, db):
    from app.esg import scoring
    cid = ObjectId()
    detail = scoring.category_detail([(2, {"E1": 32, "E2": 80})], ["E1", "E2", "E3"])
    final = {"scoring_method": "kpi_score", "kpi_coverage": {"Environment": detail},
             "environmental_score": detail["score"], "composite_score": 37.33, "composite_score_performance": "D"}
    db.esg_report.insert_one({"company_id": cid, "composite_score": 37.33, "analysis": [
        {"filename": "r.pdf", "category": "Environment", "text": "t", "page_no": 2, "page_score": 56.0,
         "kpis": ["E2 (80)", "E1 (32)"],
         "analysis": json.dumps({"reason": "r", "score": 90, "positive_keywords": [], "negative_keywords": []})},
        final,
    ]})
    lines = admin_client.get(f"/api/admin/esg/export_csv/{cid}").text.splitlines()
    assert lines[1] == "r.pdf,Environment,t,2,r,56.0,E2 (80); E1 (32),,,"
    assert lines[3:] == [
        "Category,KPI,Best Score,Found on Pages",
        "Environment,E1,32,2",
        "Environment,E2,80,2",
        "Environment,E3,0,-",
        "Environment total,,37.33,",
        "Overall,35% Environment + 30% Social + 35% Governance,37.33,Grade D",
    ]


def test_pillar_typed_by_hand_is_carried_into_the_kpi_assessment_and_csv(admin_client, db):
    sid = _kpi_submission(db)
    base = f"/api/admin/esg/submissions/{sid}/report"
    body = {"pillar_overrides": {"E": 75}}
    f = admin_client.post(f"{base}/preview", json=body).json()["effective"]["final"]
    env = f["kpi_coverage"]["Environment"]
    # The report uses 75 everywhere; the KPI Assessment keeps its KPI total (60) and says so.
    assert f["environmental_score"] == 75 and env["score"] == 60 and env["analyst_score"] == 75
    assert "analyst_score" not in f["kpi_coverage"]["Social"]

    # A KPI edit plus a typed pillar: KPI total follows the KPIs, the pillar stays typed.
    both = admin_client.post(f"{base}/preview", json={**body, "kpi_scores": {"E": {"E2": 100}}}).json()
    env = both["effective"]["final"]["kpi_coverage"]["Environment"]
    assert env["score"] == 90 and env["analyst_score"] == 75

    admin_client.put(f"{base}/edits", json=body)
    stored = db.esg_submissions.find_one({"_id": sid})["final"]
    assert stored["kpi_coverage"]["Environment"]["analyst_score"] == 75

    # CSV summary rows say the total was set by the analyst.
    from app.esg import scoring
    rows = scoring.summary_rows(stored)
    assert ["Environment total", "", "75", "Set by analyst (KPI total 60)"] in rows
    assert ["Social total", "", "30", ""] in rows

    # Removing the typed score clears the note; reset restores the AI version.
    cleared = admin_client.post(f"{base}/preview", json={"pillar_overrides": {"E": None}}).json()
    assert "analyst_score" not in cleared["effective"]["final"]["kpi_coverage"]["Environment"]
    reset = admin_client.delete(f"{base}/edits").json()["effective"]["final"]
    assert "analyst_score" not in reset["kpi_coverage"]["Environment"] and reset["environmental_score"] == 60
