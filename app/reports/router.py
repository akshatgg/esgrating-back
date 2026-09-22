# app/reports/router.py -- editable ESG/BFSI reports
# (docs/specs/2026-09-10-editable-reports-design.md). Every route is admin-only.
import json
import re
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response

from app.auth.deps import require_admin
from app.bfsi import store as bfsi_store
from app.core.uploads import read_limited
from app.esg.submissions import esg_submissions_collection, serialize_doc
from app.reports import editing, summary
from app.reports.logo import MAX_LOGO_BYTES, delete_logo_file, logo_media_type, logo_path, save_logo

router = APIRouter(prefix="/api/admin", tags=["admin-reports"])

MAX_EDITS_BODY_BYTES = 512 * 1024
NOT_READY = "Run the analysis before editing the report."
RUNNING = "The analysis is running. Wait for it to finish before editing the report."
TOO_LARGE = "The edits are too large."
_ID_RE = re.compile(r"^[0-9a-f]{24}$", re.IGNORECASE)

# The report_edits sub-fields a PUT owns. It sets (or unsets) exactly these, never the
# whole report_edits object, so a logo uploaded while the PUT was in flight survives.
_CONTENT_KEYS = ("headings", "fields", "page_scores", "kpi_scores", "pillar_overrides")


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


async def edits_body(request: Request) -> dict:
    """The JSON edits body, read with a hard byte cap. Content-Length is only a fast
    path: the stream itself is counted, so a chunked (length-less) body can't get past
    the limit either."""
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_EDITS_BODY_BYTES:
        raise HTTPException(413, TOO_LARGE)
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_EDITS_BODY_BYTES:
            raise HTTPException(413, TOO_LARGE)
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(422, "The edits must be valid JSON.")
    if not isinstance(payload, dict):
        raise HTTPException(422, "The edits must be a JSON object.")
    return payload


def _apply(col, doc: dict, update: dict) -> None:
    """Write unless an analysis run has claimed the submission since we read it."""
    update = {op: fields for op, fields in update.items() if fields}
    if not update:
        return
    result = col.update_one({"_id": doc["_id"], "analysis_status": {"$ne": "running"}}, update)
    if result.matched_count == 0:
        raise HTTPException(409, RUNNING)


def _drop_empty_edits(col, doc: dict) -> None:
    """Remove a report_edits left with neither content nor a logo (only bookkeeping).
    Conditional in the filter, so a concurrent logo upload or content save is kept."""
    col.update_one(
        {"_id": doc["_id"],
         **{f"report_edits.{f}": {"$in": [None]} for f in LOGO_SLOTS.values()},
         **{f"report_edits.{k}": {"$exists": False} for k in _CONTENT_KEYS}},
        {"$unset": {"report_edits": ""}},
    )


# The sheet's two logos: the report's own, and the optional one in the top-right
# corner where the SEBI line sits (user, 2026-09-20). One file each, same storage.
LOGO_SLOTS = {"main": "logo", "corner": "corner_logo"}


def _logo_field(slot: str) -> str:
    field = LOGO_SLOTS.get(slot)
    if field is None:
        raise HTTPException(422, f"Unknown logo slot {slot!r}.")
    return field


def _logo_name(doc: dict, slot: str = "main") -> str | None:
    return (doc.get("report_edits") or {}).get(_logo_field(slot))


def _logo_url(kind: str, doc: dict, slot: str = "main") -> str | None:
    if not _logo_name(doc, slot):
        return None
    base = f"/api/admin/{kind}/submissions/{doc['_id']}/report/logo"
    return base if slot == "main" else f"{base}?slot={slot}"


def _report(kind: str, doc: dict) -> dict:
    written = summary.fresh_narrative(kind, doc)
    ctx = editing.load_context(kind, doc)
    edits = editing.stored_edits(doc)
    result = editing.compute(kind, doc, ctx, edits, _logo_url(kind, doc),
                             _logo_url(kind, doc, "corner"))
    original = editing.compute(kind, doc, ctx, editing.empty_edits(), None, None)["effective"]
    return serialize_doc({
        "effective": result["effective"],
        "original": original,
        "edits": edits,
        "pages": result["pages"],
        "pages_editable": ctx["pages_editable"],
        "kpis": result["kpis"],
        "kpis_editable": ctx["kpis_editable"],
        "edited": editing.is_edited(doc),
        "heading_keys": editing.HEADING_KEYS[kind],
        "field_keys": editing.FIELD_KEYS[kind],
        # The written rating, and only while it still describes these scores: an edited
        # pillar score makes the stored text out of date, and the report must not carry
        # prose about numbers it no longer shows. `narrative_stale` tells the page to
        # offer regeneration rather than silently showing nothing.
        "narrative": summary.with_edits(written, doc) if written else None,
        "narrative_stale": written is None and bool((doc.get("summary_ai") or {}).get("text")),
    })


@router.get("/{kind}/submissions/{id}/report")
def get_report(kind: str, id: str, admin: str = Depends(require_admin)):
    _col, doc = _load(kind, id)
    return _report(kind, doc)


