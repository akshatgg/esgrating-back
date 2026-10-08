"""A score edited after the rating text was written (user, 2026-10-08): the text and its page
references stay exactly as written, and only the pillar and overall scores quoted in it
follow the edit. Raising one KPI used to rewrite the whole text -- from KPI rows the edit had
stripped of their evidence, so the new text cited no page at all."""
from datetime import datetime, timezone

import pytest

from app.core.config import settings
from app.esg import scoring
from app.reports import summary
from tests.test_rating_summary import KPIS, SCORES, TEXT, _seed_kpis, _text_of

# Every KPI that scored was found on sheet 12, with a reason the scoring call gave.
PAGE = 12


def _report(db):
    _seed_kpis(db)
    coverage, s = {}, {}
    for code, name in summary.PILLARS:
        names = [m for _, _, m in KPIS[code]]
        scores = {k: SCORES[k] for k in names if k in SCORES}
        detail = scoring.category_detail([(PAGE, scores, {k: f"{k} disclosed in full" for k in scores})], names)
        coverage[name], s[code] = detail, detail["score"]
    final = {"scoring_method": "kpi_score", "kpi_coverage": coverage, "sector": "Manufacturing",
             "environmental_score": s["E"], "social_score": s["S"], "governance_score": s["G"],
             "composite_score": scoring.composite_score(s["E"], s["S"], s["G"])}
    scoring.grade_all(final)
    return db.esg_submissions.insert_one({
        "company_name": "Acme Ltd", "email": "asha@example.com", "name": "Asha", "report_year": "2025-2026",
        "analyzed_at": datetime(2026, 9, 17, tzinfo=timezone.utc), "final": final}).inserted_id


# E is 43.33 (C, Average) and the overall 40.54 as analysed; raising Waste management
# policy to 100 takes E to 76.67 (B+, Very Good).
WRITTEN = {
    **TEXT,
    "executive_summary": "Acme is rated C overall with a score of 40.54.",
    "pillar_narratives": {
        "E": "Environment receives a C grade with a score of 43.33, an Average result. "
             "Water withdrawals are measured (p.12) but no Waste management policy (0) was found.",
        "S": "Social scores 35 (grade D).",
        "G": "Governance scores 42.5 (grade C), with an anti-bribery policy (p.12).",
    },
    "strengths": ["Water withdrawals are measured against a 2030 target (p.12)."],
}
RAISED = {"kpi_scores": {"E": {"Waste management policy": 100}}}


@pytest.fixture
def ai(monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "esg_openai_api_key", "test-key")

    def ask(kind, system, user):
        calls.append(user)
        return dict(WRITTEN)

    monkeypatch.setattr(summary, "_ask_ai", ask)
    return calls


def _url(sid):
    return f"/api/admin/esg/submissions/{sid}"


# --- the KPI rows keep their evidence ------------------------------------------------------

def _rows(db, sid, pillar="Environment"):
    final = db.esg_submissions.find_one({"_id": sid})["final"]
    return {r["kpi"]: r for r in final["kpi_coverage"][pillar]["kpis"]}


def test_editing_one_kpi_keeps_the_evidence_of_the_kpis_beside_it(admin_client, db):
    sid = _report(db)
    assert admin_client.put(_url(sid) + "/report/edits", json=RAISED).status_code == 200

    rows = _rows(db, sid)
    for kpi in ("Water withdrawn", "Water related targets"):
        ev = rows[kpi]["evidence"]
        assert (ev["page"], ev["reason"]) == (PAGE, f"{kpi} disclosed in full"), kpi


def test_the_edited_kpi_keeps_its_own_evidence(admin_client, db):
    sid = _report(db)
    admin_client.put(_url(sid) + "/report/edits", json={"kpi_scores": {"E": {"Water withdrawn": 100}}})

    row = _rows(db, sid)["Water withdrawn"]
    assert row["score"] == 100
    assert (row["evidence"]["page"], row["evidence"]["reason"]) == (PAGE, "Water withdrawn disclosed in full")


