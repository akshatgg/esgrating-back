# app/reports/router.py -- editable ESG/BFSI reports
# (docs/specs/2026-09-10-editable-reports-design.md). Every route is admin-only.
import re
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from app.auth.deps import require_admin
from app.bfsi import store as bfsi_store
from app.core.uploads import read_limited
from app.esg.submissions import esg_submissions_collection, serialize_doc
from app.reports import editing
from app.reports.logo import MAX_LOGO_BYTES, delete_logo_file, logo_media_type, logo_path, save_logo

router = APIRouter(prefix="/api/admin", tags=["admin-reports"])

MAX_EDITS_BODY_BYTES = 512 * 1024
NOT_READY = "Run the analysis before editing the report."
RUNNING = "The analysis is running. Wait for it to finish before editing the report."
_ID_RE = re.compile(r"^[0-9a-f]{24}$", re.IGNORECASE)


def _collection(kind: str):
    if kind == "esg":
        return esg_submissions_collection()
    if kind == "bfsi":
        return bfsi_store.submissions_collection()
    raise HTTPException(404, "Not found")


def _load(kind: str, id_: str) -> tuple:
    col = _collection(kind)
    if not _ID_RE.match(id_):
        raise HTTPException(404, "Not found")
    doc = col.find_one({"_id": ObjectId(id_)})
    if not doc:
        raise HTTPException(404, "Not found")
    if not editing.has_report(kind, doc):
        raise HTTPException(409, NOT_READY)
    return col, doc


def _reload(col, doc: dict) -> dict:
    return col.find_one({"_id": doc["_id"]})


def _not_running(doc: dict) -> None:
    if doc.get("analysis_status") == "running":
        raise HTTPException(409, RUNNING)


def _check_size(request: Request) -> None:
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_EDITS_BODY_BYTES:
        raise HTTPException(413, "The edits are too large.")


def _apply(col, doc: dict, update: dict) -> None:
    """Write unless an analysis run has claimed the submission since we read it."""
    update = {op: fields for op, fields in update.items() if fields}
    result = col.update_one({"_id": doc["_id"], "analysis_status": {"$ne": "running"}}, update)
    if result.matched_count == 0:
        raise HTTPException(409, RUNNING)


def _logo_name(doc: dict) -> str | None:
    return (doc.get("report_edits") or {}).get("logo")


def _logo_url(kind: str, doc: dict) -> str | None:
    return f"/api/admin/{kind}/submissions/{doc['_id']}/report/logo" if _logo_name(doc) else None


def _report(kind: str, doc: dict) -> dict:
    ctx = editing.load_context(kind, doc)
    edits = editing.stored_edits(doc)
    result = editing.compute(kind, doc, ctx, edits, _logo_url(kind, doc))
    original = editing.compute(kind, doc, ctx, editing.empty_edits(), None)["effective"]
    return serialize_doc({
        "effective": result["effective"],
        "original": original,
        "edits": edits,
        "pages": result["pages"],
        "edited": editing.is_edited(doc),
        "heading_keys": editing.HEADING_KEYS[kind],
        "field_keys": editing.FIELD_KEYS[kind],
    })


@router.get("/{kind}/submissions/{id}/report")
def get_report(kind: str, id: str, admin: str = Depends(require_admin)):
    _col, doc = _load(kind, id)
    return _report(kind, doc)


@router.post("/{kind}/submissions/{id}/report/preview")
def preview_report(kind: str, id: str, request: Request, payload: dict = Body(...),
                   admin: str = Depends(require_admin)):
    """Recompute with the given edits. Never saves."""
    _check_size(request)
    _col, doc = _load(kind, id)
    ctx = editing.load_context(kind, doc)
    edits = editing.normalize_edits(kind, payload, ctx)
    result = editing.compute(kind, doc, ctx, edits, _logo_url(kind, doc))
    return serialize_doc({"effective": result["effective"], "pages": result["pages"]})


