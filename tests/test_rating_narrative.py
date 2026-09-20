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
    assert len(ai) == 1


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
    assert len(ai) == 2