def test_an_edited_kpi_still_keeps_its_evidence_when_rescored_directly():
    detail = scoring.category_detail([(PAGE, {"A": 80}, {"A": "Audited"})], ["A", "B"])
    edited = scoring.rescore_category(detail, {"A": 100})
    assert edited["kpis"][0]["score"] == 100
    ev = edited["kpis"][0]["evidence"]
    assert (ev["page"], ev["reason"]) == (PAGE, "Audited")


# --- the text stays as written ------------------------------------------------------------

def test_a_kpi_edit_keeps_the_text_and_its_page_references(admin_client, db, ai):
    sid = _report(db)
    admin_client.post(_url(sid) + "/reports")
    written = len(ai)

    admin_client.put(_url(sid) + "/report/edits", json=RAISED)
    body = admin_client.get(_url(sid) + "/report").json()

    assert body["narrative_stale"] is False
    assert body["narrative"]["strengths"] == WRITTEN["strengths"]
    assert "(p.12)" in body["narrative"]["pillar_narratives"]["G"]
    assert len(ai) == written  # nothing was rewritten


def test_the_word_summary_after_a_kpi_edit_keeps_the_text(admin_client, db, ai):
    sid = _report(db)
    admin_client.post(_url(sid) + "/reports")
    written = len(ai)

    admin_client.put(_url(sid) + "/report/edits", json=RAISED)
    text = _text_of(admin_client.get(_url(sid) + "/summary").content)

    assert len(ai) == written
    assert "Water withdrawals are measured against a 2030 target (p.12)." in text
    assert "Governance scores 42.5 (grade C), with an anti-bribery policy (p.12)." in text


def test_the_edited_pillar_score_and_grade_follow_in_the_text(admin_client, db, ai):
    sid = _report(db)
    admin_client.post(_url(sid) + "/reports")
    admin_client.put(_url(sid) + "/report/edits", json=RAISED)

    text = admin_client.get(_url(sid) + "/report").json()["narrative"]
    # Only the score and its grade letter move; every other word, "Average" included, is
    # left as written (user, 2026-10-08).
    assert text["pillar_narratives"]["E"] == (
        "Environment receives a B+ grade with a score of 76.67, an Average result. "
        "Water withdrawals are measured (p.12) but no Waste management policy (100) was found.")
    # The pillars that did not move are left exactly as written.
    assert text["pillar_narratives"]["S"] == WRITTEN["pillar_narratives"]["S"]
    assert text["pillar_narratives"]["G"] == WRITTEN["pillar_narratives"]["G"]
    # The overall moved with E.
    overall = db.esg_submissions.find_one({"_id": sid})["final"]["composite_score"]
    assert text["executive_summary"] == f"Acme is rated C overall with a score of {summary._fmt(overall)}."


def test_text_written_after_an_edit_follows_the_next_edit_from_its_own_scores(admin_client, db, ai):
    """Written for E at 76.67, then E moved again: the text is restated from 76.67, the
    figure it actually quotes, not from the score the analysis first gave."""
    sid = _report(db)
    admin_client.put(_url(sid) + "/report/edits", json=RAISED)
    ai_text = dict(WRITTEN, pillar_narratives={**WRITTEN["pillar_narratives"],
                                               "E": "Environment scores 76.67 (grade B+)."})
    summary._ask_ai, original = (lambda *a: dict(ai_text)), summary._ask_ai
    try:
        admin_client.post(_url(sid) + "/reports")
    finally:
        summary._ask_ai = original

    admin_client.put(_url(sid) + "/report/edits",
                     json={"kpi_scores": {"E": {"Waste management policy": 100, "Water related targets": 100}}})
    text = admin_client.get(_url(sid) + "/report").json()["narrative"]
    assert text["pillar_narratives"]["E"] == "Environment scores 96.67 (grade A+)."


def test_a_new_analysis_still_gets_new_text(admin_client, db, ai):
    """A re-analysis replaces the result and clears every edit; text written for the old
    result must not survive it."""
    sid = _report(db)
    admin_client.post(_url(sid) + "/reports")
    db.esg_submissions.update_one({"_id": sid}, {"$set": {"final.environmental_score": 88.0}})

    body = admin_client.get(_url(sid) + "/report").json()
    assert body["narrative"] is None and body["narrative_stale"] is True