@router.post("/{kind}/submissions/{id}/report/preview")
def preview_report(kind: str, id: str, payload: dict = Depends(edits_body),
                   admin: str = Depends(require_admin)):
    """Recompute with the given edits. Never saves."""
    _col, doc = _load(kind, id)
    ctx = editing.load_context(kind, doc)
    edits = editing.normalize_edits(kind, payload, ctx)
    result = editing.compute(kind, doc, ctx, edits, _logo_url(kind, doc),
                             _logo_url(kind, doc, "corner"))
    return serialize_doc({
        "effective": result["effective"],
        "pages": result["pages"],
        "pages_editable": ctx["pages_editable"],
        "kpis": result["kpis"],
        "kpis_editable": ctx["kpis_editable"],
    })


@router.put("/{kind}/submissions/{id}/report/edits")
def save_edits(kind: str, id: str, payload: dict = Depends(edits_body),
               admin: str = Depends(require_admin)):
    col, doc = _load(kind, id)
    _not_running(doc)
    ctx = editing.load_context(kind, doc)
    edits = editing.normalize_edits(kind, payload, ctx)
    meta = {"report_edits.updated_at": datetime.now(timezone.utc), "report_edits.updated_by": admin}

    if editing.edits_have_content(edits):
        stored = editing.compute(kind, doc, ctx, edits)["stored"]
        update = {"$set": {**stored, **meta, **{f"report_edits.{k}": edits[k] for k in _CONTENT_KEYS}}}
        if not doc.get("report_original"):
            update["$set"]["report_original"] = ctx["base"]  # snapshot on first save
        _apply(col, doc, update)
    else:
        # Nothing edited (besides maybe the logo): same as a reset of the report fields.
        # Only the content sub-fields go; the logo is left exactly as it is now.
        update = editing.restore_update(kind, ctx["base"])
        update["$unset"]["report_original"] = ""
        update["$unset"].update({f"report_edits.{k}": "" for k in _CONTENT_KEYS})
        _apply(col, doc, update)
        _drop_empty_edits(col, doc)
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
    for slot in LOGO_SLOTS:
        delete_logo_file(_logo_name(doc, slot))
    return _report(kind, _reload(col, doc))


@router.get("/{kind}/submissions/{id}/summary")
def download_summary(kind: str, id: str, admin: str = Depends(require_admin)):
    """The ESG Rating Summary as a Word document (app/reports/summary.py)."""
    _col, doc = _load(kind, id)
    _not_running(doc)
    data = summary.build_summary(kind, doc)
    return Response(data, media_type=summary.DOCX_MIME, headers={
        "Content-Disposition": f'attachment; filename="{summary.filename(kind, doc)}"',
        "Cache-Control": "no-store",
    })


@router.post("/{kind}/submissions/{id}/reports")
def generate_reports(kind: str, id: str, admin: str = Depends(require_admin)):
    """Write the rating for the report as it stands -- including any scores the analyst
    has changed -- so the detailed report, the Word summary and the page-scores export
    all describe the same rating (user, 2026-09-21).

    An analysis produces the scores and the one-pager; this is the step that writes the
    prose, and it is asked for rather than paid for on every submission."""
    col, doc = _load(kind, id)
    _not_running(doc)
    if not summary.available(kind, doc):
        raise HTTPException(409, summary.NOT_AVAILABLE)
    if not summary.ai_configured(kind):
        raise HTTPException(503, "The rating text can't be written: no AI key is configured.")
    summary.narrative(kind, doc, summary.build_facts(kind, doc))  # raises 502 if it fails
    return _report(kind, _reload(col, doc))


@router.post("/{kind}/submissions/{id}/report/logo")
async def upload_logo(kind: str, id: str, logo: UploadFile = File(...), slot: str = "main",
                      admin: str = Depends(require_admin)):
    col, doc = _load(kind, id)
    field = _logo_field(slot)
    _not_running(doc)
    data = await read_limited(logo, MAX_LOGO_BYTES, "The logo must be 1 MB or smaller.")
    name = save_logo(data)  # magic-byte checked: PNG / JPEG / WebP only
    try:
        _apply(col, doc, {"$set": {
            f"report_edits.{field}": name,
            "report_edits.updated_at": datetime.now(timezone.utc),
            "report_edits.updated_by": admin,
        }})
    except HTTPException:
        delete_logo_file(name)  # an analysis claimed the submission meanwhile
        raise
    old = _logo_name(doc, slot)
    if old and old != name:
        delete_logo_file(old)
    return _report(kind, _reload(col, doc))


@router.get("/{kind}/submissions/{id}/report/logo")
def get_logo(kind: str, id: str, slot: str = "main", admin: str = Depends(require_admin)):
    _col, doc = _load(kind, id)
    name = _logo_name(doc, slot)
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
def remove_logo(kind: str, id: str, slot: str = "main", admin: str = Depends(require_admin)):
    """Back to the default logo -- for the corner slot, to no logo at all. (Not in the
    spec's table: the "use default logo" control needs it, and PUT deliberately never
    touches the logo.)"""
    col, doc = _load(kind, id)
    field = _logo_field(slot)
    _not_running(doc)
    name = _logo_name(doc, slot)
    if name:
        _apply(col, doc, {"$unset": {f"report_edits.{field}": ""}})
        _drop_empty_edits(col, doc)
        delete_logo_file(name)
    return _report(kind, _reload(col, doc))
