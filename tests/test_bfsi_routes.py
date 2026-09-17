import io
from datetime import datetime, timezone

import pytest
from bson import ObjectId

from app.bfsi import pipeline, store
from app.bfsi.options import options_payload
from app.core import jobs
from app.core.config import settings
from app.core.uploads import save_upload, upload_path
from tests.fixtures.make_pdf import make_pdf

VALID_FORM = {
    "borrower_name": "Acme Pvt Ltd",
    "cin_gstin": "U12345MH2015PLC123456",  # 21 chars
    "contact_email": "borrower@example.com",
    "industry": "manufacturing",
    "sub_sector": "Textiles",
    "loan_amount": "1000000",
    "loan_purpose": "Working Capital",
    "loan_type": "Term Loan",
    "outstanding_loans": "50000",
}


def _pdf_file(data: bytes | None = None, name: str = "report.pdf"):
    return {"report_file": (name, data or make_pdf(["hello world"]), "application/pdf")}


def _seed_submission(db, **overrides):
    stored_name, sha = save_upload("bfsi", make_pdf(["seed text"]), "pdf")
    doc = {
        "borrower_name": "Acme Pvt Ltd",
        "cin_gstin": "U12345MH2015PLC123456",
        "contact_email": "borrower@example.com",
        "industry": "manufacturing",
        "sub_sector": "Textiles",
        "loan_amount": 1000000.0,
        "loan_purpose": "Working Capital",
        "loan_type": "Term Loan",
        "outstanding_loans": 50000.0,
        "answers": [],
        "file_path": stored_name,
        "file_sha256": sha,
        "submit_ip": "1.2.3.4",
        "status": "new",
        "created_at": datetime.now(timezone.utc),
    }
    doc.update(overrides)
    return db.bfsi_submissions.insert_one(doc).inserted_id


class FakeBfsiClient:
    """Stand-in for OpenAiJson: scores by adjective found in the prompt text."""

    def __init__(self, scores=None):
        self.scores = scores or {"environmental": 70.0, "social": 60.0, "governance": 50.0}
        # KPIs each scoring answer scores 100: all 18 Environment points, 9 of the 18
        # Social points, no Governance points -> category scores 100 / 50 / 0.
        self.kpis = {"environmental": list(range(1, 19)), "social": list(range(1, 10)), "governance": []}
        self.batch_calls = 0
        self.json_calls = 0

    def batch(self, prompts, concurrency=8):
        self.batch_calls += 1
        out = {}
        for k, p in prompts.items():
            out[k] = None
            for adj, score in self.scores.items():
                if f"for {adj} performance" in p:
                    out[k] = {
                        "reason": "why", "score": score,
                        "positive_keywords": ["k1", "k2"], "negative_keywords": ["n1"],
                        "sector": "finance", "industry": "banking",
                        "kpi_scores": [[n, 100] for n in self.kpis.get(adj, [])],
                    }
                    break
        return out

    def json(self, user, system=""):
        self.json_calls += 1
        if "STRICTLY SELECT THE TOP 5 KEYWORDS" in user:
            return {"keywords": ["k1", "k2", "k3", "k4", "k5"]}
        return {
            "top_risks": ["r1"], "top_improvements": ["i1"],
            "climate_risk": "warm", "governance_summary": "ok",
            "key_metrics": {"employees": "10"},
        }


@pytest.fixture
def fake_bfsi(monkeypatch):
    c = FakeBfsiClient()
    monkeypatch.setattr(pipeline, "get_client", lambda: c)
    return c


# --- options ---------------------------------------------------------------------------

def test_options_matches_payload(client):
    resp = client.get("/api/bfsi/options")
    assert resp.status_code == 200
    assert resp.json() == options_payload()


# --- public submit: field validation, in order ------------------------------------------