# --- restating the scores in written text --------------------------------------------------

def _restate(text, old, new):
    return summary._restate(text, [summary._score_change(old, new)])


def test_a_page_reference_with_the_same_number_is_never_touched():
    assert _restate("A score of 43.33 (p.43.33) and p.43", 43.33, 76.67) == "A score of 76.67 (p.43.33) and p.43"


def test_only_the_sentence_that_quotes_the_score_has_its_grade_changed():
    text = "Environment scores 43.33 (grade C). Social is also grade C."
    assert _restate(text, 43.33, 76.67) == "Environment scores 76.67 (grade B+). Social is also grade C."


def test_b_is_not_read_as_part_of_b_plus():
    assert _restate("It scores 75 (grade B+).", 75, 65) == "It scores 65 (grade B)."
    assert _restate("It scores 65 (grade B).", 65, 75) == "It scores 75 (grade B+)."


def test_the_article_follows_the_new_grade():
    assert _restate("It earns a C grade with 43.33.", 43.33, 85) == "It earns an A grade with 85."
    assert _restate("It earns an A grade with 85.", 85, 43.33) == "It earns a C grade with 43.33."


def test_a_score_written_to_one_decimal_is_restated_to_one():
    assert _restate("Environment scores 43.3.", 43.33, 76.67) == "Environment scores 76.7."


def test_a_kpi_score_is_restated_only_beside_its_own_name():
    change = summary._kpi_change("Waste policy", 0, 100)
    text = "No Waste policy (0) was found; 0 of 3 KPIs (0) scored."
    assert summary._restate(text, [change]) == "No Waste policy (100) was found; 0 of 3 KPIs (0) scored."


def test_an_unchanged_score_changes_nothing():
    assert summary._score_change(43.33, 43.33) is None


# --- reports edited before the fix ---------------------------------------------------------

def _stripped_by_the_old_editor(admin_client, db):
    """A report saved by the old editor: the edit stored, every KPI of the pillar stripped
    of its evidence."""
    sid = _report(db)
    admin_client.put(_url(sid) + "/report/edits", json=RAISED)
    doc = db.esg_submissions.find_one({"_id": sid})
    for row in doc["final"]["kpi_coverage"]["Environment"]["kpis"]:
        row.pop("evidence", None)
    db.esg_submissions.update_one({"_id": sid}, {"$set": {"final": doc["final"]}})
    return sid, doc["final"]


def test_the_repair_puts_back_the_evidence_and_keeps_the_scores(admin_client, db, capsys):
    from scripts import repair_kpi_evidence
    sid, before = _stripped_by_the_old_editor(admin_client, db)

    repair_kpi_evidence.main(["--apply"])

    after = db.esg_submissions.find_one({"_id": sid})["final"]
    rows = {r["kpi"]: r for r in after["kpi_coverage"]["Environment"]["kpis"]}
    assert rows["Water withdrawn"]["evidence"]["page"] == PAGE
    assert rows["Waste management policy"]["score"] == 100
    for key in ("environmental_score", "social_score", "governance_score", "composite_score"):
        assert after[key] == before[key], key
    assert "1 report(s) repaired" in capsys.readouterr().out


def test_the_repair_only_reports_without_apply(admin_client, db, capsys):
    from scripts import repair_kpi_evidence
    sid, _ = _stripped_by_the_old_editor(admin_client, db)

    repair_kpi_evidence.main([])

    rows = db.esg_submissions.find_one({"_id": sid})["final"]["kpi_coverage"]["Environment"]["kpis"]
    assert not any(r.get("evidence") for r in rows)
    assert "1 report(s) to repair" in capsys.readouterr().out
    repair_kpi_evidence.main(["--apply"])
    repair_kpi_evidence.main(["--apply"])  # a second run finds nothing left to do
    assert capsys.readouterr().out.rstrip().endswith("0 report(s) repaired")
