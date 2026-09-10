# app/ratings/router.py
# Public + admin routes for the ESG ratings table. Port of dashboard/esg_rating_data.php
# (public feed), dashboard/index.php (admin CRUD/search) and dashboard/import.php
# (admin CSV import) -- docs/analysis/esg.md B2-B3.
import csv
import io
import math
import re
from datetime import datetime

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.exceptions import HTTPException
from pydantic import BaseModel

from app.auth.deps import require_admin
from app.core.errors import UserError
from app.core.uploads import read_limited
from app.esg.combined import rating_item
from app.ratings import store

public_router = APIRouter(prefix="/api", tags=["ratings"])
admin_router = APIRouter(prefix="/api/admin/ratings", tags=["admin-ratings"])

# dashboard/import.php had no size cap of its own; this only bounds memory use.
MAX_IMPORT_BYTES = 5 * 1024 * 1024

PAGE_SIZE = 50

# dashboard/import.php's exact 6-column header.
IMPORT_HEADER = ["Company_Name", "Sector", "ESG_Rating", "Date_of_Rating", "Grade", "Category"]

_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y")


def _normalize_date(value: str) -> str:
    """format_date_to_mysql: accept a handful of common date shapes and store YYYY-MM-DD;
    anything unparseable is kept as-is (trimmed) rather than rejecting the row."""
    v = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return v


def _php_float(value: str) -> float:
    """Mimic PHP's (float) cast used for ESG_Rating on import: longest leading numeric
    prefix, or 0.0 if none."""
    m = re.match(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?", value.strip())
    if not m or not m.group(0):
        return 0.0
    try:
        return float(m.group(0))
    except ValueError:
        return 0.0


def _format_public(doc: dict) -> dict:
    out = dict(doc)
    if out.get("esg_rating") is not None:
        out["esg_rating"] = float(out["esg_rating"])
    try:
        out["date_of_rating"] = datetime.strptime(out["date_of_rating"], "%Y-%m-%d").strftime("%d-%m-%Y")
    except (KeyError, ValueError, TypeError):
        pass
    return out


@public_router.get("/ratings")
def list_ratings():
    return [_format_public(d) for d in store.list_public()]


class RatingIn(BaseModel):
    company_name: str
    sector: str
    esg_rating: float
    date_of_rating: str
    grade: str
    category: str


@admin_router.get("")
def admin_list(search: str = "", page: int = 1, admin: str = Depends(require_admin)):
    page = max(page, 1)
    items, total = store.search_page(search, (page - 1) * PAGE_SIZE, PAGE_SIZE)
    pages = math.ceil(total / PAGE_SIZE) if total else 0
    return {"items": items, "total": total, "page": page, "pages": pages}


def _rating_fields(payload: RatingIn) -> dict:
    # Same date normalization as the CSV import, so the public list formats every row alike.
    fields = payload.model_dump()
    fields["date_of_rating"] = _normalize_date(fields["date_of_rating"])
    return fields


@admin_router.post("", status_code=201)
def admin_create(payload: RatingIn, admin: str = Depends(require_admin)):
    return store.insert_rating(_rating_fields(payload))


@admin_router.get("/{s_no}")
def admin_get(s_no: int, admin: str = Depends(require_admin)):
    doc = store.get_rating(s_no)
    if not doc:
        raise HTTPException(404, "Not found")
    return rating_item(doc)


@admin_router.put("/{s_no}")
def admin_update(s_no: int, payload: RatingIn, admin: str = Depends(require_admin)):
    if not store.update_rating(s_no, _rating_fields(payload)):
        raise HTTPException(404, "Not found")
    return {"ok": True}


@admin_router.delete("/{s_no}")
def admin_delete(s_no: int, admin: str = Depends(require_admin)):
    if not store.delete_rating(s_no):
        raise HTTPException(404, "Not found")
    return {"ok": True}


@admin_router.post("/import")
async def import_csv(file: UploadFile = File(...), admin: str = Depends(require_admin)):
    data = await read_limited(file, MAX_IMPORT_BYTES, "The uploaded file is too large.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise UserError("CSV header row does not match the expected format.")

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise UserError("CSV header row does not match the expected format.")

    if [h.strip() for h in header] != IMPORT_HEADER:
        raise UserError("CSV header row does not match the expected format.")

    inserted, skipped = 0, 0
    docs = []
    for row in reader:
        if len(row) < 6:
            skipped += 1
            continue

        company_name, sector, esg_rating, date_of_rating, grade, category = row[:6]
        company_name = company_name.strip()
        if not company_name:
            skipped += 1
            continue

        docs.append({
            "company_name": company_name,
            "sector": sector.strip(),
            "esg_rating": _php_float(esg_rating),
            "date_of_rating": _normalize_date(date_of_rating),
            "grade": grade.strip(),
            "category": category.strip(),
        })
        inserted += 1

    if docs:
        next_no = store.next_s_no()
        for i, doc in enumerate(docs):
            doc["s_no"] = next_no + i
        store.ratings_collection().insert_many(docs)

    return {"message": f"Import complete: {inserted} row(s) inserted, {skipped} row(s) skipped."}
