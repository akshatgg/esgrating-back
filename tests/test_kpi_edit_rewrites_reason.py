"""An edited KPI score gets its reason rewritten by the AI to fit the new score, from the
page the KPI was found on. Nothing else is touched: the page reference stays, the written
summary stays, and the Summary report shows the reason and the page together
(user, 2026-10-09).

Yesterday's rule (tests/test_kpi_edit_keeps_text.py) still holds for everything but the
edited KPI's own reason."""
import io
from datetime import datetime, timezone

import docx
import pytest
from bson import ObjectId

from app.bfsi import store as bfsi_store
from app.core.config import settings
from app.esg import scoring
from app.reports import rewrite, summary
from tests.test_bfsi_routes import _seed_submission as _seed_bfsi
from tests.test_rating_summary import TEXT, _text_of

PAGE_TEXT = "Scope 1 emissions fell 12% against the 2022 baseline, towards a 30% target by 2030."
KPI = "Emissions reduction policy"
OLD = "A reduction policy is named but no measured result is given"


def _esg(db):
    cid = ObjectId()
    detail = scoring.category_detail([(7, {KPI: 40}, {KPI: OLD})], [KPI, "Waste policy"])
    db.esg_report.insert_one({"company_id": cid, "filename": ["report.pdf"], "composite_score": 40.0, "analysis": [
        {"page_no": 7, "category": "Environment", "text": PAGE_TEXT, "analysis": "{}"},
        {"page_no": 8, "category": "Environment", "text": "Unrelated page.", "analysis": "{}"}]})
    final = {"scoring_method": "kpi_score", "kpi_coverage": {"Environment": detail}, "sector": "Banks",
             "environmental_score": detail["score"], "social_score": 0.0, "governance_score": 0.0,
             "composite_score": 40.0}
    scoring.grade_all(final)
    return db.esg_submissions.insert_one({
        "company_name": "Acme Ltd", "email": "a@x.com", "name": "A", "report_year": "2025-2026",
        "company_id": cid, "original_filename": "report.pdf",
        "analyzed_at": datetime(2026, 10, 1, tzinfo=timezone.utc), "final": final}).inserted_id


@pytest.fixture
def ai(monkeypatch):
    """A fake writer: records every call, answers with a reason that quotes the new score."""
    calls = []
    monkeypatch.setattr(settings, "esg_openai_api_key", "test-key")
    monkeypatch.setattr(settings, "bfsi_openai_api_key", "test-key")

    def ask(kind, system, user):
        if system != rewrite.SYSTEM:
            return dict(TEXT)  # the Word summary's own narrative passes
        calls.append((kind, system, user))
        return {"reason": f"Rewritten for the new score ({len(calls)})"}

    monkeypatch.setattr(summary, "_ask_ai", ask)
    return calls


def _url(sid, kind="esg"):
    return f"/api/admin/{kind}/submissions/{sid}/report"


def _row(db, sid):
    final = db.esg_submissions.find_one({"_id": sid})["final"]
    return next(r for r in final["kpi_coverage"]["Environment"]["kpis"] if r["kpi"] == KPI)


def test_raising_a_kpi_rewrites_its_reason_from_the_page_it_was_found_on(admin_client, db, ai):
    sid = _esg(db)
    assert admin_client.put(_url(sid) + "/edits", json={"kpi_scores": {"E": {KPI: 80}}}).status_code == 200

    assert len(ai) == 1
    kind, system, user = ai[0]
    assert kind == "esg"
    assert PAGE_TEXT in user and "Unrelated page" not in user
    assert KPI in user and OLD in user and "80" in user
    row = _row(db, sid)
    assert row["score"] == 80
    assert row["evidence"]["reason"] == "Rewritten for the new score (1)"
    assert row["evidence"]["page"] == 7  # the page reference is kept


