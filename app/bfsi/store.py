# Port of bfsi-calculator/lib/mongo.php (see docs/analysis/bfsi.md §1d). Same
# collections, fields and semantics as the PHP source. Differences: the db comes from
# get_db() at call time (mirrors app/esg/store.py); ids are 24-hex ObjectId strings
# validated the same way lib/mongo.php validates them (`/^[0-9a-f]{24}$/i`); the file
# cascade in delete_submission unlinks through app.core.uploads.upload_path.
import re
from datetime import datetime, timedelta, timezone

from bson import ObjectId

from app.core.db import get_db
from app.core.uploads import upload_path

_ID_RE = re.compile(r"^[0-9a-f]{24}$", re.IGNORECASE)


def submissions_collection():
    return get_db()["bfsi_submissions"]


def report_collection():
    return get_db()["bfsi_report"]


def hashes_collection():
    return get_db()["bfsi_hashes"]


def ocr_cache_collection():
    return get_db()["bfsi_ocr_cache"]


def ensure_indexes() -> None:
    """The exact indexes from bfsi.md §1d (mongo.php:254, bfsi_ensure_indexes)."""
    submissions_collection().create_index([("submit_ip", 1), ("created_at", -1)])
    submissions_collection().create_index([("borrower_name", 1)])
    submissions_collection().create_index([("file_sha256", 1)])
    report_collection().create_index([("submission_id", 1)])
    hashes_collection().create_index([("hash", 1)])
    hashes_collection().create_index([("submission_id", 1)])
    ocr_cache_collection().create_index([("file_sha256", 1)])


def _valid_oid(id_: str) -> ObjectId | None:
    if not isinstance(id_, str) or not _ID_RE.match(id_):
        return None
    return ObjectId(id_)


def insert_submission(doc: dict) -> str:
    doc = dict(doc)
    doc.setdefault("created_at", datetime.now(timezone.utc))
    result = submissions_collection().insert_one(doc)
    return str(result.inserted_id)


def _search_query(q: str) -> dict:
    q = (q or "").strip()
    if not q:
        return {}
    return {"borrower_name": {"$regex": re.escape(q), "$options": "i"}}


def list_submissions(q: str, skip: int, limit: int) -> list[dict]:
    return list(
        submissions_collection().find(_search_query(q)).sort("_id", -1).skip(skip).limit(limit)
    )


def count_submissions(q: str) -> int:
    return submissions_collection().count_documents(_search_query(q))


def get_submission(id_: str) -> dict | None:
    oid = _valid_oid(id_)
    if oid is None:
        return None
    return submissions_collection().find_one({"_id": oid})


def previous_scored(cin: str, before_id: ObjectId) -> dict | None:
    return submissions_collection().find_one(
        {"cin_gstin": cin, "_id": {"$lt": before_id}, "ai_analysis": {"$exists": True}},
        sort=[("_id", -1)],
    )


def count_recent_by_ip(ip: str, minutes: int) -> int:
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return submissions_collection().count_documents({"submit_ip": ip, "created_at": {"$gt": since}})


def delete_submission(id_: str) -> dict:
    """Cascade in the PHP order (mongo.php:207): delete the submission first (abort if
    not deleted), then the report/hashes rows, then bfsi_ocr_cache only when no
    *remaining* submission shares file_sha256, then the uploaded file."""
    result = {"deleted": False, "report": 0, "hashes": 0, "ocr": 0, "file": False}
    oid = _valid_oid(id_)
    if oid is None:
        return result

    doc = submissions_collection().find_one({"_id": oid})
    if not doc:
        return result

    deleted = submissions_collection().delete_one({"_id": oid})
    if deleted.deleted_count == 0:
        return result
    result["deleted"] = True

    result["report"] = report_collection().delete_many({"submission_id": oid}).deleted_count
    result["hashes"] = hashes_collection().delete_many({"submission_id": oid}).deleted_count

    file_sha = doc.get("file_sha256")
    if file_sha:
        remaining = submissions_collection().count_documents({"file_sha256": file_sha})
        if remaining == 0:
            result["ocr"] = ocr_cache_collection().delete_many({"file_sha256": file_sha}).deleted_count

    file_path = doc.get("file_path")
    if file_path:
        try:
            upload_path("bfsi", file_path).unlink()
            result["file"] = True
        except FileNotFoundError:
            pass

    return result


def get_llm_response(text_sha: str) -> dict | None:
    doc = hashes_collection().find_one({"hash": text_sha}, sort=[("_id", -1)])
    return doc["llm_response"] if doc else None


def store_llm_response(sub_id: ObjectId, filename: str, sha: str, ai: dict) -> None:
    hashes_collection().insert_one({
        "submission_id": sub_id,
        "filename": filename,
        "hash": sha,
        "llm_response": ai,
        "created_at": datetime.now(timezone.utc),
    })


def report_insert(sub_id: ObjectId, filename: str, reasons, overall: float) -> None:
    report_collection().insert_one({
        "submission_id": sub_id,
        "filename": filename,
        "analysis": reasons,
        "overall_score": overall,
        "created_at": datetime.now(timezone.utc),
    })
