"""The admin Dashboard (GET /api/admin/stats) and the ESG Submissions list
(GET /api/admin/esg/combined) read only the fields they show.

Both used to load submissions whole -- page scores, KPI tables, the written text and a copy
of the original report, up to ~550 KB each -- to show a name, a date, a status and a score.
On the throttled production database the Dashboard's read took 23 of its 24 seconds, and the
list read every submission whole twice per page load (user, 2026-10-08)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.dashboard import router as dashboard
from app.esg import combined

BIG = "x" * 50_000


@pytest.fixture(autouse=True)
def _no_daily_chart(monkeypatch):
    """The 30-day chart groups by $dateToString with a timezone, which mongomock does not
    implement; it reads no submission documents, so it is not what is under test here."""
    monkeypatch.setattr(dashboard, "_daily_series", lambda *a: [])


def _esg(db, **extra):
    return db.esg_submissions.insert_one({
        "company_name": "Acme PLC", "name": "Asha", "status": "new", "analysis_status": "done",
        "created_at": datetime(2026, 10, 1, tzinfo=timezone.utc),
        "final": {"composite_score": 61.234, "composite_score_performance": "B", "page_scores": [BIG]},
        "report_original": {"page_scores": [BIG]}, "summary_ai": {"text": {"x": BIG}}, **extra}).inserted_id


def _bfsi(db, **extra):
    return db.bfsi_submissions.insert_one({
        "borrower_name": "Lanka Mills", "industry": "", "loan_type": "Term", "status": "new",
        "analysis_status": "done", "created_at": datetime(2026, 10, 2, tzinfo=timezone.utc),
        "overall_score": 55.556, "grade": "C", "ai_analysis": {"reasons": [BIG]}, **extra}).inserted_id


def test_the_dashboard_still_shows_the_recent_submissions(admin_client, db):
    sid, bid = _esg(db), _bfsi(db)
    stats = admin_client.get("/api/admin/stats").json()

    assert stats["recent"]["esg"] == [{
        "id": str(sid), "title": "Acme PLC", "subtitle": "Asha", "created_at": "2026-10-01T00:00:00",
        "status": "new", "analysis_status": "done", "score": 61.23, "grade": "B"}]
    bfsi = stats["recent"]["bfsi"][0]
    assert (bfsi["id"], bfsi["title"], bfsi["subtitle"], bfsi["score"], bfsi["grade"]) == \
        (str(bid), "Lanka Mills", "Term", 55.56, "C")


def test_the_dashboard_still_lists_failed_and_waiting_analyses(admin_client, db):
    old = datetime.now(timezone.utc) - timedelta(days=2)
    failed = _esg(db, analysis_status="failed", analysis_error="Timed out")
    waiting = _bfsi(db, analysis_status="idle", created_at=old)
    rows = {r["id"]: r for r in admin_client.get("/api/admin/stats").json()["attention"]}

    assert (rows[str(failed)]["title"], rows[str(failed)]["detail"]) == ("Acme PLC", "Timed out")
    assert (rows[str(waiting)]["title"], rows[str(waiting)]["reason"]) == ("Lanka Mills", "Waiting for analysis")


class _Spy:
    """A collection that records the projection of every find()."""

    def __init__(self, col, seen):
        self._col, self._seen = col, seen

    def find(self, *args, **kwargs):
        self._seen.append(args[1] if len(args) > 1 else kwargs.get("projection"))
        return self._col.find(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._col, name)


def test_no_submission_is_read_whole(admin_client, db, monkeypatch):
    _esg(db, analysis_status="failed")
    _bfsi(db)
    seen = []
    monkeypatch.setattr(dashboard, "_esg_col", lambda: _Spy(db.esg_submissions, seen))
    monkeypatch.setattr(dashboard, "_bfsi_col", lambda: _Spy(db.bfsi_submissions, seen))

    assert admin_client.get("/api/admin/stats").status_code == 200
    assert seen, "the dashboard read no submissions"
    for projection in seen:
        assert projection, "a submission was read whole"
        assert not {"final", "report_original", "summary_ai", "ai_analysis"} & set(projection), projection


def test_the_esg_list_still_shows_each_submission(admin_client, db):
    sid = _esg(db, email="asha@example.com", report_year="2025-2026",
               final={"composite_score": 61.234, "composite_score_performance": "B",
                      "composite_score_performance_label": "Good", "sector": "Banks", "page_scores": [BIG]})
    items = admin_client.get("/api/admin/esg/combined?source=calculator").json()["items"]

    assert items == [{
        "source": "calculator", "id": str(sid), "company": "Acme PLC", "sector": "Banks", "rating": 61.2,
        "grade": "B", "category": "Good", "date": "2026-10-01", "status": "new",
        "contact": {"name": "Asha", "email": "asha@example.com"}, "report_year": "2025-2026"}]


def test_the_esg_list_search_still_matches_the_sector(admin_client, db):
    _esg(db, final={"sector": "Banks", "composite_score": 50.0})
    items = admin_client.get("/api/admin/esg/combined?source=calculator&search=bank").json()["items"]
    assert [i["company"] for i in items] == ["Acme PLC"]


def test_the_esg_list_reads_no_submission_whole(admin_client, db, monkeypatch):
    _esg(db)
    seen = []
    monkeypatch.setattr(combined, "esg_submissions_collection", lambda: _Spy(db.esg_submissions, seen))

    assert admin_client.get("/api/admin/esg/combined").status_code == 200
    assert seen and all(seen), "a submission was read whole"
    for projection in seen:
        assert "final" not in projection and "report_original" not in projection, projection


# --- the ESG list pages in the database ----------------------------------------------------

from bson import ObjectId  # noqa: E402


def _reference(db, search: str, source: str) -> list[dict]:
    """The whole list as the endpoint built it before paging moved into the database: every
    record read, filtered and merged in Python. Each page must be this, sliced."""
    s = search.lower()

    def hit(values):
        return not s or any(s in str(v or "").lower() for v in values)

    items = []
    if source in ("all", "calculator"):
        for d in db.esg_submissions.find():
            f = d.get("final") or {}
            if hit([d.get("company_name"), d.get("name"), d.get("email"), f.get("sector"),
                    f.get("composite_score_performance"), f.get("composite_score_performance_label")]):
                items.append(combined._calc_item(d))
    if source in ("all", "rating"):
        for d in db.esg_ratings.find():
            if hit([d.get(k) for k in ("company_name", "sector", "grade", "category")]):
                items.append(combined.rating_item(d))
    items.sort(key=lambda i: combined._sort_key((i["date"], i)), reverse=True)
    return items


def _mixed_list(db):
    day = datetime(2026, 9, 20, tzinfo=timezone.utc)
    # Same day, inserted in the opposite order to their times: the list orders a day's
    # submissions by id, the database by time, so a page break between them is the case
    # a plain limit gets wrong.
    for i, hour in enumerate((15, 9, 12, 18)):
        db.esg_submissions.insert_one({
            "_id": ObjectId(f"6a{i:022x}"), "company_name": f"Same Day {i}", "name": "N", "email": "e@x",
            "created_at": day.replace(hour=hour), "status": "new",
            "final": {"composite_score": 40 + i, "sector": "Banks" if i % 2 else "Cement"}})
    for i in range(7):
        db.esg_submissions.insert_one({
            "company_name": f"Calc {i}", "name": "N", "email": "e@x", "status": "sent",
            "created_at": day - timedelta(days=3 * i - 4), "final": {"composite_score": 50 + i, "sector": "Banks"}})
    for i in range(12):
        db.esg_ratings.insert_one({
            "s_no": 100 + i, "company_name": f"Rated {i}", "sector": "Banks" if i % 3 else "Tea",
            "grade": "B" if i % 2 else "C", "category": "Good", "esg_rating": 60.0 + i,
            "date_of_rating": (day - timedelta(days=2 * i - 6)).date().isoformat() if i != 5 else ""})


@pytest.mark.parametrize("source", ["all", "calculator", "rating"])
@pytest.mark.parametrize("search", ["", "bank", "b", "same day"])
def test_every_page_is_the_same_as_before(admin_client, db, monkeypatch, source, search):
    monkeypatch.setattr(combined, "PAGE_SIZE", 3)
    _mixed_list(db)
    expected = _reference(db, search, source)

    pages = -(-len(expected) // 3)
    got = []
    for page in range(1, pages + 2):
        body = admin_client.get("/api/admin/esg/combined", params={"source": source, "search": search,
                                                                    "page": page}).json()
        assert (body["total"], body["pages"]) == (len(expected), pages), page
        got += body["items"]
    assert got == expected


class _Counting:
    """A collection whose find() counts the documents it hands back."""

    def __init__(self, col):
        self._col, self.read = col, 0

    def find(self, *args, **kwargs):
        outer, cursor = self, self._col.find(*args, **kwargs)

        class _Cursor:
            def sort(self, *a, **k):
                cursor.sort(*a, **k)
                return self

            def limit(self, n):
                cursor.limit(n)
                return self

            def __iter__(self):
                for d in cursor:
                    outer.read += 1
                    yield d
        return _Cursor()

    def __getattr__(self, name):
        return getattr(self._col, name)


def test_the_first_page_reads_one_page_of_each_kind_not_everything(admin_client, db, monkeypatch):
    monkeypatch.setattr(combined, "PAGE_SIZE", 3)
    for i in range(20):
        db.esg_ratings.insert_one({"s_no": i, "company_name": f"R{i}", "sector": "S", "grade": "B",
                                   "category": "Good", "esg_rating": 50.0, "date_of_rating": f"2026-01-{i + 1:02d}"})
        db.esg_submissions.insert_one({"company_name": f"C{i}", "status": "new", "final": {},
                                       "created_at": datetime(2026, 2, i + 1, tzinfo=timezone.utc)})
    ratings, subs = _Counting(db.esg_ratings), _Counting(db.esg_submissions)
    monkeypatch.setattr(combined, "ratings_collection", lambda: ratings)
    monkeypatch.setattr(combined, "esg_submissions_collection", lambda: subs)

    body = admin_client.get("/api/admin/esg/combined").json()
    assert body["total"] == 40 and len(body["items"]) == 3
    assert ratings.read == 3 and subs.read == 3


# --- the report editor reads one run, and only its page scores -----------------------------

import json  # noqa: E402

from app.esg import store as esg_store  # noqa: E402
from tests.test_esg_routes import _seed_submission  # noqa: E402


def _runs(db, cid):
    """Two analysis runs of one report. A run carries every page's text and KPI detail --
    ~5.5 MB on production -- of which the report editor uses four fields per page."""
    def run(composite, score, reason):
        return {"company_id": cid, "filename": ["report.pdf"], "composite_score": composite, "analysis": [
            {"filename": "report.pdf", "category": "Environment", "page_no": 3, "text": BIG, "kpis": [BIG],
             "printed_no": 1, "analysis": json.dumps({"reason": reason, "score": score})},
            {"filename": "report.pdf", "category": "Social", "page_no": 4, "text": BIG, "kpis": [BIG],
             "page_score": 70, "analysis": json.dumps({"reason": f"{reason} S", "score": 10})},
        ]}
    db.esg_report.insert_one(run(40.0, 80, "matched run"))
    db.esg_report.insert_one(run(60.0, 20, "newest run"))


def _report_sub(db, cid):
    return _seed_submission(db, company_id=cid, original_filename="report.pdf", final={
        "composite_score": 40.0, "environmental_score": 80.0, "social_score": 70.0, "governance_score": 0.0})


def test_the_editor_shows_the_page_scores_of_the_run_behind_the_report(admin_client, db):
    cid = ObjectId()
    _runs(db, cid)
    sid = _report_sub(db, cid)

    pages = admin_client.get(f"/api/admin/esg/submissions/{sid}/report").json()["pages"]
    assert pages["E"] == [{"page": 3, "score": 80.0, "original_score": 80.0, "reason": "matched run"}]
    assert pages["S"] == [{"page": 4, "score": 70.0, "original_score": 70.0, "reason": "matched run S"}]


class _RunSpy:
    def __init__(self, col):
        self._col, self.finds, self.find_ones = col, [], []

    def find(self, *args, **kwargs):
        self.finds.append(args[1] if len(args) > 1 else kwargs.get("projection"))
        return self._col.find(*args, **kwargs)

    def find_one(self, *args, **kwargs):
        self.find_ones.append(args[1] if len(args) > 1 else kwargs.get("projection"))
        return self._col.find_one(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._col, name)


def test_the_editor_reads_no_page_text_or_kpi_detail(admin_client, db, monkeypatch):
    cid = ObjectId()
    _runs(db, cid)
    sid = _report_sub(db, cid)
    spy = _RunSpy(db.esg_report)
    monkeypatch.setattr(esg_store, "esg_collection", lambda: spy)

    assert admin_client.get(f"/api/admin/esg/submissions/{sid}/report").status_code == 200
    # Candidate runs are compared on their composite alone ...
    assert [set(p) - {"_id"} for p in spy.finds] == [{"composite_score"}]
    # ... and only the chosen one is read, without its page text or KPI detail.
    assert len(spy.find_ones) == 1
    projection = spy.find_ones[0]
    assert projection and not {"analysis.text", "analysis.kpis"} & set(projection)
    assert "analysis" not in projection and "analysis.analysis" in projection


def test_the_csv_export_still_reads_the_whole_run_behind_the_report(admin_client, db):
    cid = ObjectId()
    _runs(db, cid)
    sid = _report_sub(db, cid)

    body = admin_client.get(f"/api/admin/esg/export_csv/{cid}?submission_id={sid}").text
    assert "matched run" in body and "newest run" not in body
    assert BIG[:100] in body  # the page text column
