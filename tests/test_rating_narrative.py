# The rating narrative written when a report is analysed (app/reports/summary.py
# write_narrative), and the drivers shape the detailed report renders -- user, 2026-09-20.
import pathlib

import pytest

from app.core.config import settings
from app.reports import summary
from tests.test_rating_summary import TEXT, _esg

# The client's narrative section 26 shape: strengths and weaknesses are short explanatory
# strings and each pillar narrative is one string, with no fields beyond the ones he lists.
NARRATIVE = dict(TEXT)


@pytest.fixture
def ai(monkeypatch):
    """A fake AI plus a key, so write_narrative's guard passes without a real call."""
    calls = []
    monkeypatch.setattr(settings, "esg_openai_api_key", "test-key")

    def ask(kind, system, user):
        calls.append((kind, system, user))
        return dict(NARRATIVE)

    monkeypatch.setattr(summary, "_ask_ai", ask)
    return calls


def test_write_narrative_stores_the_text(admin_client, db, ai):
    sid = _esg(db)
    doc = db.esg_submissions.find_one({"_id": sid})

    assert summary.write_narrative("esg", doc) is True
    stored = db.esg_submissions.find_one({"_id": sid})["summary_ai"]
    assert stored["text"]["strengths"][0] == "Water withdrawn (90)"
    assert stored["fingerprint"] and stored["generated_at"]
    # Two calls now: the summary and pillar assessments, then the drivers.
    assert len(ai) == len(summary.PASSES)


def test_write_narrative_is_skipped_without_a_key(admin_client, db, ai, monkeypatch):
    """No key means nothing to ask -- and no network round trip to find that out."""
    monkeypatch.setattr(settings, "esg_openai_api_key", "")
    sid = _esg(db)
    doc = db.esg_submissions.find_one({"_id": sid})

    assert summary.write_narrative("esg", doc) is False
    assert not ai
    assert "summary_ai" not in db.esg_submissions.find_one({"_id": sid})


def test_write_narrative_never_fails_the_analysis(admin_client, db, monkeypatch):
    monkeypatch.setattr(settings, "esg_openai_api_key", "test-key")
    monkeypatch.setattr(summary, "_ask_ai", lambda *a: (_ for _ in ()).throw(RuntimeError("API down")))
    sid = _esg(db)

    assert summary.write_narrative("esg", db.esg_submissions.find_one({"_id": sid})) is False
    assert "summary_ai" not in db.esg_submissions.find_one({"_id": sid})


def test_write_narrative_skips_a_report_with_no_kpis(admin_client, db, ai):
    sid = db.esg_submissions.insert_one({"company_name": "Acme", "final": {"composite_score": 50.0}}).inserted_id
    assert summary.write_narrative("esg", db.esg_submissions.find_one({"_id": sid})) is False
    assert not ai


def test_the_report_payload_carries_the_narrative(admin_client, db, ai):
    sid = _esg(db)
    summary.write_narrative("esg", db.esg_submissions.find_one({"_id": sid}))

    body = admin_client.get(f"/api/admin/esg/submissions/{sid}/report").json()
    assert body["narrative"]["strengths"][1] == "Anti-bribery/corruption policy (85)"
    assert body["narrative"]["rating_rationale"].startswith("Scores follow")


def test_the_report_payload_has_no_narrative_before_one_is_written(admin_client, db):
    sid = _esg(db)
    assert admin_client.get(f"/api/admin/esg/submissions/{sid}/report").json()["narrative"] is None


# --- the drivers shape -------------------------------------------------------------

def test_drivers_keeps_headline_and_detail():
    out = summary.drivers([{"headline": "H", "detail": "D"}, {"headline": "", "detail": ""}])
    assert out == [{"headline": "H", "detail": "D"}]


def test_drivers_still_reads_text_written_before_the_shape_changed():
    """Reports summarised under the old schema keep working: the string is the detail."""
    assert summary.drivers(["Water withdrawn (90)"]) == [{"headline": "", "detail": "Water withdrawn (90)"}]


def test_driver_lines_join_the_two_for_word():
    assert summary._driver_lines([{"headline": "H", "detail": "D"}]) == ["H — D"]


