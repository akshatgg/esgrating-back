import logging, threading
from datetime import datetime, timezone
from typing import Callable
from bson import ObjectId
from app.core.db import get_db

log = logging.getLogger(__name__)
RUN_INLINE = False
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
    claimed = col.find_one_and_update(
        {"_id": doc_id, "analysis_status": {"$ne": "running"}},
        {"$set": {"analysis_status": "running", "analysis_error": None, "analysis_started_at": datetime.now(timezone.utc)}},
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
