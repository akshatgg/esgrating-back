# app/ratings/store.py
# Port of the ESG_RATING_2025_CUSTOM table (dashboard/index.php, dashboard/import.php --
# see docs/analysis/esg.md B2-B3). Collection `esg_ratings`, unique index on `s_no`.
# Search is a straight port of the SQL `LIKE '%search%'` OR-across-6-columns query: a
# case-insensitive substring match, with esg_rating compared as its string form.
import re

from app.core.db import get_db

FIELDS = ("company_name", "sector", "esg_rating", "date_of_rating", "grade", "category")


def ratings_collection():
    return get_db()["esg_ratings"]


def ensure_indexes() -> None:
    ratings_collection().create_index("s_no", unique=True)


def list_public() -> list[dict]:
    return list(ratings_collection().find({}, {"_id": 0}).sort("s_no", -1))


def _matches(doc: dict, pattern: re.Pattern) -> bool:
    return any(pattern.search(str(doc.get(f, ""))) for f in FIELDS)


def search_page(q: str, skip: int, limit: int) -> tuple[list[dict], int]:
    """One pass: return (page of items, total matching). Empty search skips
    in-Python filtering entirely and does the count + page directly in Mongo."""
    q = (q or "").strip()
    if not q:
        total = ratings_collection().count_documents({})
        items = list(
            ratings_collection().find({}, {"_id": 0}).sort("s_no", -1).skip(skip).limit(limit)
        )
        return items, total

    pattern = re.compile(re.escape(q), re.IGNORECASE)
    docs = list(ratings_collection().find({}, {"_id": 0}).sort("s_no", -1))
    matched = [d for d in docs if _matches(d, pattern)]
    return matched[skip:skip + limit], len(matched)


def next_s_no() -> int:
    last = ratings_collection().find_one(sort=[("s_no", -1)])
    return (last["s_no"] + 1) if last else 1


def insert_rating(fields: dict) -> dict:
    doc = dict(fields)
    doc["s_no"] = next_s_no()
    ratings_collection().insert_one(doc)
    doc.pop("_id", None)
    return doc


def get_rating(s_no: int) -> dict | None:
    return ratings_collection().find_one({"s_no": s_no}, {"_id": 0})


def update_rating(s_no: int, fields: dict) -> bool:
    result = ratings_collection().update_one({"s_no": s_no}, {"$set": dict(fields)})
    return result.matched_count > 0


def delete_rating(s_no: int) -> bool:
    result = ratings_collection().delete_one({"s_no": s_no})
    return result.deleted_count > 0