def test_a_changed_shape_does_not_reuse_old_cached_text(admin_client, db, ai, monkeypatch):
    """The fingerprint covers the schema version, not just the rating data."""
    sid = _esg(db)
    doc = db.esg_submissions.find_one({"_id": sid})
    summary.write_narrative("esg", doc)
    first = db.esg_submissions.find_one({"_id": sid})["summary_ai"]["fingerprint"]

    monkeypatch.setattr(summary, "NARRATIVE_VERSION", "CFC_ESG_RATING_V9.9_2099_01_01")
    summary.write_narrative("esg", db.esg_submissions.find_one({"_id": sid}))
    assert db.esg_submissions.find_one({"_id": sid})["summary_ai"]["fingerprint"] != first
    assert len(ai) == len(summary.PASSES) * 2  # two generations, one call per pass


# --- a half-written answer (production, 2026-09-21) ---------------------------------

def _half_answer(system, _user):
    """What production returned: valid JSON with a pass's own field simply absent. Asked
    for together, ~2,500 words was more than the model would give, which is why the
    narrative is written in passes."""
    if "THIS REQUEST" in system and '"executive_summary"' in system.rsplit("THIS REQUEST", 1)[1]:
        return {"executive_summary": NARRATIVE["executive_summary"],
                "pillar_narratives": NARRATIVE["pillar_narratives"]}
    return {}


def test_an_answer_that_leaves_out_the_drivers_is_not_stored(admin_client, db, monkeypatch):
    monkeypatch.setattr(settings, "esg_openai_api_key", "test-key")
    monkeypatch.setattr(summary, "_ask_ai", lambda kind, system, user: _half_answer(system, user))
    sid = _esg(db)

    assert summary.write_narrative("esg", db.esg_submissions.find_one({"_id": sid})) is False
    # Nothing cached: a stored half-narrative would be served for the life of the report.
    assert "summary_ai" not in db.esg_submissions.find_one({"_id": sid})


def test_the_drivers_come_from_their_own_call(admin_client, db, ai):
    """The summary and the drivers are asked for separately, then merged."""
    sid = _esg(db)
    summary.write_narrative("esg", db.esg_submissions.find_one({"_id": sid}))

    systems = [s for _kind, s, _user in ai]
    assert len(systems) == len(summary.PASSES)
    # Each pass asks for its own fields and says to leave the rest out, so no single
    # answer has to carry the whole narrative.
    asks = [s.rsplit("THIS REQUEST", 1)[1] for s in systems]
    assert '"executive_summary"' in asks[0] and '"strengths"' not in asks[0]
    assert '"strengths"' in asks[1] and '"weaknesses"' in asks[2]
    assert '"rating_rationale"' in asks[3]

    text = db.esg_submissions.find_one({"_id": sid})["summary_ai"]["text"]
    assert text["pillar_narratives"] and isinstance(text["strengths"][0], str)


@pytest.mark.parametrize("missing", ("executive_summary", "pillar_narratives"))
def test_the_summary_call_must_answer_with_every_field(admin_client, db, monkeypatch, missing):
    monkeypatch.setattr(settings, "esg_openai_api_key", "test-key")
    answer = {k: v for k, v in NARRATIVE.items() if k != missing}
    monkeypatch.setattr(summary, "_ask_ai", lambda *a: dict(answer))
    sid = _esg(db)

    assert summary.write_narrative("esg", db.esg_submissions.find_one({"_id": sid})) is False


# --- an analyst's corrections (user, 2026-09-21) -------------------------------------

EDITS = {"fields": {"executive_summary": "Corrected by the analyst.",
                    "pillar_narratives": {"E": "The analyst's Environment assessment."},
                    "strengths": ["The analyst's first strength."]}}


def test_the_report_serves_the_analyst_text_over_the_ai_text(admin_client, db, ai):
    sid = _esg(db)
    summary.write_narrative("esg", db.esg_submissions.find_one({"_id": sid}))
    admin_client.put(f"/api/admin/esg/submissions/{sid}/report/edits", json=EDITS)

    n = admin_client.get(f"/api/admin/esg/submissions/{sid}/report").json()["narrative"]
    assert n["executive_summary"] == "Corrected by the analyst."
    assert n["pillar_narratives"]["E"] == "The analyst's Environment assessment."
    assert n["strengths"][0] == "The analyst's first strength."
    # A pillar left alone keeps what the AI wrote.
    assert n["pillar_narratives"]["S"] == NARRATIVE["pillar_narratives"]["S"]


def test_an_untouched_report_serves_the_ai_text(admin_client, db, ai):
    sid = _esg(db)
    summary.write_narrative("esg", db.esg_submissions.find_one({"_id": sid}))
    n = admin_client.get(f"/api/admin/esg/submissions/{sid}/report").json()["narrative"]
    assert n["executive_summary"] == NARRATIVE["executive_summary"]