def test_the_rewritten_reason_reaches_the_report_page_and_the_word_summary(admin_client, db, ai):
    sid = _esg(db)
    admin_client.put(_url(sid) + "/edits", json={"kpi_scores": {"E": {KPI: 80}}})

    body = admin_client.get(_url(sid)).json()
    row = next(r for r in body["effective"]["final"]["kpi_coverage"]["Environment"]["kpis"] if r["kpi"] == KPI)
    assert row["evidence"]["reason"] == "Rewritten for the new score (1)"
    # The Summary report's evidence column carries the reason AND the page.
    driver = next(r for r in body["summary"]["kpi_rows"] if r["key"] == KPI)["driver"]
    assert driver == "Rewritten for the new score (1) (p.7)"
    text = _text_of(admin_client.get(f"/api/admin/esg/submissions/{sid}/summary").content)
    assert "Rewritten for the new score (1) (p.7)" in text


def test_saving_the_same_score_again_does_not_ask_the_ai_again(admin_client, db, ai):
    sid = _esg(db)
    admin_client.put(_url(sid) + "/edits", json={"kpi_scores": {"E": {KPI: 80}}})
    admin_client.put(_url(sid) + "/edits", json={"kpi_scores": {"E": {KPI: 80}}, "headings": {}})
    assert len(ai) == 1
    assert _row(db, sid)["evidence"]["reason"] == "Rewritten for the new score (1)"


def test_changing_the_score_again_rewrites_again(admin_client, db, ai):
    sid = _esg(db)
    admin_client.put(_url(sid) + "/edits", json={"kpi_scores": {"E": {KPI: 80}}})
    admin_client.put(_url(sid) + "/edits", json={"kpi_scores": {"E": {KPI: 100}}})
    assert len(ai) == 2 and "100" in ai[1][2]
    assert _row(db, sid)["evidence"]["reason"] == "Rewritten for the new score (2)"


def test_a_preview_never_asks_the_ai(admin_client, db, ai):
    sid = _esg(db)
    body = admin_client.post(_url(sid) + "/preview", json={"kpi_scores": {"E": {KPI: 80}}}).json()
    assert not ai
    row = next(r for r in body["effective"]["final"]["kpi_coverage"]["Environment"]["kpis"] if r["kpi"] == KPI)
    assert row["score"] == 80 and row["evidence"]["reason"] == OLD


def test_an_analysts_own_reason_wins_and_is_not_rewritten(admin_client, db, ai):
    sid = _esg(db)
    own = "The analyst's own words."
    admin_client.put(_url(sid) + "/edits", json={"kpi_scores": {"E": {KPI: 80}},
                                                 "fields": {"kpi_reasons": {"E": {KPI: own}}}})
    assert not ai
    assert _row(db, sid)["evidence"]["reason"] == own


def test_a_kpi_the_report_never_mentioned_keeps_not_addressed(admin_client, db, ai):
    sid = _esg(db)
    admin_client.put(_url(sid) + "/edits", json={"kpi_scores": {"E": {"Waste policy": 80}}})
    assert not ai
    body = admin_client.get(_url(sid)).json()
    row = next(r for r in body["effective"]["final"]["kpi_coverage"]["Environment"]["kpis"] if r["kpi"] == "Waste policy")
    assert row["score"] == 80 and not row.get("evidence")
    assert next(r for r in body["summary"]["kpi_rows"] if r["key"] == "Waste policy")["driver"] == \
        "Not addressed in the report"


def test_a_failed_rewrite_keeps_the_old_reason_and_still_saves(admin_client, db, ai, monkeypatch):
    sid = _esg(db)
    monkeypatch.setattr(summary, "_ask_ai", lambda *a: (_ for _ in ()).throw(RuntimeError("down")))
    assert admin_client.put(_url(sid) + "/edits", json={"kpi_scores": {"E": {KPI: 80}}}).status_code == 200
    row = _row(db, sid)
    assert row["score"] == 80 and row["evidence"]["reason"] == OLD


