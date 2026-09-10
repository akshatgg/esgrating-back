# app/dashboard/router.py
# Read-only admin overview: aggregate counts across esg_submissions, bfsi_submissions,
# esg_ratings and contact_messages for the superadmin dashboard. No writes, no new
# collections -- just reads through the same get_db() the other admin routers use.
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends

from app.auth.deps import require_admin
from app.bfsi.options import INDUSTRIES
from app.core.db import get_db

router = APIRouter(prefix="/api/admin", tags=["admin-dashboard"])

GRADE_ORDER = ["A+", "A", "B+", "B", "C", "D"]
DAILY_WINDOW_DAYS = 30
RECENT_LIMIT = 5
ATTENTION_LIMIT = 10
STALE_AFTER = timedelta(hours=24)


def _iso(dt):
    return dt.isoformat() if isinstance(dt, datetime) else dt


def _esg_col():
    return get_db()["esg_submissions"]


def _bfsi_col():
    return get_db()["bfsi_submissions"]


def _ratings_col():
    return get_db()["esg_ratings"]


def _contacts_col():
    return get_db()["contact_messages"]


def _status_counts(col) -> dict:
    """Bucket every doc exactly once: analysis_status running/failed take priority
    over the status field, matching the pipeline's own state machine."""
    total = col.count_documents({})
    running = col.count_documents({"analysis_status": "running"})
    failed = col.count_documents({"analysis_status": "failed"})
    not_active = {"analysis_status": {"$nin": ["running", "failed"]}}
    sent = col.count_documents({**not_active, "status": "sent"})
    report_generated = col.count_documents({**not_active, "status": "report_generated"})
    new = total - running - failed - sent - report_generated
    return {
        "total": total,
        "new": new,
        "running": running,
        "report_generated": report_generated,
        "sent": sent,
        "failed": failed,
        "reports_generated": report_generated + sent,
    }


def _daily_counts(col, since: datetime) -> dict:
    pipeline = [
        {"$match": {"created_at": {"$gte": since}}},
        {
            "$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at", "timezone": "UTC"}},
                "n": {"$sum": 1},
            }
        },
    ]
    return {row["_id"]: row["n"] for row in col.aggregate(pipeline)}


def _daily_series(esg_col, bfsi_col) -> list:
    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=DAILY_WINDOW_DAYS - 1)
    since = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
    esg_daily = _daily_counts(esg_col, since)
    bfsi_daily = _daily_counts(bfsi_col, since)
    out = []
    for i in range(DAILY_WINDOW_DAYS):
        key = (start_date + timedelta(days=i)).isoformat()
        out.append({"date": key, "esg": esg_daily.get(key, 0), "bfsi": bfsi_daily.get(key, 0)})
    return out


def _recent_esg(col) -> list:
    items = []
    for d in col.find().sort("_id", -1).limit(RECENT_LIMIT):
        final = d.get("final") or {}
        score = final.get("composite_score")
        items.append({
            "id": str(d["_id"]),
            "title": d.get("company_name"),
            "subtitle": d.get("name"),
            "created_at": _iso(d.get("created_at")),
            "status": d.get("status"),
            "analysis_status": d.get("analysis_status"),
            "score": round(score, 2) if score is not None else None,
            "grade": final.get("composite_score_performance"),
        })
    return items


def _recent_bfsi(col) -> list:
    items = []
    for d in col.find().sort("_id", -1).limit(RECENT_LIMIT):
        industry = d.get("industry")
        label = (INDUSTRIES.get(industry) or {}).get("label") if industry else None
        score = d.get("overall_score")
        items.append({
            "id": str(d["_id"]),
            "title": d.get("borrower_name"),
            "subtitle": label or d.get("loan_type"),
            "created_at": _iso(d.get("created_at")),
            "status": d.get("status"),
            "analysis_status": d.get("analysis_status"),
            "score": round(score, 2) if score is not None else None,
            "grade": d.get("grade"),
        })
    return items


def _recent_messages(col) -> list:
    items = []
    for d in col.find().sort("_id", -1).limit(RECENT_LIMIT):
        message = d.get("message") or ""
        items.append({
            "id": str(d["_id"]),
            "title": d.get("name"),
            "subtitle": d.get("email"),
            "created_at": _iso(d.get("created_at")),
            "preview": message[:120],
        })
    return items


def _attention_rows(col, kind: str, title_field: str, cutoff: datetime) -> list:
    rows = []
    for d in col.find({"analysis_status": "failed"}).sort("_id", -1).limit(ATTENTION_LIMIT):
        rows.append((d.get("created_at"), {
            "kind": kind,
            "id": str(d["_id"]),
            "title": d.get(title_field),
            "reason": "Analysis failed",
            "detail": d.get("analysis_error"),
            "created_at": _iso(d.get("created_at")),
        }))
    stale_query = {
        "status": "new",
        "analysis_status": {"$in": ["idle", None]},
        "created_at": {"$lt": cutoff},
    }
    for d in col.find(stale_query).sort("_id", -1).limit(ATTENTION_LIMIT):
        rows.append((d.get("created_at"), {
            "kind": kind,
            "id": str(d["_id"]),
            "title": d.get(title_field),
            "reason": "Waiting for analysis",
            "detail": "Submitted more than 24 hours ago",
            "created_at": _iso(d.get("created_at")),
        }))
    return rows


def _attention(esg_col, bfsi_col) -> list:
    now = datetime.now(timezone.utc)
    cutoff = now - STALE_AFTER
    rows = _attention_rows(esg_col, "esg", "company_name", cutoff) + _attention_rows(
        bfsi_col, "bfsi", "borrower_name", cutoff
    )
    rows.sort(key=lambda r: r[0] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return [item for _, item in rows[:ATTENTION_LIMIT]]


def _ratings_stats(col) -> dict:
    total = col.count_documents({})
    avg_row = list(col.aggregate([{"$group": {"_id": None, "avg": {"$avg": "$esg_rating"}}}]))
    average = round(avg_row[0]["avg"], 1) if avg_row and avg_row[0]["avg"] is not None else 0.0
    by_grade = {g: 0 for g in GRADE_ORDER}
    for row in col.aggregate([{"$group": {"_id": "$grade", "n": {"$sum": 1}}}]):
        if row["_id"] in by_grade:
            by_grade[row["_id"]] += row["n"]
    return {"total": total, "average": average, "by_grade": by_grade}


def _messages_stats(col) -> dict:
    now = datetime.now(timezone.utc)
    total = col.count_documents({})
    last_7_days = col.count_documents({"created_at": {"$gte": now - timedelta(days=7)}})
    return {"total": total, "last_7_days": last_7_days}


@router.get("/stats")
def get_stats(admin: str = Depends(require_admin)):
    esg_col = _esg_col()
    bfsi_col = _bfsi_col()
    contacts_col = _contacts_col()

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "esg": _status_counts(esg_col),
        "bfsi": _status_counts(bfsi_col),
        "ratings": _ratings_stats(_ratings_col()),
        "messages": _messages_stats(contacts_col),
        "daily": _daily_series(esg_col, bfsi_col),
        "recent": {
            "esg": _recent_esg(esg_col),
            "bfsi": _recent_bfsi(bfsi_col),
            "messages": _recent_messages(contacts_col),
        },
        "attention": _attention(esg_col, bfsi_col),
    }