def test_the_word_summary_uses_the_analyst_text_too(admin_client, db):
    """The report and the .docx must not say different things about the same rating."""
    doc = {"report_edits": EDITS, "summary_ai": {}}
    out = summary.with_edits({"executive_summary": "AI text", "pillar_narratives": {"E": "ai E", "S": "ai S"}}, doc)
    assert out["executive_summary"] == "Corrected by the analyst."
    assert out["pillar_narratives"] == {"E": "The analyst's Environment assessment.", "S": "ai S"}


def test_a_correction_must_still_be_plain_text(admin_client, db):
    sid = _esg(db)
    resp = admin_client.put(f"/api/admin/esg/submissions/{sid}/report/edits",
                            json={"fields": {"executive_summary": "<script>alert(1)</script>"}})
    assert resp.status_code == 422


def test_driver_corrections_are_validated(admin_client, db):
    """His narrative section 26 makes each strength one string, so the edit takes a list of
    strings -- an object is no longer a valid item."""
    sid = _esg(db)
    bad = admin_client.put(f"/api/admin/esg/submissions/{sid}/report/edits",
                           json={"fields": {"strengths": [{"headline": "h", "note": "x"}]}})
    assert bad.status_code == 422
    # Blank items are dropped rather than stored as empty strengths.
    ok = admin_client.put(f"/api/admin/esg/submissions/{sid}/report/edits",
                          json={"fields": {"strengths": ["", "  "]}})
    assert ok.status_code == 200 and "strengths" not in ok.json()["edits"]["fields"]


# --- the one-pager first, the other reports on request (user, 2026-09-21) -------------

def test_analysing_writes_no_prose(admin_client, db, monkeypatch):
    """An analysis produces the scores and the one-pager. The written rating costs two
    calls and half a minute, and most submissions never open a report that uses it."""
    import app.esg.submissions as subs
    assert "write_narrative" not in pathlib.Path(subs.__file__).read_text()


def test_generate_reports_writes_the_narrative(admin_client, db, ai):
    sid = _esg(db)
    assert admin_client.get(f"/api/admin/esg/submissions/{sid}/report").json()["narrative"] is None

    body = admin_client.post(f"/api/admin/esg/submissions/{sid}/reports").json()
    assert body["narrative"]["strengths"][0] == "Water withdrawn (90)"
    assert len(ai) == len(summary.PASSES)
    assert db.esg_submissions.find_one({"_id": sid})["summary_ai"]["text"]


def test_a_changed_score_makes_the_stored_text_stale(admin_client, db, ai):
    """The complaint this exists for: raise one pillar above another and the prose written
    for the old numbers must not still call the old pillar the strongest."""
    sid = _esg(db)
    admin_client.post(f"/api/admin/esg/submissions/{sid}/reports")
    assert admin_client.get(f"/api/admin/esg/submissions/{sid}/report").json()["narrative"]

    admin_client.put(f"/api/admin/esg/submissions/{sid}/report/edits",
                     json={"pillar_overrides": {"E": 95}})

    body = admin_client.get(f"/api/admin/esg/submissions/{sid}/report").json()
    assert body["narrative"] is None
    assert body["narrative_stale"] is True


def test_regenerating_writes_it_for_the_edited_scores(admin_client, db, ai):
    sid = _esg(db)
    admin_client.post(f"/api/admin/esg/submissions/{sid}/reports")
    admin_client.put(f"/api/admin/esg/submissions/{sid}/report/edits",
                     json={"pillar_overrides": {"E": 95}})

    body = admin_client.post(f"/api/admin/esg/submissions/{sid}/reports").json()
    assert body["narrative"] is not None and body["narrative_stale"] is False
    # The writer was given the edited score, not the one the analysis produced.
    assert '"score": 95' in ai[-1][2]


def test_a_report_with_no_prose_yet_is_not_called_stale(admin_client, db):
    sid = _esg(db)
    body = admin_client.get(f"/api/admin/esg/submissions/{sid}/report").json()
    assert body["narrative"] is None and body["narrative_stale"] is False


def test_generate_reports_needs_a_kpi_scored_report(admin_client, db, ai):
    sid = db.esg_submissions.insert_one({"company_name": "Acme", "final": {"composite_score": 50.0}}).inserted_id
    assert admin_client.post(f"/api/admin/esg/submissions/{sid}/reports").status_code == 409
    assert not ai


