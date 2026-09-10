# app/esg/combined.py
# Merges the ESG calculator submissions (esg_submissions) with the ESG Rating List
# (esg_ratings, ~461 companies) into one uniform, paginated feed for the admin
# "ESG Submissions" page -- see app/esg/router_admin.py (submission shape/status) and
# app/ratings/store.py + app/ratings/router.py (rating shape/search).
import math
import re
from datetime import datetime, timezone

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


def _calc_docs(search: str) -> list[dict]:
    query = {}
    if search:
        rx = {"$regex": re.escape(search), "$options": "i"}
        query = {
            "$or": [
                {"company_name": rx},
                {"name": rx},
                {"email": rx},
                {"final.sector": rx},
                {"final.composite_score_performance": rx},
                {"final.composite_score_performance_label": rx},
            ]
        }
    return list(esg_submissions_collection().find(query))


def _rating_docs(search: str) -> list[dict]:
    # No-search path: one plain find(), no separate count + re-fetch.
    if not search:
        return list(ratings_collection().find({}))
    pattern = re.compile(re.escape(search), re.IGNORECASE)
    docs = list(ratings_collection().find({}))
    return [d for d in docs if any(pattern.search(str(d.get(f, ""))) for f in _RATING_SEARCH_FIELDS)]


def _sort_key(entry: tuple[str, dict]):
    date, item = entry
    if item["source"] == "calculator":
        # str(ObjectId) hex sorts chronologically (timestamp-prefixed); reverse=True
        # below makes a newer _id sort first among same-date calculator rows.
        return (date, 1, item["id"])
    return (date, 0, int(item["id"]))


@router.get("/combined")
def combined_list(search: str = "", page: int = 1, source: str = "all", admin: str = Depends(require_admin)):
    if source not in VALID_SOURCES:
        raise HTTPException(422, f"Invalid source '{source}'; expected one of: all, calculator, rating.")

    search = search.strip()
    page = max(page, 1)

    items: list[dict] = []
    if source in ("all", "calculator"):
        items.extend(_calc_item(d) for d in _calc_docs(search))
    if source in ("all", "rating"):
        items.extend(rating_item(d) for d in _rating_docs(search))

    items.sort(key=lambda item: _sort_key((item["date"], item)), reverse=True)

    total = len(items)
    pages = math.ceil(total / PAGE_SIZE) if total else 0
    start = (page - 1) * PAGE_SIZE
    page_items = items[start:start + PAGE_SIZE]

    return {"items": page_items, "total": total, "page": page, "pages": pages}
