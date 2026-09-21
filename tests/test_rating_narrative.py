# The rating narrative written when a report is analysed (app/reports/summary.py
# write_narrative), and the drivers shape the detailed report renders -- user, 2026-09-20.
import pytest

from app.core.config import settings
from app.reports import summary
from tests.test_rating_summary import TEXT, _esg

NARRATIVE = {
    **TEXT,
    "favourable_factors": "The score favourably factors in measured water performance.",
    "constraints": "It is constrained by the absence of waste disclosure.",
    "strengths": [{"headline": "Measured water performance", "detail": "Water withdrawn scored 90 on p.12."}],
    "weaknesses": [{"headline": "No waste disclosure", "detail": "The report does not evidence a waste policy."}],
}


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
    assert stored["text"]["strengths"][0]["headline"] == "Measured water performance"
    assert stored["fingerprint"] and stored["generated_at"]
    # Two calls now: the summary and pillar assessments, then the drivers.
    assert len(ai) == 2


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
    assert body["narrative"]["strengths"][0]["detail"] == "Water withdrawn scored 90 on p.12."
    assert body["narrative"]["constraints"].startswith("It is constrained by")


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

    monkeypatch.setattr(summary, "NARRATIVE_VERSION", summary.NARRATIVE_VERSION + 1)
    summary.write_narrative("esg", db.esg_submissions.find_one({"_id": sid}))
    assert db.esg_submissions.find_one({"_id": sid})["summary_ai"]["fingerprint"] != first
    assert len(ai) == 4  # two generations, two calls each


# --- a half-written answer (production, 2026-09-21) ---------------------------------

def _half_answer(system, _user):
    """What production returned: valid JSON with the pillar assessments written and the
    drivers simply absent. Asked for together, ~2,500 words was more than it would give."""
    if "pillar_narratives" in system:
        return {k: NARRATIVE[k] for k in
                ("executive_summary", "favourable_factors", "constraints", "pillar_narratives")}
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
    assert len(systems) == 2
    assert "pillar_narratives" in systems[0] and "pillar_narratives" not in systems[1]
    assert '"strengths"' in systems[1]

    text = db.esg_submissions.find_one({"_id": sid})["summary_ai"]["text"]
    assert text["pillar_narratives"] and text["strengths"][0]["headline"]


@pytest.mark.parametrize("missing", ["executive_summary", "favourable_factors", "constraints",
                                     "pillar_narratives"])
def test_the_summary_call_must_answer_with_every_field(admin_client, db, monkeypatch, missing):
    monkeypatch.setattr(settings, "esg_openai_api_key", "test-key")
    answer = {k: v for k, v in NARRATIVE.items() if k != missing}
    monkeypatch.setattr(summary, "_ask_ai", lambda *a: dict(answer))
    sid = _esg(db)

    assert summary.write_narrative("esg", db.esg_submissions.find_one({"_id": sid})) is False


# --- an analyst's corrections (user, 2026-09-21) -------------------------------------

EDITS = {"fields": {"executive_summary": "Corrected by the analyst.",
                    "pillar_narratives": {"E": "The analyst's Environment assessment."},
                    "strengths": [{"headline": "Analyst headline", "detail": "Analyst detail."}]}}


def test_the_report_serves_the_analyst_text_over_the_ai_text(admin_client, db, ai):
    sid = _esg(db)
    summary.write_narrative("esg", db.esg_submissions.find_one({"_id": sid}))
    admin_client.put(f"/api/admin/esg/submissions/{sid}/report/edits", json=EDITS)

    n = admin_client.get(f"/api/admin/esg/submissions/{sid}/report").json()["narrative"]
    assert n["executive_summary"] == "Corrected by the analyst."
    assert n["pillar_narratives"]["E"] == "The analyst's Environment assessment."
    assert n["strengths"][0]["headline"] == "Analyst headline"
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
    sid = _esg(db)
    bad = admin_client.put(f"/api/admin/esg/submissions/{sid}/report/edits",
                           json={"fields": {"strengths": [{"headline": "h", "note": "x"}]}})
    assert bad.status_code == 422 and "unknown key" in bad.json()["detail"]
    # Blank items are dropped rather than stored as empty drivers.
    ok = admin_client.put(f"/api/admin/esg/submissions/{sid}/report/edits",
                          json={"fields": {"strengths": [{"headline": "", "detail": ""}]}})
    assert ok.status_code == 200 and "strengths" not in ok.json()["edits"]["fields"]