def test_generate_reports_without_an_ai_key_is_503(admin_client, db, monkeypatch):
    monkeypatch.setattr(settings, "esg_openai_api_key", "")
    sid = _esg(db)
    assert admin_client.post(f"/api/admin/esg/submissions/{sid}/reports").status_code == 503


# --- the analyst's own pillar weights (user, 2026-09-22) -----------------------------

def _scored(db):
    """A KPI-scored report with the pillar scores from the screenshot."""
    return db.esg_submissions.insert_one({
        "company_name": "Seylan Bank PLC", "report_year": "2025-2026",
        "final": {"scoring_method": "kpi_score",
                  "environmental_score": 15.28, "social_score": 35.09, "governance_score": 58.94,
                  "composite_score": 36.5,
                  "kpi_coverage": {"Environment": {"method": "kpi_score", "score": 15.28,
                                                   "kpis": [{"kpi": "A", "score": 15.28, "points": 15.28,
                                                             "level": "partial", "pages": [1]}]}}},
    }).inserted_id


def test_changing_a_weight_changes_the_overall_score(admin_client, db):
    sid = _scored(db)
    base = f"/api/admin/esg/submissions/{sid}/report"

    # 35 / 30 / 35 is the method's own: 36.50, as the report shows.
    assert admin_client.get(base).json()["effective"]["final"]["composite_score"] == pytest.approx(36.5, abs=0.01)

    body = {"fields": {"weights": {"E": 20, "S": 30, "G": 50}}}
    final = admin_client.post(f"{base}/preview", json=body).json()["effective"]["final"]
    assert final["composite_score"] == pytest.approx(43.05, abs=0.01)
    assert final["weights"] == {"Environment": 20, "Social": 30, "Governance": 50}
    # The grade follows the new score: 43 is C, not D.
    assert final["composite_score_performance"] == "C"


def test_the_weights_are_stored_and_read_everywhere(admin_client, db):
    sid = _scored(db)
    admin_client.put(f"/api/admin/esg/submissions/{sid}/report/edits",
                     json={"fields": {"weights": {"E": 50, "S": 25, "G": 25}}})

    stored = db.esg_submissions.find_one({"_id": sid})["final"]
    assert stored["weights"] == {"Environment": 50, "Social": 25, "Governance": 25}
    # weights_for is the one place every reader comes through -- score, CSV, Word, writer.
    from app.esg import scoring
    assert scoring.weights_for(stored) == {"Environment": 0.5, "Social": 0.25, "Governance": 0.25}


@pytest.mark.parametrize("bad, detail", [
    ({"E": 35, "S": 30}, "needs a mark for E, S and G"),
    ({"E": 35, "S": 30, "G": 101}, "between 0 and 100"),
    ({"E": 35, "S": 30, "G": -1}, "between 0 and 100"),
    ({"E": 35, "S": 30, "G": "thirty"}, "must be a number"),
])
def test_a_weight_must_be_a_mark_for_every_pillar(admin_client, db, bad, detail):
    sid = _scored(db)
    resp = admin_client.put(f"/api/admin/esg/submissions/{sid}/report/edits",
                            json={"fields": {"weights": bad}})
    assert resp.status_code == 422 and detail in resp.json()["detail"]


def test_weights_that_do_not_total_100_are_accepted_and_shown(admin_client, db):
    """Not silently normalised: the report shows the real total so the analyst sees it."""
    sid = _scored(db)
    body = {"fields": {"weights": {"E": 40, "S": 30, "G": 35}}}
    final = admin_client.post(f"/api/admin/esg/submissions/{sid}/report/preview", json=body).json()["effective"]["final"]
    assert sum(final["weights"].values()) == 105
    assert final["composite_score"] == pytest.approx(0.40 * 15.28 + 0.30 * 35.09 + 0.35 * 58.94, abs=0.01)


def test_clearing_the_weights_returns_to_the_methods_own(admin_client, db):
    sid = _scored(db)
    base = f"/api/admin/esg/submissions/{sid}/report"
    admin_client.put(f"{base}/edits", json={"fields": {"weights": {"E": 50, "S": 25, "G": 25}}})
    admin_client.put(f"{base}/edits", json={"fields": {}})

    final = admin_client.get(base).json()["effective"]["final"]
    assert final.get("weights") is None
    assert final["composite_score"] == pytest.approx(36.5, abs=0.01)