@pytest.mark.parametrize("overrides,message", [
    ({"borrower_name": ""}, "Borrower name is required."),
    ({"borrower_name": "x" * 256}, "Borrower name is required."),
    ({"cin_gstin": "TOO-SHORT"}, "CIN must be 21 characters or GSTIN 15 characters."),
    ({"contact_email": "not-an-email"}, "A valid contact email is required."),
    ({"industry": "not-a-key"}, "Invalid industry."),
    ({"sub_sector": "Not A Sub Sector"}, "Invalid sub-sector."),
    ({"sub_sector": ""}, "Invalid sub-sector."),
    ({"loan_amount": "0"}, "Loan amount must be positive."),
    ({"loan_amount": "abc"}, "Loan amount must be positive."),
    ({"loan_purpose": "Not A Purpose"}, "Invalid loan purpose."),
    ({"loan_type": "Not A Type"}, "Invalid loan type."),
    ({"outstanding_loans": "-1"}, "Outstanding loans must be 0 or more."),
])
def test_public_submit_field_validation_messages(client, overrides, message):
    form = {**VALID_FORM, **overrides}
    resp = client.post("/api/bfsi/submissions", data=form, files=_pdf_file())
    assert resp.status_code == 422
    assert resp.json()["detail"] == message


def test_contact_email_too_long_message(client):
    form = {**VALID_FORM, "contact_email": "a" * 250 + "@example.com"}
    resp = client.post("/api/bfsi/submissions", data=form, files=_pdf_file())
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Contact email is too long."


def test_field_checks_run_in_order_borrower_before_cin(client):
    # Both borrower_name and cin_gstin are invalid -- the earlier check (borrower)
    # must win, proving the checks run in the documented order.
    form = {**VALID_FORM, "borrower_name": "", "cin_gstin": "bad"}
    resp = client.post("/api/bfsi/submissions", data=form, files=_pdf_file())
    assert resp.json()["detail"] == "Borrower name is required."


def test_public_submit_missing_file(client):
    resp = client.post("/api/bfsi/submissions", data=VALID_FORM)
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Report upload failed."


def test_public_submit_file_too_large(client):
    big = b"%PDF" + b"0" * (20 * 1024 * 1024 + 1)
    resp = client.post("/api/bfsi/submissions", data=VALID_FORM, files=_pdf_file(big))
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Report must be under 20 MB."