def test_an_empty_answer_keeps_the_old_reason(admin_client, db, ai, monkeypatch):
    sid = _esg(db)
    monkeypatch.setattr(summary, "_ask_ai", lambda *a: {"reason": "   "})
    admin_client.put(_url(sid) + "/edits", json={"kpi_scores": {"E": {KPI: 80}}})
    assert _row(db, sid)["evidence"]["reason"] == OLD


def test_clearing_the_edit_restores_the_original_reason(admin_client, db, ai):
    sid = _esg(db)
    admin_client.put(_url(sid) + "/edits", json={"kpi_scores": {"E": {KPI: 80}}})
    admin_client.put(_url(sid) + "/edits", json={"kpi_scores": {}})
    row = _row(db, sid)
    assert row["score"] == 40 and row["evidence"]["reason"] == OLD
    assert "ai_reasons" not in (db.esg_submissions.find_one({"_id": sid}).get("report_edits") or {})


def test_without_an_ai_key_the_score_still_saves_with_the_old_reason(admin_client, db, ai, monkeypatch):
    monkeypatch.setattr(settings, "esg_openai_api_key", "")
    sid = _esg(db)
    assert admin_client.put(_url(sid) + "/edits", json={"kpi_scores": {"E": {KPI: 80}}}).status_code == 200
    assert not ai and _row(db, sid)["evidence"]["reason"] == OLD


def test_the_writer_is_told_not_to_invent_evidence():
    assert "never invent" in rewrite.SYSTEM.lower()
    assert "page number" in rewrite.SYSTEM.lower()


# --- BFSI --------------------------------------------------------------------------------------

def _bfsi(db):
    detail = scoring.category_detail([(3, {"E1": 40, "E2": 80}, {"E1": OLD})], ["E1", "E2"])
    ai = {"scoring_method": "kpi_score", "e_score": detail["score"], "s_score": 0.0, "g_score": 0.0,
          "kpi_coverage": {"Environment": detail}, "reasons": {"E": [], "S": [], "G": []}}
    bfsi_store.hashes_collection().insert_one({"hash": "sha-1", "pages": [
        {"unit": 0, "page_no": 3, "text": PAGE_TEXT}, {"unit": 1, "page_no": 3, "text": "Second unit."},
        {"unit": 2, "page_no": 4, "text": "Unrelated page."}]})
    return _seed_bfsi(db, ai_analysis=ai, e_score=detail["score"], s_score=0.0, g_score=0.0,
                      text_sha256="sha-1", loan_type="Agriculture Loan")


def test_a_bfsi_kpi_edit_rewrites_its_reason_from_the_cached_page(admin_client, db, ai):
    sid = _bfsi(db)
    assert admin_client.put(_url(sid, "bfsi") + "/edits", json={"kpi_scores": {"E": {"E1": 100}}}).status_code == 200

    assert len(ai) == 1 and ai[0][0] == "bfsi"
    assert PAGE_TEXT in ai[0][2] and "Second unit." in ai[0][2] and "Unrelated page" not in ai[0][2]
    row = db.bfsi_submissions.find_one({"_id": sid})["ai_analysis"]["kpi_coverage"]["Environment"]["kpis"][0]
    assert row["score"] == 100 and row["evidence"]["reason"] == "Rewritten for the new score (1)"


def test_a_bfsi_analysts_own_reason_reaches_the_report(admin_client, db, ai):
    """It never did: only the ESG editor applied a corrected KPI reason (gap found 2026-10-09)."""
    sid = _bfsi(db)
    admin_client.put(_url(sid, "bfsi") + "/edits", json={"fields": {"kpi_reasons": {"E": {"E1": "Mine."}}}})
    row = db.bfsi_submissions.find_one({"_id": sid})["ai_analysis"]["kpi_coverage"]["Environment"]["kpis"][0]
    assert row["evidence"]["reason"] == "Mine."
    assert not ai
