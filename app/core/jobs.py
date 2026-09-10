import logging, threading
from datetime import datetime, timedelta, timezone
from typing import Callable
from bson import ObjectId
from app.core.db import get_db

log = logging.getLogger(__name__)
RUN_INLINE = False
STALE_AFTER = timedelta(minutes=30)
JOB_COLLECTIONS = ("esg_submissions", "bfsi_submissions")


def _run(collection: str, doc_id: ObjectId, fn: Callable[[], None]) -> None:
    col = get_db()[collection]
    try:
        fn()
        col.update_one({"_id": doc_id}, {"$set": {"analysis_status": "done", "analysis_error": None}})
    except Exception as e:
        log.exception("analysis failed for %s/%s", collection, doc_id)
        col.update_one({"_id": doc_id}, {"$set": {"analysis_status": "failed", "analysis_error": str(e) or e.__class__.__name__}})


def start_job(collection: str, doc_id: ObjectId, fn: Callable[[], None]) -> bool:
    col = get_db()[collection]
    now = datetime.now(timezone.utc)
    # A "running" doc whose worker hung would otherwise answer 409 until a restart, so a
    # run older than STALE_AFTER may be reclaimed.
    claimed = col.find_one_and_update(
        {"_id": doc_id, "$or": [
            {"analysis_status": {"$ne": "running"}},
            {"analysis_started_at": {"$lt": now - STALE_AFTER}},
        ]},
        {"$set": {"analysis_status": "running", "analysis_error": None, "analysis_started_at": now}},
    )
    if claimed is None:
        return False
    if RUN_INLINE:
        _run(collection, doc_id, fn)
    else:
        threading.Thread(target=_run, args=(collection, doc_id, fn), daemon=True).start()
    return True


def reset_interrupted_jobs() -> None:
    for name in JOB_COLLECTIONS:
        get_db()[name].update_many(
            {"analysis_status": "running"},
            {"$set": {"analysis_status": "failed", "analysis_error": "Interrupted by a server restart — run the analysis again."}},
        )
