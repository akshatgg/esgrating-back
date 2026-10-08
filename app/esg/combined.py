# app/esg/combined.py
# Merges the ESG calculator submissions (esg_submissions) with the ESG Rating List
# (esg_ratings, ~461 companies) into one uniform, paginated feed for the admin
# "ESG Submissions" page -- see app/esg/router_admin.py (submission shape/status) and
# app/ratings/store.py + app/ratings/router.py (rating shape/search).
import math
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.auth.deps import require_admin
from app.esg.submissions import esg_submissions_collection
from app.ratings.store import ratings_collection

router = APIRouter(prefix="/api/admin/esg", tags=["admin-esg-combined"])

PAGE_SIZE = 50
VALID_SOURCES = {"all", "calculator", "rating"}

# Search is restricted to these display columns (+ name/email for calculator rows),
# same fields the merged list actually shows -- not esg_rating/date_of_rating.
_RATING_SEARCH_FIELDS = ("company_name", "sector", "grade", "category")


def _calc_status(doc: dict) -> str:
    """Same bucketing as app/dashboard/router.py's _status_counts: analysis_status
    running/failed take priority over the status field."""
    if doc.get("analysis_status") == "running":
        return "running"
    if doc.get("analysis_status") == "failed":
        return "failed"
    if doc.get("status") == "sent":
        return "sent"
    if doc.get("status") == "report_generated":
        return "report_generated"
    return "new"


def _calc_date(doc: dict) -> str:
    created = doc.get("created_at")
    if isinstance(created, datetime):
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return created.astimezone(timezone.utc).date().isoformat()
    return ""


def _calc_item(doc: dict) -> dict:
    final = doc.get("final") or {}
    score = final.get("composite_score")
    return {
        "source": "calculator",
        "id": str(doc["_id"]),
        "company": doc.get("company_name"),
        "sector": final.get("sector"),
        "rating": round(score, 1) if score is not None else None,
        "grade": final.get("composite_score_performance"),
        "category": final.get("composite_score_performance_label"),
        "date": _calc_date(doc),
        "status": _calc_status(doc),
        "contact": {"name": doc.get("name"), "email": doc.get("email")},
        "report_year": doc.get("report_year"),
    }


def rating_item(doc: dict) -> dict:
    """Shape used both by the combined list and GET /api/admin/ratings/{s_no}."""
    rating = doc.get("esg_rating")
    return {
        "source": "rating",
        "id": str(doc.get("s_no")),
        "company": doc.get("company_name"),
        "sector": doc.get("sector"),
        "rating": float(rating) if rating is not None else None,
        "grade": doc.get("grade"),
        "category": doc.get("category"),
        "date": doc.get("date_of_rating") or "",
        "status": "rated",
        "contact": None,
        "report_year": None,
    }


# What _calc_item, _calc_status and rating_item read, and nothing else. A submission
# carries its page scores, KPI tables, written text and a copy of the original report -- up
# to ~550 KB -- and this list read every one of them whole, twice per page load, on a
# database throttled to ~100 KB/s (user, 2026-10-08).
_CALC_FIELDS = {"company_name": 1, "name": 1, "email": 1, "report_year": 1, "created_at": 1,
                "status": 1, "analysis_status": 1, "final.sector": 1, "final.composite_score": 1,
                "final.composite_score_performance": 1, "final.composite_score_performance_label": 1}
_RATING_FIELDS = {"s_no": 1, "company_name": 1, "sector": 1, "esg_rating": 1, "grade": 1,
                  "category": 1, "date_of_rating": 1}


def _calc_query(search: str) -> dict:
    if not search:
        return {}
    rx = {"$regex": re.escape(search), "$options": "i"}
    return {
        "$or": [
            {"company_name": rx},
            {"name": rx},
            {"email": rx},
            {"final.sector": rx},
            {"final.composite_score_performance": rx},
            {"final.composite_score_performance_label": rx},
        ]
    }


def _rating_query(search: str) -> dict:
    if not search:
        return {}
    rx = {"$regex": re.escape(search), "$options": "i"}
    return {"$or": [{f: rx} for f in _RATING_SEARCH_FIELDS]}


def _newest_calc(query: dict, n: int) -> list[dict]:
    """At least the n submissions that come first in the list.

    The list orders a day's submissions by id, the database orders them by time, and the
    two disagree for submissions imported with their original dates. So the whole of the
    last day reached is read as well: then no submission that belongs before the cut is
    left out, and _sort_key puts them in the list's own order."""
    col = esg_submissions_collection()
    docs = list(col.find(query, _CALC_FIELDS).sort([("created_at", -1), ("_id", -1)]).limit(n))
    last = docs[-1].get("created_at") if len(docs) == n else None
    if isinstance(last, datetime):
        day = datetime(last.year, last.month, last.day, tzinfo=last.tzinfo)
        same_day = {"created_at": {"$gte": day, "$lt": day + timedelta(days=1)},
                    "_id": {"$nin": [d["_id"] for d in docs]}}
        docs += list(col.find({"$and": [query, same_day]} if query else same_day, _CALC_FIELDS))
    return docs


def _newest_ratings(query: dict, n: int) -> list[dict]:
    """The n rated companies that come first in the list: newest rating date, then highest
    s_no -- the order _sort_key gives them, done by the database."""
    return list(ratings_collection().find(query, _RATING_FIELDS)
                .sort([("date_of_rating", -1), ("s_no", -1)]).limit(n))


def _sort_key(entry: tuple[str, dict]):
    date, item = entry
    if item["source"] == "calculator":
        # str(ObjectId) hex sorts chronologically (timestamp-prefixed); reverse=True
        # below makes a newer _id sort first among same-date calculator rows.
        return (date, 1, item["id"])
    return (date, 0, int(item["id"]))


@router.get("/combined")
def combined_list(search: str = "", page: int = 1, source: str = "all", admin: str = Depends(require_admin)):
    """One page of the merged list, paged in the database: the totals are counted there,
    and only the first page*PAGE_SIZE records of each kind are read -- the page can only
    hold records from those -- then merged and cut to the page."""
    if source not in VALID_SOURCES:
        raise HTTPException(422, f"Invalid source '{source}'; expected one of: all, calculator, rating.")

    search = search.strip()
    page = max(page, 1)
    needed = page * PAGE_SIZE

    items: list[dict] = []
    total = 0
    if source in ("all", "calculator"):
        query = _calc_query(search)
        total += esg_submissions_collection().count_documents(query)
        items.extend(_calc_item(d) for d in _newest_calc(query, needed))
    if source in ("all", "rating"):
        query = _rating_query(search)
        total += ratings_collection().count_documents(query)
        items.extend(rating_item(d) for d in _newest_ratings(query, needed))

    items.sort(key=lambda item: _sort_key((item["date"], item)), reverse=True)

    pages = math.ceil(total / PAGE_SIZE) if total else 0
    start = (page - 1) * PAGE_SIZE
    page_items = items[start:start + PAGE_SIZE]

    return {"items": page_items, "total": total, "page": page, "pages": pages}