def test_public_submit_disallowed_extension(client):
    resp = client.post(
        "/api/bfsi/submissions", data=VALID_FORM,
        files={"report_file": ("report.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Only PDF or DOCX accepted."


def test_public_submit_content_mismatch(client):
    resp = client.post(
        "/api/bfsi/submissions", data=VALID_FORM,
        files={"report_file": ("report.pdf", b"not a pdf", "application/pdf")},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "File content does not match its type."


def test_public_submit_valid_stores_document_and_notifies(client, db, monkeypatch):
    calls = []

    def fake_send(to, subject, body, **kw):
        calls.append({"to": to, "subject": subject, "body": body, **kw})
        return True

    monkeypatch.setattr("app.bfsi.router_public.send_mail_best_effort", fake_send)

    resp = client.post("/api/bfsi/submissions", data=VALID_FORM, files=_pdf_file())
    assert resp.status_code == 201
    body = resp.json()
    assert body["ok"] is True and body["id"]

    doc = db.bfsi_submissions.find_one({"_id": ObjectId(body["id"])})
    assert doc["borrower_name"] == "Acme Pvt Ltd"
    assert doc["cin_gstin"] == "U12345MH2015PLC123456"
    assert doc["status"] == "new"
    assert doc["answers"] == []
    assert doc["file_path"]

    assert len(calls) == 1
    assert calls[0]["to"] == [settings.team_email]
    assert calls[0]["subject"] == "BFSI ESG Submission — Acme Pvt Ltd"
    assert calls[0]["reply_to"] == "borrower@example.com"
    # submit.php:64 attaches the stored filename (uploads/$fname), not the submitter's
    # original "report.pdf" -- must match the stored doc's file_path.
    assert calls[0]["attachments"][0].filename == doc["file_path"]
    assert calls[0]["attachments"][0].filename != "report.pdf"


def test_public_submit_rate_limited_after_five(client):
    for _ in range(5):
        resp = client.post("/api/bfsi/submissions", data=VALID_FORM, files=_pdf_file())
        assert resp.status_code == 201
    resp = client.post("/api/bfsi/submissions", data=VALID_FORM, files=_pdf_file())
    assert resp.status_code == 429
    assert resp.json()["detail"] == "Too many submissions — please try again later."


# --- admin: auth -------------------------------------------------------------------------

def test_admin_list_requires_auth(client):
    resp = client.get("/api/admin/bfsi/submissions")
    assert resp.status_code == 401


# --- admin: submissions list + calculator -------------------------------------------------

def test_admin_list_search_and_pagination(admin_client, db):
    _seed_submission(db, borrower_name="Wipro Finance")
    _seed_submission(db, borrower_name="Someone Else")
    resp = admin_client.get("/api/admin/bfsi/submissions", params={"search": "wipro"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1 and len(body["items"]) == 1
    assert body["items"][0]["borrower_name"] == "Wipro Finance"
    assert isinstance(body["items"][0]["_id"], str)
    assert body["page"] == 1 and body["pages"] == 1


def test_admin_get_submission_missing_404(admin_client):
    resp = admin_client.get(f"/api/admin/bfsi/submissions/{ObjectId()}")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Not found"


def test_admin_get_submission_invalid_id_404(admin_client):
    resp = admin_client.get("/api/admin/bfsi/submissions/not-an-object-id")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Not found"


def test_admin_calculator_creates_no_mail_no_rate_limit(admin_client, db, monkeypatch):
    """admin/calculator.php: same fields as the public form, no CSRF, no rate limit,
    no mail. The route under test (POST /api/admin/bfsi/submissions) is
    router_admin.create_submission, which never references send_mail_best_effort --
    that symbol lives (and is only ever called) in router_public. The only mail
    function router_admin.py imports at all is `send_mail` (used by the separate
    /submissions/{id}/send route), so that's what we patch to boom here: it's the
    one mail symbol actually reachable from this module, and create_submission must
    never call it."""
    monkeypatch.setattr(jobs, "RUN_INLINE", True)

    def boom(*a, **kw):
        raise AssertionError("admin calculator must not send mail")

    monkeypatch.setattr("app.bfsi.router_admin.send_mail", boom)

    # More than 5 submissions must all succeed -- no rate limit on the admin path.
    for _ in range(6):
        resp = admin_client.post("/api/admin/bfsi/submissions", data=VALID_FORM, files=_pdf_file())
        assert resp.status_code == 201
        assert "id" in resp.json()


# --- admin: records (Add New) --------------------------------------------------------------

def test_admin_create_record_valid(admin_client, db):
    payload = {
        "borrower_name": "Manual Co",
        "cin_gstin": "u12345mh2015plc123456",
        "industry": "manufacturing",
        "sub_sector": "Anything Free Text",
        "loan_amount": 500000,
        "loan_purpose": "Working Capital",
        "loan_type": "Term Loan",
        "status": "new",
    }
    resp = admin_client.post("/api/admin/bfsi/records", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["inserted"] is True and body["id"]
    doc = db.bfsi_submissions.find_one({"_id": ObjectId(body["id"])})
    assert doc["cin_gstin"] == "U12345MH2015PLC123456"
    assert doc["file_path"] == "" and doc["file_sha256"] == "" and doc["submit_ip"] == ""
    assert doc["answers"] == []


def test_admin_create_record_trims_name_email_sub_sector(admin_client, db):
    """dashboard/index.php's create_bfsi trims borrower_name, contact_email and
    sub_sector before storing."""
    payload = {
        "borrower_name": "  Manual Co  ",
        "contact_email": "  someone@example.com  ",
        "industry": "manufacturing",
        "sub_sector": "  Anything Free Text  ",
        "loan_purpose": "Working Capital",
        "loan_type": "Term Loan",
    }
    resp = admin_client.post("/api/admin/bfsi/records", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["inserted"] is True
    doc = db.bfsi_submissions.find_one({"_id": ObjectId(body["id"])})
    assert doc["borrower_name"] == "Manual Co"
    assert doc["contact_email"] == "someone@example.com"
    assert doc["sub_sector"] == "Anything Free Text"


@pytest.mark.parametrize("status", [None, "", "bogus", "deleted"])
def test_admin_create_record_invalid_status_defaults_to_new(admin_client, db, status):
    """dashboard/index.php's create_bfsi whitelists status to
    {new, report_generated, sent}, defaulting to "new" for anything else, including
    a missing field."""
    payload = {
        "borrower_name": "Manual Co",
        "industry": "manufacturing",
        "sub_sector": "Anything Free Text",
        "loan_purpose": "Working Capital",
        "loan_type": "Term Loan",
    }
    if status is not None:
        payload["status"] = status
    resp = admin_client.post("/api/admin/bfsi/records", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["inserted"] is True
    doc = db.bfsi_submissions.find_one({"_id": ObjectId(body["id"])})
    assert doc["status"] == "new"


@pytest.mark.parametrize("status", ["new", "report_generated", "sent"])
def test_admin_create_record_valid_status_stored_as_is(admin_client, db, status):
    payload = {
        "borrower_name": "Manual Co",
        "industry": "manufacturing",
        "sub_sector": "Anything Free Text",
        "loan_purpose": "Working Capital",
        "loan_type": "Term Loan",
        "status": status,
    }
    resp = admin_client.post("/api/admin/bfsi/records", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["inserted"] is True
    doc = db.bfsi_submissions.find_one({"_id": ObjectId(body["id"])})
    assert doc["status"] == status


@pytest.mark.parametrize("bad_field,bad_value", [
    ("industry", "not-a-key"),
    ("loan_purpose", "Not A Purpose"),
    ("loan_type", "Not A Type"),
])
def test_admin_create_record_invalid_silently_skipped(admin_client, db, bad_field, bad_value):
    payload = {
        "borrower_name": "Manual Co", "cin_gstin": "x", "industry": "manufacturing",
        "sub_sector": "x", "loan_amount": 1, "loan_purpose": "Working Capital",
        "loan_type": "Term Loan", "status": "new",
    }
    payload[bad_field] = bad_value
    resp = admin_client.post("/api/admin/bfsi/records", json=payload)
    assert resp.status_code == 200
    assert resp.json() == {"inserted": False}
    assert db.bfsi_submissions.count_documents({}) == 0


# --- admin: CSV import -----------------------------------------------------------------

IMPORT_HEADER = "borrower_name,cin_gstin,contact_email,industry,sub_sector,loan_amount,loan_purpose,loan_type,outstanding_loans,overall_score,grade,status"


def test_admin_import_bad_header(admin_client):
    csv_text = "wrong,header\nfoo,bar\n"
    resp = admin_client.post(
        "/api/admin/bfsi/import",
        files={"file": ("bad.csv", csv_text.encode(), "text/csv")},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == (
        "Error: CSV header row does not match the expected format. Expected: " + IMPORT_HEADER
    )


def test_admin_import_non_csv_extension_rejected(admin_client):
    resp = admin_client.post(
        "/api/admin/bfsi/import",
        files={"file": ("data.txt", b"whatever", "text/plain")},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Invalid file type. Please upload a .csv file."


def test_admin_import_good_file_two_inserted_one_skipped(admin_client, db):
    rows = [
        IMPORT_HEADER,
        "ACME Pvt Ltd,27AAAAA0000A1Z5,contact@acme.com,it,SaaS,1000000,Working Capital,Working Capital,0,85,B,new",
        # Bad row: industry is not a known key -> skipped.
        "Beta Co,BADCIN12345678901234,b@example.com,not-a-key,SaaS,100000,Working Capital,Working Capital,0,,,new",
        "Gamma Co,,,manufacturing,Textiles,200000,Working Capital,Term Loan,0,,,",
    ]
    csv_text = "\n".join(rows) + "\n"

    resp = admin_client.post(
        "/api/admin/bfsi/import",
        files={"file": ("good.csv", csv_text.encode(), "text/csv")},
    )
    assert resp.status_code == 200
    assert resp.json()["message"] == "Import complete: 2 row(s) inserted, 1 row(s) skipped."
    assert db.bfsi_submissions.count_documents({"imported": True}) == 2


def test_admin_import_numeric_coercion_matches_php_float_cast(admin_client, db):
    """admin/import.php casts loan_amount via PHP's (float) unconditionally -- blank or
    garbage becomes 0.0, never null. overall_score is null only for a truly blank cell;
    a non-blank but unparseable cell still becomes 0.0 via the same forgiving cast."""
    rows = [
        IMPORT_HEADER,
        # blank loan_amount -> 0.0; blank overall_score -> None
        "Blank Co,,,manufacturing,Textiles,,Working Capital,Term Loan,0,,,new",
        # garbage loan_amount -> 0.0; garbage overall_score -> 0.0
        "Garbage Co,,,manufacturing,Textiles,abc,Working Capital,Term Loan,0,xyz,,new",
    ]
    csv_text = "\n".join(rows) + "\n"

    resp = admin_client.post(
        "/api/admin/bfsi/import",
        files={"file": ("numeric.csv", csv_text.encode(), "text/csv")},
    )
    assert resp.status_code == 200
    assert resp.json()["message"] == "Import complete: 2 row(s) inserted, 0 row(s) skipped."

    blank = db.bfsi_submissions.find_one({"borrower_name": "Blank Co"})
    assert blank["loan_amount"] == 0.0
    assert blank["overall_score"] is None

    garbage = db.bfsi_submissions.find_one({"borrower_name": "Garbage Co"})
    assert garbage["loan_amount"] == 0.0
    assert garbage["overall_score"] == 0.0


# --- admin: analyze ---------------------------------------------------------------------

def test_admin_analyze_sets_grade_and_overall_and_caches(admin_client, db, fake_bfsi, monkeypatch):
    monkeypatch.setattr(jobs, "RUN_INLINE", True)
    sub_id = _seed_submission(db)

    resp = admin_client.post(f"/api/admin/bfsi/submissions/{sub_id}/analyze")
    assert resp.status_code == 202
    assert resp.json() == {"started": True}

    doc = db.bfsi_submissions.find_one({"_id": sub_id})
    assert doc["status"] == "report_generated"
    assert doc["grade"] in ("A+", "A", "B+", "B", "C", "D")
    assert doc["overall_score"] is not None
    # KPI coverage, not the unit-score average (see FakeBfsiClient.kpis).
    assert doc["e_score"] == 100.0 and doc["s_score"] == 50.0 and doc["g_score"] == 0.0
    assert db.bfsi_report.count_documents({"submission_id": sub_id}) == 1
    assert db.bfsi_hashes.count_documents({"submission_id": sub_id}) == 1
    assert fake_bfsi.batch_calls > 0

    calls_before = fake_bfsi.batch_calls
    json_calls_before = fake_bfsi.json_calls

    resp2 = admin_client.post(f"/api/admin/bfsi/submissions/{sub_id}/analyze?use_cache=true")
    assert resp2.status_code == 202
    # Second run opts into the text-hash cache -- no further LLM calls.
    assert fake_bfsi.batch_calls == calls_before
    assert fake_bfsi.json_calls == json_calls_before
    assert db.bfsi_report.count_documents({"submission_id": sub_id}) == 2
    assert db.bfsi_hashes.count_documents({"submission_id": sub_id}) == 1


def test_admin_analyze_use_cache_false_scores_fresh(admin_client, db, fake_bfsi, monkeypatch):
    monkeypatch.setattr(jobs, "RUN_INLINE", True)
    sub_id = _seed_submission(db)
    admin_client.post(f"/api/admin/bfsi/submissions/{sub_id}/analyze")
    calls = fake_bfsi.batch_calls

    resp = admin_client.post(f"/api/admin/bfsi/submissions/{sub_id}/analyze?use_cache=false")
    assert resp.status_code == 202
    # Scored again rather than served from bfsi_hashes, and the fresh result is cached too.
    assert fake_bfsi.batch_calls > calls
    assert db.bfsi_hashes.count_documents({"submission_id": sub_id}) == 2


def test_admin_export_page_scores_csv(admin_client, db, fake_bfsi, monkeypatch):
    import csv
    monkeypatch.setattr(jobs, "RUN_INLINE", True)
    sub_id = _seed_submission(db)
    url = f"/api/admin/bfsi/submissions/{sub_id}/export_csv"
    assert admin_client.get(url).status_code == 404  # nothing analysed yet

    admin_client.post(f"/api/admin/bfsi/submissions/{sub_id}/analyze")
    resp = admin_client.get(url)
    assert resp.status_code == 200
    lines = resp.text.splitlines()
    assert lines[0] == "Filename,Category,Text,Page No,Reason,Score,KPIs Present,Positive Keywords,Negative Keywords"
    blank = lines.index("")
    rows = list(csv.reader(lines[1:blank]))
    assert rows and [r[1] for r in rows] == sorted(
        (r[1] for r in rows), key=["Environment", "Social", "Governance"].index)
    assert all(r[4] == "why" and r[7] == "k1, k2" and r[8] == "n1" for r in rows)
    # Page score = average of the page's KPI scores (100), not the AI's own number.
    assert {r[1]: r[5] for r in rows} == {"Environment": "100.0", "Social": "100.0", "Governance": "0.0"}
    summary = list(csv.reader(lines[blank + 1:]))
    assert summary[0] == ["Category", "KPI", "Best Score", "Found on Pages"]
    assert ["Environment total", "", "100", ""] in summary and ["Social total", "", "50", ""] in summary
    # Term Loan -> Working Capital weights 20/30/50: 20 + 15 + 0 = 35 -> D
    assert summary[-1] == ["Overall", "Working Capital: 20% Environment + 30% Social + 50% Governance",
                           "35.00", "Grade D"]

    # A cache hit reuses the cached page rows, so the export is unchanged.
    admin_client.post(f"/api/admin/bfsi/submissions/{sub_id}/analyze?use_cache=true")
    assert admin_client.get(url).text == resp.text


def test_admin_analyze_conflict_while_running(admin_client, db):
    sub_id = _seed_submission(db, analysis_status="running")
    resp = admin_client.post(f"/api/admin/bfsi/submissions/{sub_id}/analyze")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Analysis is already running."


def test_admin_get_submission_detail_overall_and_previous(admin_client, db):
    # Older, already-scored submission for the same CIN.
    older_id = _seed_submission(
        db, ai_analysis={"e_score": 1}, overall_score=55.5,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    newer_id = _seed_submission(
        db, e_score=80.0, s_score=70.0, g_score=60.0,
        created_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
    )
    # Ensure newer_id sorts after older_id (mongomock ObjectIds are already monotonic
    # by insertion order, but assert to catch flakiness).
    assert str(newer_id) > str(older_id)

    resp = admin_client.get(f"/api/admin/bfsi/submissions/{newer_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["overall"]["overall"] is not None
    assert body["recommendation"]
    assert body["industry_label"] == "Manufacturing"
    assert body["previous"]["overall"] == 55.5


def test_admin_get_submission_detail_no_scores_yet(admin_client, db):
    sub_id = _seed_submission(db)
    resp = admin_client.get(f"/api/admin/bfsi/submissions/{sub_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["overall"] is None
    assert body["recommendation"] is None
    assert body["previous"] is None


def test_admin_download_submission_file(admin_client, db):
    sub_id = _seed_submission(db)
    resp = admin_client.get(f"/api/admin/bfsi/submissions/{sub_id}/file")
    assert resp.status_code == 200
    doc = db.bfsi_submissions.find_one({"_id": sub_id})
    assert f'filename="{doc["file_path"]}"' in resp.headers["content-disposition"]


def test_admin_download_submission_missing_file_is_404(admin_client, db):
    """download.php: a resolved-but-missing (or traversal-triggering) file returns a
    clean 404, not a 500."""
    sub_id = _seed_submission(db)
    doc = db.bfsi_submissions.find_one({"_id": sub_id})
    upload_path("bfsi", doc["file_path"]).unlink()

    resp = admin_client.get(f"/api/admin/bfsi/submissions/{sub_id}/file")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Not found"


# --- admin: delete cascade ---------------------------------------------------------------

def test_admin_delete_cascade_removes_report_hashes_and_file(admin_client, db):
    sub_id = _seed_submission(db)
    doc = db.bfsi_submissions.find_one({"_id": sub_id})
    stored_path = upload_path("bfsi", doc["file_path"])
    assert stored_path.is_file()

    db.bfsi_report.insert_one({"submission_id": sub_id, "filename": "x", "analysis": {}, "overall_score": 1})
    db.bfsi_hashes.insert_one({"submission_id": sub_id, "filename": "x", "hash": "h", "llm_response": {}})
    db.bfsi_ocr_cache.insert_one({"file_sha256": doc["file_sha256"], "filename": "x", "pages": []})

    resp = admin_client.delete(f"/api/admin/bfsi/submissions/{sub_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] is True and body["report"] == 1 and body["hashes"] == 1
    assert body["ocr"] == 1 and body["file"] is True

    assert db.bfsi_submissions.find_one({"_id": sub_id}) is None
    assert db.bfsi_report.count_documents({"submission_id": sub_id}) == 0
    assert db.bfsi_hashes.count_documents({"submission_id": sub_id}) == 0
    assert db.bfsi_ocr_cache.count_documents({"file_sha256": doc["file_sha256"]}) == 0
    assert not stored_path.is_file()


def test_admin_delete_keeps_ocr_cache_when_another_submission_shares_hash(admin_client, db):
    shared_sha = "shared-hash-value"
    id1 = _seed_submission(db, file_sha256=shared_sha)
    id2 = _seed_submission(db, file_sha256=shared_sha)
    db.bfsi_ocr_cache.insert_one({"file_sha256": shared_sha, "filename": "x", "pages": []})

    resp = admin_client.delete(f"/api/admin/bfsi/submissions/{id1}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] is True
    # id2 still shares the hash, so the OCR cache row survives.
    assert body["ocr"] == 0
    assert db.bfsi_ocr_cache.count_documents({"file_sha256": shared_sha}) == 1

    resp2 = admin_client.delete(f"/api/admin/bfsi/submissions/{id2}")
    body2 = resp2.json()
    assert body2["ocr"] == 1
    assert db.bfsi_ocr_cache.count_documents({"file_sha256": shared_sha}) == 0


def test_admin_delete_missing_returns_not_found(admin_client, db):
    resp = admin_client.delete(f"/api/admin/bfsi/submissions/{ObjectId()}")
    assert resp.status_code == 404


# --- admin: send report -------------------------------------------------------------------

def _pdf_bytes(tag: bytes = b"fake") -> bytes:
    return b"%PDF-1.4 " + tag


def test_admin_send_requires_analysis(admin_client, db):
    sub_id = _seed_submission(db)  # no ai_analysis
    resp = admin_client.post(
        f"/api/admin/bfsi/submissions/{sub_id}/send",
        files=[("pdfs", ("a.pdf", _pdf_bytes(), "application/pdf"))],
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Run the analysis before sending the report."


def test_admin_send_requires_contact_email(admin_client, db):
    sub_id = _seed_submission(db, ai_analysis={"e_score": 1}, contact_email="")
    resp = admin_client.post(
        f"/api/admin/bfsi/submissions/{sub_id}/send",
        files=[("pdfs", ("a.pdf", _pdf_bytes(), "application/pdf"))],
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "This submission has no contact email."


def test_admin_send_without_smtp_is_503(admin_client, db):
    sub_id = _seed_submission(db, ai_analysis={"e_score": 1})
    resp = admin_client.post(
        f"/api/admin/bfsi/submissions/{sub_id}/send",
        files=[("pdfs", ("a.pdf", _pdf_bytes(), "application/pdf"))],
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "Mail is not configured"


def test_admin_send_success_marks_sent_and_sends_both_attachments(admin_client, db, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_user", "user@example.com")
    monkeypatch.setattr(settings, "smtp_password", "pw")

    sub_id = _seed_submission(db, ai_analysis={"e_score": 1})

    calls = []

    def fake_send_mail(to, subject, body, *, cc=None, reply_to=None, attachments=None):
        calls.append({"to": to, "subject": subject, "body": body, "cc": cc, "attachments": attachments})

    monkeypatch.setattr("app.bfsi.router_admin.send_mail", fake_send_mail)

    resp = admin_client.post(
        f"/api/admin/bfsi/submissions/{sub_id}/send",
        files=[
            ("pdfs", ("detailed.pdf", _pdf_bytes(b"detailed"), "application/pdf")),
            ("pdfs", ("onepager.pdf", _pdf_bytes(b"onepager"), "application/pdf")),
        ],
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    assert len(calls) == 1
    call = calls[0]
    assert call["to"] == ["borrower@example.com"]
    assert call["cc"] == [settings.team_email]
    assert call["subject"] == "Your BFSI ESG Credit Risk Report — Acme Pvt Ltd"
    assert [a.filename for a in call["attachments"]] == [
        f"bfsi-detailed-report-{sub_id}.pdf", f"esg-rating-report-{sub_id}.pdf",
    ]

    doc = db.bfsi_submissions.find_one({"_id": sub_id})
    assert doc["status"] == "sent"
    assert doc["sent_at"] is not None


def test_admin_send_rejects_non_pdf_content(admin_client, db, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_user", "user@example.com")
    monkeypatch.setattr(settings, "smtp_password", "pw")

    sub_id = _seed_submission(db, ai_analysis={"e_score": 1})
    resp = admin_client.post(
        f"/api/admin/bfsi/submissions/{sub_id}/send",
        files=[("pdfs", ("a.pdf", b"not a pdf", "application/pdf"))],
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "File content does not match its type."


# --- report editor: KPI scores ----------------------------------------------------------

def _kpi_scored_bfsi(db, method=True):
    from app.esg.scoring import category_detail
    coverage, scores = {}, {}
    for cat, key, values in (("Environment", "e_score", [80, 40]), ("Social", "s_score", [60, 0]),
                             ("Governance", "g_score", [90, 50])):
        detail = category_detail([(1, {f"{cat[0]}{i + 1}": v for i, v in enumerate(values) if v})],
                                 [f"{cat[0]}{i + 1}" for i in range(len(values))])
        coverage[cat], scores[key] = detail, detail["score"]
    ai = {**scores, "kpi_coverage": coverage, "reasons": {"E": [], "S": [], "G": []}}
    if method:
        ai["scoring_method"] = "kpi_score"
    return _seed_submission(db, ai_analysis=ai, **scores, loan_type="Agriculture Loan")


def test_bfsi_report_kpi_score_edit_recomputes_overall_grade_and_recommendation(admin_client, db):
    sid = _kpi_scored_bfsi(db)
    base = f"/api/admin/bfsi/submissions/{sid}/report"
    got = admin_client.get(base).json()
    assert got["kpis_editable"] == {"E": True, "S": True, "G": True}
    assert [r["score"] for r in got["kpis"]["E"]] == [80.0, 40.0]
    # Agriculture 50/25/25: 0.5*60 + 0.25*30 + 0.25*70 = 55 -> C
    assert got["effective"]["overall"]["overall"] == 55.0

    body = {"kpi_scores": {"E": {"E2": 100}}}
    eff = admin_client.post(f"{base}/preview", json=body).json()["effective"]
    assert eff["e_score"] == 90  # (80 + 100) / 200
    assert eff["overall"]["overall"] == 70.0 and eff["overall"]["grade"] == "B"  # 45 + 7.5 + 17.5
    assert eff["recommendation"] == "Moderate — lend with standard ESG conditions"
    assert eff["ai_analysis"]["kpi_coverage"]["Environment"]["kpis"][1]["score"] == 100.0

    admin_client.put(f"{base}/edits", json=body)
    doc = db.bfsi_submissions.find_one({"_id": sid})
    assert doc["e_score"] == 90 and doc["grade"] == "B"
    assert admin_client.delete(f"{base}/edits").json()["effective"]["e_score"] == 60


def test_bfsi_report_kpi_score_rejects_out_of_range(admin_client, db):
    sid = _kpi_scored_bfsi(db)
    url = f"/api/admin/bfsi/submissions/{sid}/report/preview"
    assert admin_client.post(url, json={"kpi_scores": {"E": {"E1": 101}}}).status_code == 422


def test_bfsi_pillar_typed_by_hand_is_carried_into_the_kpi_assessment(admin_client, db):
    sid = _kpi_scored_bfsi(db)
    base = f"/api/admin/bfsi/submissions/{sid}/report"
    eff = admin_client.post(f"{base}/preview", json={"pillar_overrides": {"G": 40}}).json()["effective"]
    gov = eff["ai_analysis"]["kpi_coverage"]["Governance"]
    assert eff["g_score"] == 40 and gov["score"] == 70 and gov["analyst_score"] == 40
    assert "analyst_score" not in eff["ai_analysis"]["kpi_coverage"]["Environment"]