def test_the_rating_summary_sentence_follows_the_scores_until_it_is_rewritten(admin_client, db):
    """It is generated from the scores, so a changed weight or pillar moves it on its own.
    An analyst's own wording replaces it, and clearing that returns to the generated one."""
    sid = _scored(db)
    base = f"/api/admin/esg/submissions/{sid}/report"

    # Nothing stored: the report generates the sentence from the scores it carries.
    assert admin_client.get(base).json()["edits"]["fields"] == {}

    own = "Seylan Bank PLC is rated D on the evidence its report provides."
    saved = admin_client.put(f"{base}/edits", json={"fields": {"rating_summary_text": own}}).json()
    assert saved["edits"]["fields"]["rating_summary_text"] == own

    # A weight change still moves the scores underneath it; the wording stays the analyst's.
    final = admin_client.post(f"{base}/preview",
                              json={"fields": {"rating_summary_text": own,
                                               "weights": {"E": 20, "S": 30, "G": 50}}}).json()["effective"]["final"]
    assert final["composite_score"] == pytest.approx(43.05, abs=0.01)

    cleared = admin_client.put(f"{base}/edits", json={"fields": {}}).json()
    assert "rating_summary_text" not in (cleared["edits"] or {}).get("fields", {})
    assert cleared["effective"]["final"]["composite_score"] == pytest.approx(36.5, abs=0.01)


def test_the_rating_summary_sentence_is_plain_text(admin_client, db):
    sid = _scored(db)
    resp = admin_client.put(f"/api/admin/esg/submissions/{sid}/report/edits",
                            json={"fields": {"rating_summary_text": "<b>rated</b>"}})
    assert resp.status_code == 422


# --- correcting a Reason and the KPI Assessment note (user, 2026-09-23) --------------

def _kpi_report(db):
    return db.esg_submissions.insert_one({
        "company_name": "Acme Ltd", "report_year": "2025-2026",
        "final": {"scoring_method": "kpi_score",
                  "environmental_score": 40.0, "social_score": 40.0, "governance_score": 40.0,
                  "composite_score": 40.0,
                  "kpi_coverage": {"Environment": {"method": "kpi_score", "score": 40.0, "kpis": [
                      {"kpi": "Emissions reduction policy", "score": 40.0, "points": 40.0,
                       "level": "partial", "pages": [7],
                       "evidence": {"page": 7, "score": 40.0, "reason": "what the AI wrote"}}]}}},
    }).inserted_id


def test_a_corrected_reason_replaces_the_one_the_ai_wrote(admin_client, db):
    sid = _kpi_report(db)
    base = f"/api/admin/esg/submissions/{sid}/report"
    own = "Climate action is named, but no policy or measured result is given."

    body = {"fields": {"kpi_reasons": {"E": {"Emissions reduction policy": own}}}}
    row = admin_client.post(f"{base}/preview", json=body).json()[
        "effective"]["final"]["kpi_coverage"]["Environment"]["kpis"][0]
    assert row["evidence"]["reason"] == own

    # Saved, it is on the report itself -- so the PDF, the Word summary and the writer
    # all read the corrected text.
    admin_client.put(f"{base}/edits", json=body)
    stored = db.esg_submissions.find_one({"_id": sid})["final"]
    assert stored["kpi_coverage"]["Environment"]["kpis"][0]["evidence"]["reason"] == own


def test_a_reason_can_only_be_set_for_a_kpi_of_this_report(admin_client, db):
    sid = _kpi_report(db)
    resp = admin_client.put(f"/api/admin/esg/submissions/{sid}/report/edits",
                            json={"fields": {"kpi_reasons": {"E": {"Palm oil sourcing policy": "x"}}}})
    assert resp.status_code == 422 and "not a KPI of this report" in resp.json()["detail"]


def test_the_kpi_assessment_note_can_be_reworded(admin_client, db):
    sid = _kpi_report(db)
    own = "Each KPI is scored on the evidence this report provides."
    saved = admin_client.put(f"/api/admin/esg/submissions/{sid}/report/edits",
                             json={"fields": {"kpi_assessment_note": own}}).json()
    assert saved["edits"]["fields"]["kpi_assessment_note"] == own


def test_clearing_a_corrected_reason_restores_the_ai_text(admin_client, db):
    sid = _kpi_report(db)
    base = f"/api/admin/esg/submissions/{sid}/report"
    admin_client.put(f"{base}/edits",
                     json={"fields": {"kpi_reasons": {"E": {"Emissions reduction policy": "mine"}}}})
    admin_client.put(f"{base}/edits", json={"fields": {}})

    row = admin_client.get(base).json()["effective"]["final"]["kpi_coverage"]["Environment"]["kpis"][0]
    assert row["evidence"]["reason"] == "what the AI wrote"