@router.put("/{kind}/submissions/{id}/report/edits")
def save_edits(kind: str, id: str, request: Request, payload: dict = Body(...),
               admin: str = Depends(require_admin)):
    _check_size(request)
    col, doc = _load(kind, id)
    _not_running(doc)
    ctx = editing.load_context(kind, doc)
    edits = editing.normalize_edits(kind, payload, ctx)
    logo = _logo_name(doc)
    record = {**edits, "logo": logo, "updated_at": datetime.now(timezone.utc), "updated_by": admin}

    if editing.edits_have_content(edits):
        stored = editing.compute(kind, doc, ctx, edits)["stored"]
        update = {"$set": {**stored, "report_edits": record}}
        if not doc.get("report_original"):
            update["$set"]["report_original"] = ctx["base"]  # snapshot on first save
    else:
        # Nothing edited (besides maybe the logo): same as a reset of the report fields.
        update = editing.restore_update(kind, ctx["base"])
        update["$unset"]["report_original"] = ""
        if logo:
            update["$set"]["report_edits"] = record
        else:
            update["$unset"]["report_edits"] = ""
    _apply(col, doc, update)
    return _report(kind, _reload(col, doc))


@router.delete("/{kind}/submissions/{id}/report/edits")
def reset_edits(kind: str, id: str, admin: str = Depends(require_admin)):
    """Restore the AI version from report_original; drop the edits and the custom logo."""
    col, doc = _load(kind, id)
    _not_running(doc)
    update = {"$set": {}, "$unset": {"report_edits": "", "report_original": ""}}
    if doc.get("report_original"):
        restore = editing.restore_update(kind, doc["report_original"])
        update["$set"].update(restore["$set"])
        update["$unset"].update(restore["$unset"])
    _apply(col, doc, update)
    delete_logo_file(_logo_name(doc))
    return _report(kind, _reload(col, doc))


@router.post("/{kind}/submissions/{id}/report/logo")
async def upload_logo(kind: str, id: str, logo: UploadFile = File(...), admin: str = Depends(require_admin)):
    col, doc = _load(kind, id)
    data = await read_limited(logo, MAX_LOGO_BYTES, "The logo must be 1 MB or smaller.")
    name = save_logo(data)  # magic-byte checked: PNG / JPEG / WebP only
    col.update_one({"_id": doc["_id"]}, {"$set": {
        "report_edits.logo": name,
        "report_edits.updated_at": datetime.now(timezone.utc),
        "report_edits.updated_by": admin,
    }})
    old = _logo_name(doc)
    if old and old != name:
        delete_logo_file(old)
    return _report(kind, _reload(col, doc))


@router.get("/{kind}/submissions/{id}/report/logo")
def get_logo(kind: str, id: str, admin: str = Depends(require_admin)):
    _col, doc = _load(kind, id)
    name = _logo_name(doc)
    if not name:
        raise HTTPException(404, "Not found")
    try:
        path = logo_path(name)
    except FileNotFoundError:
        raise HTTPException(404, "Not found")
    return FileResponse(path, media_type=logo_media_type(name), headers={
        "Cache-Control": "private, no-cache",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'",
    })


@router.delete("/{kind}/submissions/{id}/report/logo")
def remove_logo(kind: str, id: str, admin: str = Depends(require_admin)):
    """Back to the default logo. (Not in the spec's table: the "use default logo" control
    needs it, and PUT deliberately never touches the logo.)"""
    col, doc = _load(kind, id)
    name = _logo_name(doc)
    if name:
        if editing.edits_have_content(doc.get("report_edits")):
            col.update_one({"_id": doc["_id"]}, {"$unset": {"report_edits.logo": ""}})
        else:
            col.update_one({"_id": doc["_id"]}, {"$unset": {"report_edits": ""}})
        delete_logo_file(name)
    return _report(kind, _reload(col, doc))
