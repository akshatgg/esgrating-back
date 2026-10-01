"""One KPI edit, every report. A score changed in the editor has to be the score on the ESG
Rating Report, the Detailed Report, the Summary Report page and the Word summary, with the
pillar and the overall rating recomputed from it (user, 2026-10-01)."""
import io
from datetime import datetime, timezone

import docx

from app.esg import scoring
from app.esg.pipeline import composite_score
from tests.test_rating_summary import _seed_kpis, fake_ai  # noqa: F401  (fixture)

META = {"Water withdrawn": {"theme": "Water", "key_issue": "Water use"},
        "Water targets": {"theme": "Water", "key_issue": "Water use"},
        "Waste policy": {"theme": "Waste", "key_issue": "Waste management"}}


def _rolled_up(db):
    """A report whose pillars were scored by the methodology roll-up, as live ones are."""
    _seed_kpis(db)
    kpis = list(META)
    pages = [(3, {"Water withdrawn": 90, "Water targets": 40}, {}, {"Water withdrawn": "Audited data"})]
    coverage = {name: scoring.category_detail(pages, kpis, META, "Materials")
                for name in ("Environment", "Social", "Governance")}
    e, s, g = (coverage[n]["score"] for n in ("Environment", "Social", "Governance"))
    final = {"scoring_method": "kpi_score", "kpi_coverage": coverage, "sector": "Materials",
             "environmental_score": e, "social_score": s, "governance_score": g,
             "composite_score": composite_score(e, s, g)}
    scoring.grade_all(final)
    return db.esg_submissions.insert_one({
        "company_name": "Acme PLC", "email": "a@example.com", "name": "A", "report_year": "2025-2026",
        "analyzed_at": datetime(2026, 10, 1, tzinfo=timezone.utc), "final": final}).inserted_id


def test_kpi_scores_are_editable_on_a_rolled_up_report(admin_client, db):
    sid = _rolled_up(db)
    report = admin_client.get(f"/api/admin/esg/submissions/{sid}/report").json()
    assert report["kpis_editable"] == {"E": True, "S": True, "G": True}


def test_one_kpi_edit_reaches_every_report(admin_client, db, fake_ai):
    sid = _rolled_up(db)
    base = f"/api/admin/esg/submissions/{sid}"
    before = admin_client.get(base + "/report").json()["effective"]["final"]

    assert admin_client.put(base + "/report/edits",
                            json={"kpi_scores": {"E": {"Waste policy": 80}}}).status_code == 200
    report = admin_client.get(base + "/report").json()
    final = report["effective"]["final"]

    # The pillar and the overall rating follow the edit.
    assert final["environmental_score"] > before["environmental_score"]
    assert final["social_score"] == before["social_score"]
    assert abs(final["composite_score"] - composite_score(
        final["environmental_score"], final["social_score"], final["governance_score"])) < 0.01

    # Detailed Report: the KPI row.
    row = next(r for r in final["kpi_coverage"]["Environment"]["kpis"] if r["kpi"] == "Waste policy")
    assert row["score"] == 80
    # Summary Report page: the same KPI row, pillar and overall.
    facts = report["summary"]
    srow = next(r for r in facts["kpi_rows"] if r["pillar"] == "Environment" and r["key"] == "Waste policy")
    assert srow["score"] == 80
    assert facts["pillars"]["E"]["score"] == final["environmental_score"]
    assert facts["overall"] == final["composite_score"]
    # ESG Rating Report: it reads the stored result, which now carries the same numbers.
    stored = db.esg_submissions.find_one({"_id": sid})["final"]
    assert stored["environmental_score"] == final["environmental_score"]
    assert stored["composite_score"] == final["composite_score"]
    # Word summary: the downloaded file shows the edited score.
    data = admin_client.get(base + "/summary").content
    cells = [c.text for t in docx.Document(io.BytesIO(data)).tables for r in t.rows for c in r.cells]
    assert any(a == "Waste policy" and b == "80" for a, b in zip(cells, cells[1:]))
